#!/usr/bin/env python3
"""
Drift Check — 六件套文件 vs 现实文件系统一致性检查工具。

用法:
    python3 drift_check.py              # P0 + P1 (schema compliance + path/env check)
    python3 drift_check.py --deep       # + P2 (cross-reference + MCP config)
    python3 drift_check.py --full       # + P3 (git status + MCP endpoint ping)
    python3 drift_check.py -o report.yaml  # 输出到指定文件 (默认 stdout)
    python3 drift_check.py --quiet      # 仅输出 YAML report, 不打印摘要
    python3 drift_check.py --generate-map  # 运行漂移检测 + 生成 workflow_map.yaml（G6）

约束: 仅依赖 Python 标准库。不自启动、不自动改文件、不自注册。
"""

import argparse
import datetime
import os
import re
import socket
import subprocess
import sys
import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 脚本所在目录的上二级 = 项目根（自 WorkflowBase/self-maint/ 上二级）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKYB_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
REGISTRY_DIR = os.path.join(WORKYB_ROOT, "WorkflowBase", "registry")

REGISTRY_FILES = [
    "skill_registry.yaml",
    "mcp_registry.yaml",
    "hook_registry.yaml",
    "executor_registry.yaml",
]

# Schema: required universal fields
REQUIRED_FIELDS = ["id", "name", "type", "status", "purpose"]

# Enum definitions
ENUMS = {
    "type": ["skill", "mcp", "hook", "executor", "plugin", "gate_script", "utility", "resolver"],
    "status": ["active", "candidate", "to_be_filled", "deprecated", "retired"],
    "permission_level": ["readonly", "write_within_scope", "full"],
    "risk_level": ["low", "medium", "high", "critical"],
    "hook_type": ["shell", "python", "python_script", "http"],
    "execution_model": ["tmux_interactive", "managed_subprocess", "http_proxy"],
    "skill_type": ["hermes", "cc_switch", "adarian_builtin"],
    "transport": ["stdio", "http"],
}

VALID_LANES = {
    "A_LINE_FORMAL", "B_LINE_LIGHTWEIGHT_DAG", "WORKYB_RUNTIME",
    "DOGFOOD_TEST", "COURSEWORK", "EXPERIMENT", "PRODUCTIVITY", "ALL",
}

# Path fields to check for existence (P1)
PATH_FIELDS = {
    "skill_path": "skill",
    "hook_path": "hook",
    "module_path": "executor",
    "command": "mcp",
}

# System commands that are expected to be resolved via PATH, not as file paths
SYSTEM_COMMANDS = {"uvx", "uv", "python3", "python", "node", "npx"}

