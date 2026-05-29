"""Smoke tests — cheap infrastructure checks to run before LLM agent cells.

Three checks per use case (always):
  1. scaffold  — run setup.sh, verify files are created (~5s)
  2. typecheck — tsc --noEmit on bare scaffold (~5s)
  3. mcp       — spawn MCP server, call list_files, verify response (~1s)

Optional fourth check (when reference solution exists):
  4. grader    — copy solutions/{vendor}/{yaml-stem}/ into scaffold, run grader

Run via `docs-eval smoke` or called automatically at the start of `docs-eval run`
(pass check_grader=False for the fast pre-run variant).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import UseCase


@dataclass
class SmokeResult:
    use_case_id: str
    scaffold_ok: bool
    scaffold_error: str = ""
    typecheck_ok: bool | None = None  # None = skipped
    typecheck_error: str = ""
    mcp_ok: bool | None = None        # None = skipped (scaffold failed)
    mcp_error: str = ""
    grader_ok: bool | None = None     # None = no reference solution / skipped
    grader_error: str = ""

    @property
    def passed(self) -> bool:
        return (
            self.scaffold_ok
            and self.typecheck_ok is not False
            and self.mcp_ok is not False
            and self.grader_ok is not False
        )

    def summary_line(self) -> str:
        def fmt(v: bool | None, label: str) -> str:
            if v is True:
                return f"[green]{label}:✓[/green]"
            if v is False:
                return f"[red]{label}:✗[/red]"
            return f"[dim]{label}:—[/dim]"

        parts = [
            fmt(self.scaffold_ok, "scaffold"),
            fmt(self.typecheck_ok, "typecheck"),
            fmt(self.mcp_ok, "mcp"),
            fmt(self.grader_ok, "grader"),
        ]
        return "  ".join(parts)

    def first_error(self) -> str:
        if not self.scaffold_ok:
            return self.scaffold_error
        if self.typecheck_ok is False:
            return self.typecheck_error
        if self.mcp_ok is False:
            return self.mcp_error
        if self.grader_ok is False:
            return self.grader_error
        return ""


def _resolve_path(p: str | Path, project_root: Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else project_root / p


def _solution_dir(uc: UseCase, project_root: Path) -> Path | None:
    d = project_root / "solutions" / uc.vendor / uc.source_path.stem
    if d.exists() and any(d.rglob("*")):
        return d
    return None


def _check_mcp(work_dir: Path, grader_script: Path) -> tuple[bool, str]:
    """Spawn MCP server, send list_files, verify it returns the scaffold files."""
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "docs_eval.mcp_server",
             "--work-dir", str(work_dir),         # already absolute (from tempfile)
             "--grader-script", str(grader_script),
             "--enable-fetch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "smoke", "version": "0.1"}}},
            {"jsonrpc": "2.0", "id": None, "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "list_files", "arguments": {}}},
        ]
        stdin_payload = "\n".join(json.dumps(m) for m in messages) + "\n"
        stdout, _ = proc.communicate(stdin_payload, timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        return False, "MCP server timed out after 15s"
    except Exception as e:
        return False, str(e)

    responses: dict[int, dict] = {}
    for line in stdout.splitlines():
        try:
            obj = json.loads(line)
            rid = obj.get("id")
            if rid is not None:
                responses[rid] = obj
        except json.JSONDecodeError:
            pass

    if 1 not in responses:
        return False, "MCP server did not respond to initialize"
    if 2 not in responses:
        return False, "MCP server did not respond to list_files"

    content = responses[2].get("result", {}).get("content", [])
    if not content:
        return False, "list_files returned empty content block"

    text = content[0].get("text", "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False, f"list_files non-JSON response: {text[:120]}"

    if "error" in data:
        return False, f"list_files error: {data['error']}"

    files = data.get("files", [])
    if not files:
        return False, f"list_files returned 0 files (work_dir={work_dir})"

    return True, ""


def _smoke_one(
    uc: UseCase,
    project_root: Path,
    verbose: bool,
    check_grader: bool,
) -> SmokeResult:
    setup_script = _resolve_path(uc.scaffold["setup"], project_root)
    grader_script = _resolve_path(uc.grader["run"], project_root)
    grader_env_extra = {k: str(v) for k, v in uc.grader.get("env", {}).items()}

    with tempfile.TemporaryDirectory(prefix="docs-eval-smoke-") as tmp:
        work_dir = Path(tmp)

        # ── 1. Scaffold ────────────────────────────────────────────────────
        proc = subprocess.run(
            ["bash", str(setup_script), str(work_dir)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            err = (proc.stdout + proc.stderr)[-600:].strip()
            return SmokeResult(use_case_id=uc.id, scaffold_ok=False, scaffold_error=err)
        if verbose:
            print(f"    scaffold  ✓")

        # ── 2. Typecheck on bare scaffold ──────────────────────────────────
        tsc = work_dir / "node_modules" / ".bin" / "tsc"
        tc = subprocess.run(
            [str(tsc), "--noEmit"],
            capture_output=True, text=True, cwd=work_dir, timeout=60,
        )
        typecheck_ok = tc.returncode == 0
        typecheck_err = "" if typecheck_ok else (tc.stdout + tc.stderr)[-600:].strip()
        if verbose:
            print(f"    typecheck {'✓' if typecheck_ok else '✗'}")
            if not typecheck_ok:
                print(f"      {typecheck_err[:200]}")

        # ── 3. MCP server round-trip ───────────────────────────────────────
        mcp_ok, mcp_err = _check_mcp(work_dir, grader_script)
        if verbose:
            print(f"    mcp       {'✓' if mcp_ok else '✗'}")
            if not mcp_ok:
                print(f"      {mcp_err}")

        # ── 4. Full grader on reference solution (streams output so it never hangs silently)
        grader_ok: bool | None = None
        grader_err = ""
        if check_grader:
            sol_dir = _solution_dir(uc, project_root)
            if sol_dir:
                for src in sol_dir.rglob("*"):
                    if src.is_file():
                        dst = work_dir / src.relative_to(sol_dir)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
                env = os.environ.copy()
                env.update(grader_env_extra)
                print(f"    grader    running…", flush=True)
                tail: list[str] = []
                IDLE_TIMEOUT = 45   # kill if no output for this many seconds
                try:
                    proc = subprocess.Popen(
                        ["bash", str(grader_script), str(work_dir)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, env=env,
                    )
                    assert proc.stdout
                    last_output = time.monotonic()
                    killed_reason = ""

                    def _watchdog() -> None:
                        nonlocal killed_reason
                        while proc.poll() is None:
                            if time.monotonic() - last_output > IDLE_TIMEOUT:
                                killed_reason = f"no output for {IDLE_TIMEOUT}s"
                                proc.kill()
                                return
                            time.sleep(2)

                    wd = threading.Thread(target=_watchdog, daemon=True)
                    wd.start()

                    for line in proc.stdout:
                        last_output = time.monotonic()
                        line = line.rstrip()
                        tail.append(line)
                        tail = tail[-40:]
                        if verbose:
                            print(f"      {line}", flush=True)

                    proc.wait(timeout=10)
                    grader_ok = proc.returncode == 0
                    if killed_reason:
                        grader_ok = False
                        tail.append(f"KILLED: {killed_reason}")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    grader_ok = False
                    tail.append("TIMEOUT after 300s")
                grader_err = "" if grader_ok else "\n".join(tail[-15:])
                print(f"    grader    {'✓' if grader_ok else '✗'}", flush=True)
                if not grader_ok and not verbose:
                    for ln in tail[-10:]:
                        print(f"      {ln}")

        return SmokeResult(
            use_case_id=uc.id,
            scaffold_ok=True,
            typecheck_ok=typecheck_ok, typecheck_error=typecheck_err,
            mcp_ok=mcp_ok, mcp_error=mcp_err,
            grader_ok=grader_ok, grader_error=grader_err,
        )


def run_smoke(
    use_cases: list[UseCase],
    project_root: Path,
    verbose: bool = False,
    check_grader: bool = True,
) -> list[SmokeResult]:
    results: list[SmokeResult] = []
    for uc in use_cases:
        if verbose:
            print(f"  [{uc.id}]")
        result = _smoke_one(uc, project_root, verbose, check_grader)
        results.append(result)
    return results
