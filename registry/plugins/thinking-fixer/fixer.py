"""
DeepSeek thinking mode round-trip fixer.

双向 thinking 管理代理：

1. RESP（出站 → DeepSeek）：注入缓存的 thinking 块到 assistant 消息
2. RESP（入站 → Claude）：剥离 thinking 块并缓存
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sys
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

log = logging.getLogger("deepseek-thinking-fixer")

DEFAULT_PORT = 4569
DEFAULT_UPSTREAM = "https://api.deepseek.com/anthropic"
PID_DIR = Path(os.getenv("XDG_RUNTIME_DIR") or Path.home() / ".local" / "share")
PID_FILE = PID_DIR / "deepseek-thinking-fixer.pid"


# ── Thinking Cache ─────────────────────────────────────────
# 结构：thinking_by_msg[conversation_hash][msg_position] = [thinking_block, ...]
# 用 messages 的 hash 标识会话，msg_position 是 assistant 消息在 messages 中的索引
class ThinkingCache:
    def __init__(self, max_conversations: int = 10) -> None:
        self._cache: dict[str, dict[int, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()
        self._max = max_conversations

    def _conv_key(self, messages: list[dict]) -> str:
        """生成会话 key：基于首尾各一条 user 消息签名。"""
        key_parts = []
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            key_parts.append(block.get("text", "")[:50])
                        elif isinstance(block, str):
                            key_parts.append(block[:50])
                elif isinstance(content, str):
                    key_parts.append(content[:50])
                if len(key_parts) >= 3:
                    break
        raw = "|".join(key_parts) if key_parts else "default"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def store_response(
        self,
        request_messages: list[dict],
        thinking_blocks: list[dict[str, Any]],
    ) -> int:
        """为最后一个 assistant 消息位置的 thinking 做缓存。返回缓存的消息位置。"""
        if not thinking_blocks:
            return -1
        # 找到最后一个 assistant 消息的位置
        last_asst_idx = -1
        for i, msg in enumerate(request_messages):
            if msg.get("role") == "assistant":
                last_asst_idx = i
        if last_asst_idx < 0:
            # 如果是第一轮响应（没有 assistant 消息在请求中），位置 = len(messages)
            last_asst_idx = len(request_messages)

        conv = self._conv_key(request_messages)
        with self._lock:
            if conv not in self._cache:
                if len(self._cache) >= self._max:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[conv] = {}
            self._cache[conv][last_asst_idx] = thinking_blocks
            log.info("cached thinking: conv=%s idx=%d count=%d", conv, last_asst_idx, len(thinking_blocks))
        return last_asst_idx

    def inject_into_request(self, messages: list[dict]) -> list[dict]:
        """为 request 中每个缺少 thinking 的 assistant 消息注入缓存。"""
        conv = self._conv_key(messages)
        with self._lock:
            conv_cache = self._cache.get(conv)
            if not conv_cache:
                return messages

        modified = False
        for i, msg in enumerate(messages):
            if msg.get("role") != "assistant":
                continue
            cached = conv_cache.get(i)
            if not cached:
                continue

            content = msg.get("content", [])
            if not isinstance(content, list):
                continue

            # 已经有 thinking 的跳过
            has_thinking = any(isinstance(b, dict) and b.get("type") == "thinking" for b in content)
            if has_thinking:
                continue

            content[:0] = cached
            msg["content"] = content
            modified = True
            log.info("injected thinking: conv=%s idx=%d count=%d", conv, i, len(cached))

        return messages


# ── 全局缓存 ───────────────────────────────────────────────
_cache = ThinkingCache()


# ── Request 处理：注入 thinking ──────────────────────────────
def inject_thinking(body: bytes) -> bytes:
    """在 assistant 消息中注入缓存的 thinking 块。"""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body

    messages = data.get("messages")
    if not isinstance(messages, list):
        return body

    _cache.inject_into_request(messages)
    return json.dumps(data).encode("utf-8")


def _join_text(content: Any) -> str:
    """从 content 数组中提取纯文本字符串。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    pass  # 跳过 tool results
                else:
                    # fallback: 取 text 或 content 字段
                    parts.append(str(block.get("text", block.get("content", ""))))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return str(content)


# ── Response 处理：剥离 + 缓存 thinking ────────────────────
def strip_and_cache(response_body: bytes, request_messages: list[dict] | None = None, is_streaming: bool = False) -> bytes:
    """剥离 response 中的 thinking 块并缓存。"""
    if is_streaming:
        return _process_streaming(response_body)

    try:
        data = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return response_body

    if not isinstance(data, dict):
        return response_body

    content = data.get("content")
    if not isinstance(content, list):
        return response_body

    thinking_blocks = [
        b for b in content
        if isinstance(b, dict) and b.get("type") == "thinking"
    ]
    if not thinking_blocks:
        return response_body

    # 缓存 thinking（按消息索引）
    if request_messages is not None:
        _cache.store_response(request_messages, thinking_blocks)

    # 剥离
    cleaned = [
        b for b in content
        if not (isinstance(b, dict) and b.get("type") == "thinking")
    ]
    if not cleaned:
        cleaned = [{"type": "text", "text": ""}]

    data["content"] = cleaned
    result = json.dumps(data).encode("utf-8")
    log.info("stripped+cached %d thinking block(s)", len(thinking_blocks))
    return result