# Placeholder values
PLACEHOLDER_VALUES = {"to_be_filled", "TO_BE_FILLED", "", None}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_yaml_file(filepath):
    """Load a YAML file, return list of entries."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    # Strip comment-only leading lines that confuse YAML parser for some files
    data = yaml.safe_load(content)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    # Unexpected format
    return []


def resolve_path(path_str):
    """Expand ~ and make absolute."""
    return os.path.expanduser(os.path.expandvars(path_str))


def now_iso():
    return datetime.datetime.now(
        tz=datetime.timezone(datetime.timedelta(hours=8))
    ).isoformat(timespec="seconds")


class DriftItem:
    """Single drift finding."""
    def __init__(self, entry_id, registry_file, check_type, field,
                 expected, actual, severity, note=None):
        self.entry_id = entry_id
        self.registry_file = registry_file
        self.check_type = check_type
        self.field = field
        self.expected = str(expected)
        self.actual = str(actual)
        self.severity = severity
        self.note = note

    def to_dict(self):
        d = {
            "entry_id": self.entry_id,
            "registry_file": self.registry_file,
            "check_type": self.check_type,
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "severity": self.severity,
        }
        if self.note:
            d["note"] = self.note
        return d


# ---------------------------------------------------------------------------
# P0: Schema compliance
# ---------------------------------------------------------------------------

def check_schema_compliance(entries, registry_file):
    """Check required fields and enum values."""
    items = []
    for entry in entries:
        eid = entry.get("id", f"<unknown in {registry_file}>")
        entry_type = entry.get("type", "")

        # Required fields
        for field in REQUIRED_FIELDS:
            val = entry.get(field)
            if val is None or val == "":
                items.append(DriftItem(
                    eid, registry_file, "schema_compliance", field,
                    "present", "missing", "critical",
                    f"Required field '{field}' is missing or empty"
                ))

        # Enum validation
        for field_name, allowed_values in ENUMS.items():
            val = entry.get(field_name)
            if val is not None and val not in allowed_values:
                items.append(DriftItem(
                    eid, registry_file, "schema_compliance", field_name,
                    f"one of {allowed_values}", val, "warning",
                    f"Enum field '{field_name}' has invalid value"
                ))

        # owner_approval_required must exist
        if "owner_approval_required" not in entry:
            items.append(DriftItem(
                eid, registry_file, "schema_compliance",
                "owner_approval_required",
                "present (bool)", "missing", "warning",
                "owner_approval_required must be declared"
            ))

        # applicable_lanes validation
        lanes = entry.get("applicable_lanes", [])
        if isinstance(lanes, list):
            for lane in lanes:
                if lane not in VALID_LANES:
                    items.append(DriftItem(
                        eid, registry_file, "schema_compliance",
                        "applicable_lanes",
                        f"valid lane enum", lane, "warning",
                        f"Lane '{lane}' not in defined enum set"
                    ))

    return items


# ---------------------------------------------------------------------------
# P0: Cross-reference (depends_on / used_by)
# ---------------------------------------------------------------------------

def check_cross_references(all_entries_by_id):
    """Verify depends_on / used_by references point to existing entries."""
    items = []
    for eid, (entry, registry_file) in all_entries_by_id.items():
        for ref_field in ("depends_on", "used_by"):
            refs = entry.get(ref_field, [])
            if not isinstance(refs, list):
                continue
            for ref_id in refs:
                if ref_id not in all_entries_by_id:
                    items.append(DriftItem(
                        eid, registry_file, "cross_reference", ref_field,
                        f"entry '{ref_id}' exists", "not found", "critical",
                        f"Referenced entry '{ref_id}' does not exist in any registry"
                    ))
    return items


# ---------------------------------------------------------------------------
# P1: Path existence
# ---------------------------------------------------------------------------

def check_path_existence(entry, registry_file):
    """Check that path fields point to existing files/dirs."""
    items = []
    eid = entry.get("id", "<unknown>")
    entry_type = entry.get("type", "")

    # Check skill_path
    if "skill_path" in entry:
        sp = entry["skill_path"]
        if sp and sp not in PLACEHOLDER_VALUES:
            resolved = resolve_path(sp)
            if not os.path.exists(resolved):
                items.append(DriftItem(
                    eid, registry_file, "path_existence", "skill_path",
                    f"path exists: {sp}", "not found", "warning",
                ))

    # Check hook_path
    if "hook_path" in entry:
        hp = entry["hook_path"]
        if hp and hp not in PLACEHOLDER_VALUES:
            resolved = resolve_path(hp)
            if not os.path.exists(resolved):
                items.append(DriftItem(
                    eid, registry_file, "path_existence", "hook_path",
                    f"path exists: {hp}", "not found", "warning",
                ))

    # Check module_path
    if "module_path" in entry:
        mp = entry["module_path"]
        if mp and mp not in PLACEHOLDER_VALUES:
            resolved = resolve_path(mp)
            if not os.path.exists(resolved):
                # Try relative to project root
                alt = os.path.join(WORKYB_ROOT, mp)
                if not os.path.exists(alt):
                    items.append(DriftItem(
                        eid, registry_file, "path_existence", "module_path",
                        f"path exists: {mp}", "not found", "warning",
                    ))

    # Check command (MCP)
    if "command" in entry:
        cmd = entry["command"]
        if cmd and cmd not in PLACEHOLDER_VALUES:
            # Extract first token as the executable
            parts = str(cmd).split()
            exe = parts[0] if parts else ""
            if exe and exe not in SYSTEM_COMMANDS:
                resolved = resolve_path(exe)
                if not os.path.exists(resolved):
                    items.append(DriftItem(
                        eid, registry_file, "path_existence", "command",
                        f"executable exists: {exe}", "not found", "info",
                        "Command may rely on PATH resolution or be a relative path"
                    ))

    return items


# ---------------------------------------------------------------------------
# P1: Environment variable check
# ---------------------------------------------------------------------------

# Cache for config.yaml MCP env vars
_config_mcp_env_cache = None

def _get_config_mcp_env_vars():
    """Read ~/.hermes/config.yaml and collect env vars from MCP server env blocks."""
    global _config_mcp_env_cache
    if _config_mcp_env_cache is not None:
        return _config_mcp_env_cache

    config_path = os.path.expanduser("~/.hermes/config.yaml")
    if not os.path.exists(config_path):
        _config_mcp_env_cache = {}
        return _config_mcp_env_cache

    try:
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
        config_env = {}

        # Extract env blocks from MCP servers
        mcp_servers = data.get("mcp_servers", {})
        for server_name, server_config in mcp_servers.items():
            env_block = server_config.get("env", {})
            if isinstance(env_block, dict):
                for var_name in env_block:
                    config_env.setdefault(var_name, []).append(
                        f"~/.hermes/config.yaml mcp_servers.{server_name}.env"
                    )

        # Extract env from CC Switch proxy config
        cc_switch = data.get("cc-switch", {})
        proxy_http = cc_switch.get("http_proxy")
        if proxy_http:
            # Indicates proxy is configured via CC Switch
            config_env.setdefault("HTTP_PROXY", []).append(
                "~/.hermes/config.yaml cc-switch.http_proxy"
            )
        proxy_https = cc_switch.get("https_proxy")
        if proxy_https:
            config_env.setdefault("HTTPS_PROXY", []).append(
                "~/.hermes/config.yaml cc-switch.https_proxy"
            )

        _config_mcp_env_cache = config_env
        return config_env
    except Exception:
        _config_mcp_env_cache = {}
        return {}


def check_env_required(entry, registry_file):
    """Check env_required vars are set, accounting for config.yaml injection + annotations."""
    items = []
    eid = entry.get("id", "<unknown>")
    env_list = entry.get("env_required", [])
    if not isinstance(env_list, list):
        return items

    # Preload config.yaml MCP env block once
    config_env = _get_config_mcp_env_vars()

    for env_spec in env_list:
        raw_spec = str(env_spec)
        # Detect "(handled by ...)" annotation — env is managed through other means
        if "(handled by" in raw_spec.lower():
            continue

        var_name = raw_spec.split("(")[0].split("=")[0].strip()
        if not var_name:
            continue

        # 1. Check os.environ
        if var_name in os.environ:
            continue

        # 2. Check config.yaml MCP env blocks
        if var_name in config_env:
            note = f"Environment variable '{var_name}' not in shell env but IS configured in: {', '.join(config_env[var_name])}"
            items.append(DriftItem(
                eid, registry_file, "env_check", "env_required",
                f"${var_name} set", "not_set_in_shell_but_in_config", "info",
                note
            ))
            continue

        # 3. Truly missing
        items.append(DriftItem(
            eid, registry_file, "env_check", "env_required",
            f"${var_name} set", "not set", "warning",
            f"Environment variable '{var_name}' not found in shell env or config.yaml MCP env blocks"
        ))
    return items


# ---------------------------------------------------------------------------
# P2: Skill content drift — pm-relay vs references
# ---------------------------------------------------------------------------


def check_skill_content_drift() -> list[DriftItem]:
    """检查 pm-relay SKILL.md 与 reference 文件的路径一致性和内容同步。

    P2 deep check:
      — 扫描主 skill 中的路径引用（WorkflowBase/ vs tools/）
      — 检查 reference 文件路径是否仍然存在
      — 发现过时路径时报告 DriftItem
    """
    items = []
    skill_dir = os.path.expanduser("~/.hermes/skills/pm-runtime/pm-relay")
    skill_file = os.path.join(skill_dir, "SKILL.md")
    ref_dir = os.path.join(skill_dir, "references")
    if not os.path.exists(skill_file):
        return items

    # 读主 skill
    with open(skill_file, encoding="utf-8") as f:
        skill_text = f.read()

    # 过时路径模式 — 出现就算漂移
    DEPRECATED_PATTERNS = {
        "tools/pm_runtime/relay/": "WorkflowBase/runner/",
        "tools/pm_runtime/": "WorkflowBase/",
        "tools/dialog_watcher.py": "WorkflowBase/registry/skills/dispatch-prompt-authoring/scripts/dialog_watcher.py",
        "tools/sound_utils.py": "WorkflowBase/infra/sound/sound_utils.py",
        "from runner.": "from WorkflowBase.runner.",
        "import runner.": "import WorkflowBase.runner.",
    }

    for old, new in DEPRECATED_PATTERNS.items():
        count = skill_text.count(old)
        if count > 0:
            items.append(DriftItem(
                entry_id="skill-pm-relay",
                field="SKILL.md content",
                check_type="skill_content_drift",
                severity="warning",
                expected=f"替换为 '{new}'",
                actual=f"SKILL.md 含 {count} 处过时引用 '{old}'",
                registry_file="drift_check",
                note="见 relay-skill-governance skill 获取完整检查",
            ))

    # 检查 reference 文件列表是否与目录一致
    if os.path.isdir(ref_dir):
        ref_files = sorted(os.listdir(ref_dir))
        for ref in ref_files:
            ref_path = os.path.join(ref_dir, ref)
            if os.path.isfile(ref_path) and ref.endswith(".md"):
                with open(ref_path, encoding="utf-8") as f:
                    ref_text = f.read()
                for old, new in DEPRECATED_PATTERNS.items():
                    if old in ref_text:
                        items.append(DriftItem(
                            entry_id=f"ref-{ref}",
                            field=f"references/{ref}",
                            check_type="skill_content_drift",
                            severity="warning",
                            expected=f"替换为 '{new}'（或加过时标记注释）",
                            actual=f"reference '{ref}' 含过时路径 '{old}'",
                            registry_file="drift_check",
                        ))

    return items


# ---------------------------------------------------------------------------
# P2: MCP config cross-check
# ---------------------------------------------------------------------------

def check_filesystem_vs_registry() -> list[DriftItem]:
    """P2: Reverse scan — check filesystem for items not in registry.

    Scans ~/.cc-switch/skills/ for unregistered skills and
    ~/.hermes/scripts/ for unregistered hooks.
    """
    items = []

    # Scan CC Switch skills
    cc_skills_dir = os.path.expanduser("~/.cc-switch/skills")
    if os.path.isdir(cc_skills_dir):
        registered_skills = set()
        skill_reg_path = os.path.join(REGISTRY_DIR, "skill_registry.yaml")
        if os.path.exists(skill_reg_path):
            for entry in load_yaml_file(skill_reg_path):
                eid = entry.get("id", "")
                if eid.startswith("skill-cc-"):
                    registered_skills.add(eid.replace("skill-cc-", ""))

        for skill_dir in sorted(os.listdir(cc_skills_dir)):
            skill_md = os.path.join(cc_skills_dir, skill_dir, "SKILL.md")
            if os.path.isfile(skill_md) and skill_dir not in registered_skills:
                items.append(DriftItem(
                    skill_dir, "skill_registry.yaml",
                    "filesystem_vs_registry", "skill_path",
                    f"registered in registry",
                    f"exists at ~/.cc-switch/skills/{skill_dir}/ but NOT in skill_registry.yaml",
                    "warning",
                    "Manually add a skill-cc- entry to skill_registry.yaml"
                ))

    # Scan Hermes scripts for hooks not in hook_registry
    hermes_scripts_dir = os.path.expanduser("~/.hermes/scripts")
    if os.path.isdir(hermes_scripts_dir):
        registered_hooks = set()
        hook_reg_path = os.path.join(REGISTRY_DIR, "hook_registry.yaml")
        if os.path.exists(hook_reg_path):
            for entry in load_yaml_file(hook_reg_path):
                eid = entry.get("id", "")
                # Check hook_path field for script names
                hook_path = entry.get("hook_path", "") or entry.get("script_path", "")
                if hook_path:
                    registered_hooks.add(os.path.basename(hook_path))

        for script_file in sorted(os.listdir(hermes_scripts_dir)):
            if script_file.endswith((".py", ".sh")) and script_file not in registered_hooks:
                # Skip known utility files
                if script_file in ("__init__.py",):
                    continue
                items.append(DriftItem(
                    script_file, "hook_registry.yaml",
                    "filesystem_vs_registry", "hook_path",
                    f"registered in registry",
                    f"exists at ~/.hermes/scripts/{script_file} but NOT in hook_registry.yaml",
                    "info",
                    "Register as hook or gate_script if needed"
                ))

    return items


def check_mcp_config_cross_reference():
    """Compare MCP entries in config.yaml vs mcp_registry.yaml."""
    items = []

    # Try to find config.yaml in common locations
    config_candidates = [
        os.path.join(os.path.expanduser("~"), ".hermes", "config.yaml"),
        os.path.join(os.path.expanduser("~"), ".claude", "config.yaml"),
        os.path.join(os.path.expanduser("~"), ".claude.json"),
        os.path.join(WORKYB_ROOT, "config.yaml"),
    ]

    config_path = None
    for p in config_candidates:
        if os.path.exists(p):
            config_path = p
            break

    if config_path is None:
        items.append(DriftItem(
            "mcp_registry.yaml", "mcp_registry.yaml",
            "cross_reference", "config.yaml",
            "config.yaml accessible", "not found",
            "info", "No config.yaml found for MCP cross-reference check"
        ))
        return items

    # Load config and mcp_registry
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        items.append(DriftItem(
            "mcp_registry.yaml", "mcp_registry.yaml",
            "cross_reference", "config.yaml",
            "valid YAML", f"parse error: {e}",
            "warning", "config.yaml could not be parsed"
        ))
        return items

    mcp_reg_path = os.path.join(REGISTRY_DIR, "mcp_registry.yaml")
    mcp_entries = load_yaml_file(mcp_reg_path)
    mcp_names = {e.get("server_name", e.get("id", "")) for e in mcp_entries}

    # Extract MCP servers from config (Hermes: mcp_servers, Claude: mcpServers)
    config_mcp = {}
    for key in ("mcp_servers", "mcpServers"):
        val = config.get(key, {})
        if isinstance(val, dict):
            config_mcp.update(val)
    if isinstance(config_mcp, dict):
        config_names = set(config_mcp.keys())
    else:
        config_names = set()

    # Registry has, config doesn't
    for name in mcp_names - config_names:
        items.append(DriftItem(
            f"mcp-{name}", "mcp_registry.yaml",
            "cross_reference", "mcpServers",
            f"'{name}' in config.yaml", "missing",
            "warning", "MCP server in registry but not in config"
        ))

    # Config has, registry doesn't
    for name in config_names - mcp_names:
        items.append(DriftItem(
            f"mcp-{name}", "config.yaml",
            "cross_reference", "mcpServers",
            f"'{name}' in mcp_registry.yaml", "missing",
            "info", "MCP server in config but not in registry"
        ))

    return items


# ---------------------------------------------------------------------------
# P2: README consistency
# ---------------------------------------------------------------------------

def check_readme_consistency():
    """Verify README entry counts match actual counts."""
    items = []
    readme_path = os.path.join(REGISTRY_DIR, "README.md")
    if not os.path.exists(readme_path):
        items.append(DriftItem(
            "README.md", "README.md",
            "readme_consistency", "file",
            "README.md exists", "not found", "critical"
        ))
        return items

    with open(readme_path, "r", encoding="utf-8") as f:
        readme_content = f.read()

    # Count actual entries per file
    expected_counts = {
        "skill_registry.yaml": ("Skill", r"已注册 Skill[：:]\s*~?(\d+)"),
        "mcp_registry.yaml": ("MCP", r"已注册 MCP[：:]\s*(\d+)"),
        "hook_registry.yaml": ("Hook", r"已注册 Hook[：:]\s*(\d+)"),
        "executor_registry.yaml": ("Executor", r"已注册 Executor.*?[：:]\s*(\d+)"),
    }

    for fname, (label, pattern) in expected_counts.items():
        fpath = os.path.join(REGISTRY_DIR, fname)
        if not os.path.exists(fpath):
            continue
        actual = len(load_yaml_file(fpath))
        match = re.search(pattern, readme_content)
        if match:
            declared = int(match.group(1))
            # Only flag if the difference is significant (> 1 from declared top-level)
            # README counts may be approximate due to groups
            if abs(actual - declared) > 2 and "~" not in match.group(0):
                items.append(DriftItem(
                    "README.md", "README.md",
                    "readme_consistency", f"entry_count_{fname}",
                    f"{declared} {label} entries", f"{actual} actual",
                    "info", "README count doesn't match actual entry count"
                ))

    return items


# ---------------------------------------------------------------------------
# P3: Git status
# ---------------------------------------------------------------------------

def check_git_status():
    """Detect uncommitted changes in registry directory."""
    items = []
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", REGISTRY_DIR],
            capture_output=True, text=True, timeout=10,
            cwd=WORKYB_ROOT,
        )
        if result.returncode == 0 and result.stdout.strip():
            changed_files = [
                line.strip() for line in result.stdout.strip().split("\n")
                if line.strip()
            ]
            items.append(DriftItem(
                "git_status", "registry/",
                "git_status", "uncommitted_changes",
                "clean working tree",
                f"{len(changed_files)} file(s) with uncommitted changes",
                "info",
                f"Files: {', '.join(os.path.basename(f.split()[-1]) for f in changed_files[:5])}"
            ))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        items.append(DriftItem(
            "git_status", "registry/",
            "git_status", "git",
            "git available", "git not available or timeout",
            "info"
        ))
    return items


# ---------------------------------------------------------------------------
# P3: MCP endpoint ping
# ---------------------------------------------------------------------------

def check_mcp_endpoints():
    """Ping MCP endpoints (HTTP transport only, non-blocking)."""
    items = []
    mcp_path = os.path.join(REGISTRY_DIR, "mcp_registry.yaml")
    mcp_entries = load_yaml_file(mcp_path)

    for entry in mcp_entries:
        transport = entry.get("transport", "stdio")
        url = entry.get("url", "")
        eid = entry.get("id", "<unknown>")

        if transport == "http" and url:
            # Extract host:port from URL
            from urllib.parse import urlparse
            try:
                parsed = urlparse(url)
                host = parsed.hostname
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                result = sock.connect_ex((host, port))
                sock.close()
                if result != 0:
                    items.append(DriftItem(
                        eid, "mcp_registry.yaml",
                        "mcp_endpoint_ping", "url",
                        f"reachable: {url}", f"connection refused (port {port})",
                        "info", "Non-blocking; MCP may start on demand"
                    ))
            except Exception as e:
                items.append(DriftItem(
                    eid, "mcp_registry.yaml",
                    "mcp_endpoint_ping", "url",
                    f"reachable: {url}", f"ping failed: {e}",
                    "info", "Non-blocking; MCP may start on demand"
                ))

    return items


# ---------------------------------------------------------------------------
# Security annotation (P3)
# ---------------------------------------------------------------------------

def check_security_annotations(entries, registry_file):
    """Check security-related annotations."""
    items = []
    for entry in entries:
        eid = entry.get("id", "<unknown>")
        perm = entry.get("permission_level", "")
        risk = entry.get("risk_level", "")
        forbidden = entry.get("forbidden_paths", [])

        # permission_level=full should have forbidden_paths
        if perm == "full" and (not forbidden or forbidden == []):
            items.append(DriftItem(
                eid, registry_file, "security_annotation",
                "forbidden_paths",
                "non-empty forbidden_paths for permission_level=full",
                "empty or missing",
                "warning",
                "Entries with permission_level=full should define forbidden_paths"
            ))

    return items


# ── G6: Workflow Map Generator ──────────────────────────────────────────────

def generate_workflow_map() -> dict:
    """Generate workflow_map.yaml — 机器维护的 Agent 导航地图。
    
    由 drift_check --generate-map 触发。
    基于当前注册表 + 文件系统状态，生成精简导航索引。
    """
    # Load registry YAMLs
    registries = {}
    reg_dir = REGISTRY_DIR
    for fname in REGISTRY_FILES:
        fpath = os.path.join(reg_dir, fname)
        entries = load_yaml_file(fpath) if os.path.exists(fpath) else []
        registries[fname.replace(".yaml", "")] = entries

    # ── Build map ──
    skills = registries.get("skill_registry", [])
    mcps = registries.get("mcp_registry", [])
    hooks = registries.get("hook_registry", [])
    executors_raw = registries.get("executor_registry", [])

    mcp_names = sorted(e.get("server_name") or e.get("id", "").replace("mcp-", "") for e in mcps if e.get("status") == "active")
    skill_count = len([e for e in skills if e.get("status") == "active"])
    has_tmux = any(e.get("execution_model") == "tmux_interactive" for e in executors_raw)
    has_subprocess = any(e.get("execution_model") == "managed_subprocess" for e in executors_raw)
    has_http = any(e.get("execution_model") == "http_proxy" for e in executors_raw)

    auto_hooks = sorted(e.get("id", "") for e in hooks if e.get("type") == "hook" and e.get("status") == "active")
    gate_scripts = sorted(e.get("id", "") for e in hooks if e.get("type") in ("gate_script",) and e.get("status") == "active")
    utility_scripts = sorted(e.get("id", "") for e in hooks if e.get("type") == "utility" and e.get("status") == "active")

    executor_list = []
    for e in executors_raw:
        eid = e.get("id", "unknown")
        etype = e.get("type", "")
        model = e.get("execution_model", "")
        if etype in ("executor", "plugin"):
            executor_list.append({"id": eid, "execution_model": model})
        elif etype == "resolver":
            executor_list.append({"id": eid, "type": "resolver"})

    # Scan active iteration plans
    adarian_root = WORKYB_ROOT
    active_iter_dir = os.path.join(adarian_root, "docs", "iterations", "active")
    active_plans = []
    if os.path.isdir(active_iter_dir):
        for f in sorted(os.listdir(active_iter_dir)):
            if f.endswith(".md") and not f.startswith("_") and not f.startswith("."):
                active_plans.append(f)

    archived_root = os.path.join(adarian_root, "docs", "iterations", "archived")
    archived_versions = []
    if os.path.isdir(archived_root):
        archived_versions = sorted(
            d for d in os.listdir(archived_root)
            if os.path.isdir(os.path.join(archived_root, d))
        )

    # Check self-maint status
    drift_check_path = os.path.join(WORKYB_ROOT, "WorkflowBase", "self-maint", "drift_check.py")
    self_maint_active = os.path.exists(drift_check_path)

    map_data = {
        "workflow_map": {
            "generated_at": datetime.datetime.now(
                tz=datetime.timezone(datetime.timedelta(hours=8))
            ).isoformat(timespec="seconds"),
            "purpose": "机器维护的 Agent 导航地图。Agent 加载后用于了解系统结构和工作流位置。",
            "workspace": {
                "project": "Adarian MVP",
                "version": "v4.0",
                "root": str(WORKYB_ROOT),
                "workflow_base": str(REGISTRY_DIR.replace("/registry", "")),
            },
            "iterations": {
                "active": active_plans,
                "archived_versions": archived_versions,
                "template": "docs/iterations/templates/_template_v4.0_full.md",
                "template_light": "docs/iterations/templates/_template_v4.0_light_draft.md",
            },
            "registries": {
                "skills": {
                    "total_active": skill_count,
                    "total_entries": len(skills),
                    "registry_file": "WorkflowBase/registry/skill_registry.yaml",
                    "schema_file": "WorkflowBase/registry/registry_schema.md",
                },
                "mcps": {
                    "active": mcp_names,
                    "count": len(mcp_names),
                    "registry_file": "WorkflowBase/registry/mcp_registry.yaml",
                },
                "hooks": {
                    "auto_trigger": auto_hooks,
                    "gate_scripts": gate_scripts,
                    "utility_scripts": utility_scripts,
                    "total": len(auto_hooks) + len(gate_scripts) + len(utility_scripts),
                    "registry_file": "WorkflowBase/registry/hook_registry.yaml",
                },
                "executors": {
                    "entries": executor_list,
                    "count": len(executor_list),
                    "has_tmux": has_tmux,
                    "has_subprocess": has_subprocess,
                    "has_http_proxy": has_http,
                    "registry_file": "WorkflowBase/registry/executor_registry.yaml",
                },
            },
            "runner": {
                "relay_runner": "WorkflowBase/runner/relay_runner.py",
                "executor_registry": "WorkflowBase/runner/executor_registry.py",
                "path_resolver": "WorkflowBase/runner/path_resolver.py",
            },
            "governance": {
                "drift_check": {
                    "active": self_maint_active,
                    "script": "WorkflowBase/self-maint/drift_check.py",
                    "known_blind_spots": "WorkflowBase/self-maint/KNOWN_BLIND_SPOTS.md",
                },
                "closeout_gate": {
                    "skill": "closeout-gate",
                    "writer_script": "~/.hermes/scripts/task-status-writer.py",
                },
                "memory_governance": {
                    "registry": "WorkflowBase/memory/memory_registry.yaml",
                    "retrieval_protocol": "WorkflowBase/memory/retrieval_protocol.yaml",
                },
            },
            "key_paths": {
                "templates": "docs/iterations/templates/",
                "design_docs": "docs/design/",
                "active_iterations": "docs/iterations/active/",
                "archived_iterations": "docs/iterations/archived/",
                "task_active": "tasks/active/",
                "task_archived": "tasks/archived/",
            },
            "workflow_conventions": {
                "template_full": "`_template_v4.0_full.md` 用于 L-Level、全链路 gate 任务",
                "template_light": "`_template_v4.0_light_draft.md` 用于日常敏捷迭代",
                "log_updates": "TASK_LOG.md + CHANGELOG.md 由 relay_runner 自动维护",
                "closeout_profiles": "smoke | standard | full_dag（closeout-gate skill）",
            },
        }
    }
    return map_data


# ── Main ────────────────────────────────────────────────────────────────────

def run_drift_check(args):
    """Execute drift check and return structured report."""
    checks_performed = []
    all_items = []

    # Load all entries
    all_entries_by_id = {}  # id -> (entry, registry_file)
    total_entries = 0

    for fname in REGISTRY_FILES:
        fpath = os.path.join(REGISTRY_DIR, fname)
        if not os.path.exists(fpath):
            all_items.append(DriftItem(
                fname, fname, "schema_compliance", "file",
                f"file exists: {fname}", "not found", "critical",
                "Registry file is missing"
            ))
            continue

        entries = load_yaml_file(fpath)
        total_entries += len(entries)

        # Register all entries for cross-reference
        for entry in entries:
            eid = entry.get("id")
            if eid:
                all_entries_by_id[eid] = (entry, fname)

    # --- P0: Schema compliance ---
    checks_performed.append("schema_compliance")
    for fname in REGISTRY_FILES:
        fpath = os.path.join(REGISTRY_DIR, fname)
        if os.path.exists(fpath):
            entries = load_yaml_file(fpath)
            all_items.extend(check_schema_compliance(entries, fname))

    # --- P0: Cross-reference (depends_on / used_by) ---
    checks_performed.append("cross_reference")
    all_items.extend(check_cross_references(all_entries_by_id))

    # --- P1: Path existence + env check ---
    checks_performed.extend(["path_existence", "env_check"])
    for fname in REGISTRY_FILES:
        fpath = os.path.join(REGISTRY_DIR, fname)
        if os.path.exists(fpath):
            entries = load_yaml_file(fpath)
            for entry in entries:
                all_items.extend(check_path_existence(entry, fname))
                all_items.extend(check_env_required(entry, fname))

    # --- P2: Deep checks ---
    if args.deep or args.full:
        checks_performed.extend(["cross_reference_mcp", "readme_consistency",
                                 "filesystem_vs_registry",
                                 "skill_content_drift"])
        all_items.extend(check_mcp_config_cross_reference())
        all_items.extend(check_readme_consistency())
        all_items.extend(check_filesystem_vs_registry())
        all_items.extend(check_skill_content_drift())

    # --- P3: Full checks ---
    if args.full:
        checks_performed.extend(["git_status", "mcp_endpoint_ping",
                                  "security_annotation"])
        all_items.extend(check_git_status())
        all_items.extend(check_mcp_endpoints())
        for fname in REGISTRY_FILES:
            fpath = os.path.join(REGISTRY_DIR, fname)
            if os.path.exists(fpath):
                entries = load_yaml_file(fpath)
                all_items.extend(check_security_annotations(entries, fname))

    # Build report
    drifted = len(all_items)
    healthy = total_entries - len(set(item.entry_id for item in all_items
                                       if item.severity == "critical"))

    report = {
        "drift_report": {
            "generated_at": now_iso(),
            "registry_path": REGISTRY_DIR,
            "schema_version": "R0",
            "total_entries": total_entries,
            "drifted": drifted,
            "healthy": max(0, healthy),
            "checks_performed": checks_performed,
            "items": [item.to_dict() for item in all_items],
        }
    }

    return report


def format_summary(report):
    """Print human-readable summary to stderr."""
    dr = report["drift_report"]
    items = dr["items"]

    print("=" * 60, file=sys.stderr)
    print("  DRIFT CHECK REPORT", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Generated:     {dr['generated_at']}", file=sys.stderr)
    print(f"  Registry:      {dr['registry_path']}", file=sys.stderr)
    print(f"  Schema:        {dr['schema_version']}", file=sys.stderr)
    print(f"  Total entries: {dr['total_entries']}", file=sys.stderr)
    print(f"  Checks:        {', '.join(dr['checks_performed'])}", file=sys.stderr)
    print("-" * 60, file=sys.stderr)

    if not items:
        print("  ✅ ALL CLEAR — no drift detected.", file=sys.stderr)
    else:
        severity_counts = {}
        for item in items:
            s = item["severity"]
            severity_counts[s] = severity_counts.get(s, 0) + 1

        print(f"  Drift items:   {len(items)}", file=sys.stderr)
        for sev in ["critical", "warning", "info"]:
            if sev in severity_counts:
                icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}[sev]
                print(f"    {icon} {sev}: {severity_counts[sev]}", file=sys.stderr)

        # Show critical items
        criticals = [i for i in items if i["severity"] == "critical"]
        if criticals:
            print("\n  CRITICAL items:", file=sys.stderr)
            for c in criticals[:10]:
                print(f"    - [{c['entry_id']}] {c['field']}: "
                      f"expected={c['expected']}, actual={c['actual']}", file=sys.stderr)
            if len(criticals) > 10:
                print(f"    ... and {len(criticals) - 10} more", file=sys.stderr)

    print("=" * 60, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Drift Check — 六件套 vs 现实一致性检查"
    )
    parser.add_argument("--deep", action="store_true",
                        help="Enable P2 checks (cross-reference MCP config + README)")
    parser.add_argument("--full", action="store_true",
                        help="Enable P3 checks (git status + MCP ping + security)")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Output YAML file path (default: stdout)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress summary to stderr")
    parser.add_argument("--generate-map", action="store_true",
                        help="Generate workflow_map.yaml (G6: agent navigation map)")
    args = parser.parse_args()

    report = run_drift_check(args)

    # Output YAML
    yaml_output = yaml.dump(report, default_flow_style=False,
                            allow_unicode=True, sort_keys=False,
                            width=120)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(yaml_output)
        if not args.quiet:
            print(f"Report written to: {args.output}", file=sys.stderr)
    else:
        print(yaml_output)

    if not args.quiet:
        format_summary(report)

    # ── G6: Generate workflow map ──
    if args.generate_map:
        map_data = generate_workflow_map()
        map_path = os.path.join(WORKYB_ROOT, "workflow_map.yaml")
        os.makedirs(os.path.dirname(os.path.abspath(map_path)), exist_ok=True)
        with open(map_path, "w", encoding="utf-8") as f:
            yaml.dump(map_data, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False, width=120)
        if not args.quiet:
            print(f"Workflow map generated: {map_path}", file=sys.stderr)

    # Exit code: 1 if critical drift found
    dr = report["drift_report"]
    has_critical = any(i["severity"] == "critical" for i in dr["items"])
    sys.exit(1 if has_critical else 0)


if __name__ == "__main__":
    main()
