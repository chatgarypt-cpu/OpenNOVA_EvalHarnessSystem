"""Minimal executor registry for PM Runtime relay R1.2.

Provides registration and lookup of executor types.
Each executor class must implement:
    __init__(self, config: dict) -> None
    run(self) -> RunResult          # or anything with .exit_code/.classification/etc.

Usage:
    # Registration (at module bottom of executor .py):
    register_executor("claude", ClaudeTmuxExecutor)

    # Lookup (in relay_runner.run_task):
    cls = get_executor("codex")
    if cls:
        result = cls(config).run()
        # result.exit_code, result.classification, ...

=== Future registry upgrade ===
- Add base class / Protocol for type safety
- Add validate_config() per executor
- Add lifecycle hooks (pre_run, post_run)
- Add executor_options schema validation
"""
from __future__ import annotations

import importlib
from typing import Any

# Registered executor classes: name → class
_EXECUTORS: dict[str, type] = {}

# Module map for lazy loading: name → relative module path
# When get_executor() is called and the module isn't loaded yet,
# the registry imports the module to trigger its registration code.
_MODULE_MAP: dict[str, str] = {
    "claude": "WorkflowBase.runner.claude.tmux_executor",
    "codex": "WorkflowBase.runner.codex.executor",
    "codex-tmux": "WorkflowBase.runner.codex.tmux_executor",
}


def register_executor(name: str, cls: type) -> None:
    """Register an executor class under the given name."""
    _EXECUTORS[name] = cls


def get_executor(name: str) -> type | None:
    """Look up an executor class by name.

    Lazily loads the executor module on first access so registration
    code at module level is triggered. Returns None if the executor
    type is unknown or the module fails to import.
    """
    # Already registered
    cls = _EXECUTORS.get(name)
    if cls is not None:
        return cls

    # Try to load the module (triggers registration at module level)
    module_path = _MODULE_MAP.get(name)
    if module_path is not None:
        try:
            importlib.import_module(module_path)
        except ImportError:
            return None
        return _EXECUTORS.get(name)

    return None


def list_executors() -> list[str]:
    """Return all known executor type names."""
    return list(_MODULE_MAP.keys())
