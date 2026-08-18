"""Dependency-aware semantic plan utilities.

The model may describe a multi-step workflow, but it must never invent IDs
created by an earlier step. This module provides the small execution-context
boundary used to resolve references locally from domain-operation results.
"""

from __future__ import annotations

import re
from typing import Any


_REFERENCE_RE = re.compile(r"^\$([A-Za-z][A-Za-z0-9_-]*)\.([A-Za-z][A-Za-z0-9_.-]*)$")


class PlanReferenceError(ValueError):
    """Raised when a plan references an unavailable prior result."""


def step_name(step: dict, index: int) -> str:
    value = step.get("step_id") or step.get("id")
    if value is None:
        return f"step{index + 1}"
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", value):
        raise PlanReferenceError("plan step identifiers must be simple names")
    return value


def _lookup(reference: str, outputs: dict[str, dict[str, Any]]) -> Any:
    match = _REFERENCE_RE.fullmatch(reference)
    if not match:
        return reference
    step, path = match.groups()
    result = outputs.get(step)
    if result is None:
        raise PlanReferenceError(f"plan reference {reference} is not available yet")
    value: Any = result
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            raise PlanReferenceError(f"plan reference {reference} has no value")
        value = value[key]
    return value


def resolve_references(value: Any, outputs: dict[str, dict[str, Any]]) -> Any:
    """Recursively replace exact ``$step.field`` references in plan data."""
    if isinstance(value, str):
        return _lookup(value, outputs)
    if isinstance(value, list):
        return [resolve_references(item, outputs) for item in value]
    if isinstance(value, dict):
        return {key: resolve_references(item, outputs) for key, item in value.items()}
    return value


def resolve_step(step: dict, outputs: dict[str, dict[str, Any]]) -> dict:
    """Return a copy of a plan step with all local references resolved."""
    return resolve_references(step, outputs)


def record_output(
    outputs: dict[str, dict[str, Any]],
    name: str,
    result: object,
) -> None:
    """Record only structured operation results for later plan steps."""
    if isinstance(result, dict):
        outputs[name] = result
