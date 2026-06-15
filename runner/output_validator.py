"""Output validator — validates expected outputs exist and are non-empty.

Used by ClaudeTmuxExecutor._validate_and_repair() for post-execution
artifact validation and Repair Agent trigger. G5 implementation.

Usage:
    from output_validator import validate_outputs
    result = validate_outputs(task_dir, ["outputs/report.md"])
    if result.all_pass:
        print("All outputs present")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Any


class _ValidationItem:
    """Lightweight wrapper so callers can use ``item.path`` / ``item.error``."""
    __slots__ = ("path", "error", "status", "resolved", "size", "reason")

    def __init__(self, path: str, error: bool, status: str = "",
                 resolved: str = "", size: int = 0, reason: str = "") -> None:
        self.path = path
        self.error = error
        self.status = status
        self.resolved = resolved
        self.size = size
        self.reason = reason


@dataclass
class ValidationResult:
    """Result of a validate_outputs() call."""
    verdict: str          # "pass" | "fail"
    all_pass: bool
    failures: list[dict] = field(default_factory=list)
    details: list[dict] = field(default_factory=list)

    # ---- interface expected by relay_runner Repair Agent ----

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON/YAML embedding."""
        return asdict(self)

    @property
    def items(self) -> list[_ValidationItem]:
        """Expose details as objects with ``.path`` / ``.error`` attributes.

        Each item carries ``error=True`` when its status is *missing*
        or *empty* (i.e. validation failed for that path).
        """
        failure_paths = {f["path"] for f in self.failures}
        return [
            _ValidationItem(
                path=d["path"],
                error=d["path"] in failure_paths,
                status=d.get("status", ""),
                resolved=d.get("resolved", ""),
                size=d.get("size", 0),
                reason=next(
                    (f["reason"] for f in self.failures if f["path"] == d["path"]),
                    "",
                ),
            )
            for d in self.details
        ]


def validate_outputs(task_dir: str, expected_paths: list[str]) -> ValidationResult:
    """Validate that expected output files exist and are non-empty.
    
    Args:
        task_dir: Absolute path to the task directory.
        expected_paths: List of relative or absolute paths to check.
    
    Returns:
        ValidationResult with verdict, all_pass, failures, details.
    """
    failures = []
    details = []
    all_pass = True
    
    for path_str in expected_paths:
        # Resolve relative paths against task_dir
        if not os.path.isabs(path_str):
            resolved = os.path.join(task_dir, path_str)
        else:
            resolved = path_str
        
        resolved = os.path.normpath(resolved)
        exists = os.path.exists(resolved)
        is_empty = exists and os.path.isfile(resolved) and os.path.getsize(resolved) == 0
        
        status = "present"
        if not exists:
            status = "missing"
            all_pass = False
            failures.append({
                "path": path_str,
                "resolved": resolved,
                "reason": "file does not exist",
            })
        elif is_empty:
            status = "empty"
            all_pass = False
            failures.append({
                "path": path_str,
                "resolved": resolved,
                "reason": "file is empty",
            })
        
        details.append({
            "path": path_str,
            "resolved": resolved,
            "status": status,
            "size": os.path.getsize(resolved) if exists else 0,
        })
    
    return ValidationResult(
        verdict="pass" if all_pass else "fail",
        all_pass=all_pass,
        failures=failures,
        details=details,
    )
