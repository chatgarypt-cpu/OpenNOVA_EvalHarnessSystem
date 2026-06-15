"""
deepseek-thinking-fixer — PM Runtime external plugin

修复 DeepSeek V4 thinking mode 多轮对话兼容性。

=== 问题 ===
DeepSeek V4 在 response 中返回 content[type=thinking] 块。
CC Switch 整流器从 response 剥离了 thinking（correct），
但下一轮 request 中没有补回 thinking 块给 DeepSeek（missing），
导致 DeepSeek 报 400: "content[].thinking must be passed back to API"。

=== 修复方案 ===
坐在 CC Switch 代理和 DeepSeek 之间，做双向 thinking 管理：

  Claude → CC Switch (15721) → 本 fixer → DeepSeek API

1. RESPONSE（DeepSeek → Claude 方向）：剥离 thinking 块，缓存
2. REQUEST（Claude → DeepSeek 方向）：在 assistant 消息中补回缓存的 thinking

=== 卸载 ===
1. 停服务：python -m tools.pm_runtime.plugins.deepseek_thinking_fixer stop
2. 删目录：rm -rf tools/pm_runtime/plugins/deepseek_thinking_fixer/
3. 如果改了 CC Switch 数据库，记得恢复 provider_endpoints 的 URL
"""

from __future__ import annotations

__version__ = "1.0.0"
__plugin_name__ = "deepseek-thinking-fixer"
__description__ = "DeepSeek V4 thinking mode round-trip fixer"
__reason__ = (
    "DeepSeek returns thinking blocks in responses but CC Switch's rectifier "
    "only strips them from responses without re-injecting into next-turn requests."
)
