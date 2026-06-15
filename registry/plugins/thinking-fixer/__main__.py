"""
Standalone entry: python -m tools.pm_runtime.plugins.deepseek_thinking_fixer start
"""
from __future__ import annotations
from .fixer import main

if __name__ == "__main__":
    raise SystemExit(main())
