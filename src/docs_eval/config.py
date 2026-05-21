"""Config loading for use cases and targets.

Both are YAML on disk. We keep them as plain dicts with light validation so
that adding fields doesn't require touching the type definitions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml


@dataclass
class UseCase:
    id: str
    vendor: str
    title: str
    difficulty: int
    tags: list[str]
    prompt: str
    expected: dict[str, Any]
    grader: dict[str, Any]
    budget: dict[str, Any]
    source_path: Path
    # Optional human review spec. When present, the runner can start the app
    # locally after the grader passes and ask a human to confirm it works.
    human_check: dict[str, Any] = field(default_factory=dict)

    @property
    def max_turns(self) -> int:
        return int(self.budget.get("max_turns", 8))

    @property
    def max_seconds(self) -> int:
        return int(self.budget.get("max_seconds", 300))


@dataclass
class Target:
    name: str
    vendor: str
    platform: str
    base_url: str
    llms_txt: str | None
    mcp_endpoint: str | None
    markdown_suffix: str | None
    notes: str
    enabled: bool = True
    # Override the Mintlify MCP tool slug (e.g. "zero_dev" → search_zero_dev).
    # If None, falls back to vendor.lower().replace("-","_") + "_docs".
    mcp_tool_slug: str | None = None
    # Free-form extension point — anything in the YAML we didn't pull out.
    extra: dict[str, Any] = field(default_factory=dict)


# Modes the runner can execute. Each test cell is one (use_case, target, mode).
MODES = ("web", "llms-txt", "mcp", "skill")


def load_use_case(path: Path) -> UseCase:
    with path.open() as f:
        data = yaml.safe_load(f)
    required = ("id", "vendor", "title", "prompt", "expected", "grader", "budget")
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"{path}: missing required fields: {missing}")
    return UseCase(
        id=data["id"],
        vendor=data["vendor"],
        title=data["title"],
        difficulty=int(data.get("difficulty", 1)),
        tags=list(data.get("tags", [])),
        prompt=data["prompt"],
        expected=data["expected"],
        grader=data["grader"],
        budget=data["budget"],
        source_path=path,
        human_check=dict(data.get("human_check", {})),
    )


def load_use_cases(root: Path, patterns: Iterable[str] | None = None) -> list[UseCase]:
    """Load all use cases under `root`, optionally filtered by glob patterns.

    Patterns are matched against the relative path under `root`. A pattern like
    "zerodev/*" matches all ZeroDev cases.
    """
    files: list[Path] = []
    if patterns:
        for pat in patterns:
            files.extend(root.glob(pat))
    else:
        files.extend(root.rglob("*.yaml"))
    # Skip schema docs etc.
    files = [f for f in files if f.name != "SCHEMA.md" and f.suffix in (".yaml", ".yml")]
    return [load_use_case(f) for f in sorted(set(files))]


def load_targets(path: Path, names: Iterable[str] | None = None) -> list[Target]:
    """Load targets from a single YAML file (the master targets.yaml).

    If `names` is given, returns only those (in order). Disabled targets are
    skipped unless explicitly named — letting you force a run on a target you
    know is half-finished.
    """
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    raw_targets = data.get("targets", [])

    out: list[Target] = []
    for entry in raw_targets:
        t = Target(
            name=entry["name"],
            vendor=entry["vendor"],
            platform=entry.get("platform", "custom"),
            base_url=entry["base_url"],
            llms_txt=entry.get("llms_txt"),
            mcp_endpoint=entry.get("mcp_endpoint"),
            markdown_suffix=entry.get("markdown_suffix"),
            notes=entry.get("notes", ""),
            enabled=bool(entry.get("enabled", True)),
            mcp_tool_slug=entry.get("mcp_tool_slug"),
            extra={k: v for k, v in entry.items()
                   if k not in {"name", "vendor", "platform", "base_url",
                                "llms_txt", "mcp_endpoint", "markdown_suffix",
                                "notes", "enabled", "mcp_tool_slug"}},
        )
        out.append(t)

    if names is not None:
        name_set = set(names)
        out = [t for t in out if t.name in name_set]
        # Don't silently drop named-but-missing targets
        found = {t.name for t in out}
        missing = name_set - found
        if missing:
            raise ValueError(f"Targets not found in {path}: {sorted(missing)}")
    else:
        out = [t for t in out if t.enabled]

    return out