def _process_streaming(raw: bytes) -> bytes:
    """处理 SSE 流式响应中的 thinking 块。"""
    lines = raw.split(b"\n")
    result_lines: list[bytes] = []
    # 收集流式 thinking 用于缓存（SSE 流中无法完整缓存，跳过）
    for line in lines:
        if line.startswith(b"data: "):
            try:
                payload = json.loads(line[6:])
                if isinstance(payload, dict):
                    delta = payload.get("delta", {})
                    if delta.get("type") == "thinking":
                        continue  # 丢弃 thinking delta
            except json.JSONDecodeError:
                pass
        result_lines.append(line)
    return b"\n".join(result_lines)


# ── HTTP Handler ──────────────────────────────────────────
class FixerHandler(BaseHTTPRequestHandler):
    upstream: str = DEFAULT_UPSTREAM

    def do_GET(self) -> None:
        if self.path in ("/health", "/"):
            self._health()
            return
        self._forward(b"")

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        self._forward(body)

    def _health(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "ok",
            "plugin": "deepseek-thinking-fixer",
            "upstream": self.upstream,
        }).encode("utf-8"))

    def _forward(self, body: bytes) -> None:
        # 1) 解析请求消息（用于 thinking 缓存）
        request_messages: list[dict] | None = None
        try:
            parsed = json.loads(body)
            msgs = parsed.get("messages")
            if isinstance(msgs, list):
                request_messages = msgs
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        # 2) 请求：注入 thinking
        fixed_body = inject_thinking(body)

        # 3) 转发到 DeepSeek
        upstream_url = self.upstream.rstrip("/") + self.path

        req_headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length")
        }
        req = urllib.request.Request(
            upstream_url,
            data=fixed_body,
            headers=req_headers,
            method=self.command,
        )
        req.add_header("Content-Length", str(len(fixed_body)))

        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = resp.read()
                is_streaming = resp.headers.get("Content-Type", "").startswith(
                    "text/event-stream"
                )
                fixed_resp = strip_and_cache(raw, request_messages=request_messages, is_streaming=is_streaming)

                self.send_response(resp.status)
                for h in ("Content-Type", "x-request-id", "request-id"):
                    val = resp.headers.get(h)
                    if val:
                        self.send_header(h, val)
                self.end_headers()
                self.wfile.write(fixed_resp)

        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type",
                              e.headers.get("Content-Type", "application/json"))
            self.end_headers()
            self.wfile.write(e.read())
        except urllib.error.URLError as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "type": "error",
                "error": {"type": "proxy_error", "message": str(e.reason)},
            }).encode("utf-8"))


# ── Server lifecycle ──────────────────────────────────────
class FixerServer:
    def __init__(self, port: int = DEFAULT_PORT, upstream: str = DEFAULT_UPSTREAM,
                 quiet: bool = False) -> None:
        self.port = port
        self.upstream = upstream
        self.quiet = quiet
        self._server: HTTPServer | None = None

    @property
    def is_running(self) -> bool:
        if not PID_FILE.exists():
            return False
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False

    @property
    def pid(self) -> int | None:
        if not PID_FILE.exists():
            return None
        try:
            return int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            return None

    def start(self, daemon: bool = True) -> int:
        if self.is_running:
            log.error("already running (pid %d)", self.pid)
            return 1

        if daemon:
            pid = os.fork()
            if pid > 0:
                PID_DIR.mkdir(parents=True, exist_ok=True)
                PID_FILE.write_text(str(pid))
                print(f"deepseek-thinking-fixer started (pid {pid}, port {self.port})")
                return 0
            os.setsid()

        FixerHandler.upstream = self.upstream
        try:
            self._server = HTTPServer(("127.0.0.1", self.port), FixerHandler)
            if not self.quiet:
                print(f"[thinking-fixer] listening on http://127.0.0.1:{self.port}")
                print(f"[thinking-fixer] forwarding to {self.upstream}")
            self._server.serve_forever()
        except OSError as e:
            log.error("failed to start: %s", e)
            return 1
        return 0

    def stop(self) -> int:
        pid = self.pid
        if not pid:
            print("deepseek-thinking-fixer is not running")
            return 1
        try:
            os.kill(pid, signal.SIGTERM)
            PID_FILE.unlink(missing_ok=True)
            print(f"deepseek-thinking-fixer stopped (pid {pid})")
            return 0
        except ProcessLookupError:
            PID_FILE.unlink(missing_ok=True)
            return 0
        except OSError as e:
            log.error("failed to stop: %s", e)
            return 1

    def status(self) -> int:
        if self.is_running:
            print(f"RUNNING (pid {self.pid}, port {self.port})")
            return 0
        print("STOPPED")
        return 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="deepseek-thinking-fixer")
    parser.add_argument("action", choices=["start", "stop", "status", "restart"])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="local port (default: 4569)")
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM,
                        help="DeepSeek API endpoint")
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    fixer = FixerServer(port=args.port, upstream=args.upstream, quiet=args.quiet)
    daemon = not args.foreground

    if args.action == "start":
        return fixer.start(daemon=daemon)
    elif args.action == "stop":
        return fixer.stop()
    elif args.action == "status":
        return fixer.status()
    elif args.action == "restart":
        fixer.stop()
        return fixer.start(daemon=daemon)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
