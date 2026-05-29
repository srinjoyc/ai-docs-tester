"""Minimal stdio MCP server exposing docs-eval tools to the Claude Code CLI.

We implement the MCP JSON-RPC protocol by hand rather than using the `mcp`
package because the package requires Python >=3.10 and this project targets
3.9. The protocol is simple enough that we don't need the SDK: each message
is a JSON object on a newline; we respond to initialize / tools/list /
tools/call and ignore everything else.

This server is started as a stdio subprocess per cell by _run_loop_claude()
in runner.py. We use this path (instead of calling the Anthropic API directly)
because we don't hold a raw ANTHROPIC_API_KEY — the Claude Code CLI manages
authentication and we invoke it via `claude -p`.

Usage:
    python -m docs_eval.mcp_server \
        --work-dir /path/to/workdir \
        --grader-script /path/to/run.sh \
        [--grader-env '{"KEY":"val"}'] \
        [--enable-fetch]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _respond(req_id: object, result: object) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id: object, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _tool_result(req_id: object, text: str, is_error: bool = False) -> None:
    _respond(req_id, {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    })


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _list_files(work_dir: Path) -> str:
    files: list[str] = []
    for p in sorted(work_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(work_dir)
        if any(part in ("node_modules", ".git", ".next") for part in rel.parts):
            continue
        files.append(str(rel))
    return json.dumps({"files": files})


def _read_file(work_dir: Path, path: str) -> str:
    target = (work_dir / path).resolve()
    if not str(target).startswith(str(work_dir)):
        return json.dumps({"error": "path escapes work directory"})
    if not target.exists():
        return json.dumps({"error": f"file not found: {path}"})
    return target.read_text(errors="replace")


def _write_file(work_dir: Path, path: str, content: str) -> str:
    target = (work_dir / path).resolve()
    if not str(target).startswith(str(work_dir)):
        return json.dumps({"error": "path escapes work directory"})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return json.dumps({"ok": True, "path": path, "bytes": len(content)})


def _run_grader(grader_script: str, work_dir: Path, grader_env: dict) -> str:
    env = os.environ.copy()
    env.update(grader_env)
    try:
        proc = subprocess.run(
            ["bash", grader_script, str(work_dir)],
            capture_output=True, text=True, timeout=240, env=env,
        )
        return json.dumps({
            "pass": proc.returncode == 0,
            "stdout": proc.stdout[-3000:],
            "stderr": proc.stderr[-3000:],
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"pass": False, "stdout": "", "stderr": "GRADER TIMEOUT after 240s"})


def _fetch_url(url: str) -> str:
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "docs-eval/0.1"})
        text = r.text
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        truncated = len(text) > 12_000
        return json.dumps({"url": url, "status": r.status_code,
                           "text": text[:12_000], "truncated": truncated})
    except Exception as e:
        return json.dumps({"error": str(e), "url": url})


# ---------------------------------------------------------------------------
# Main server loop
# ---------------------------------------------------------------------------

def _build_tool_schemas(enable_fetch: bool) -> list[dict]:
    tools = [
        {
            "name": "list_files",
            "description": "List all files in the starter app (excluding node_modules). "
                           "Call this first to understand the project structure.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "read_file",
            "description": "Read a file from the work directory.",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string",
                                        "description": "Relative path, e.g. 'src/App.tsx'"}},
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "description": "Write or overwrite a file in the work directory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "run_grader",
            "description": "Typecheck the project and verify required imports/calls. "
                           "Returns pass/fail + compiler errors. Fix errors and call again.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
    ]
    if enable_fetch:
        tools.append({
            "name": "fetch_url",
            "description": "Fetch a documentation URL and return readable plain text.",
            "inputSchema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        })
    return tools


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--grader-script", required=True)
    parser.add_argument("--grader-env", default="{}")
    parser.add_argument("--enable-fetch", action="store_true")
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    grader_env: dict = json.loads(args.grader_env)
    tool_schemas = _build_tool_schemas(args.enable_fetch)

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        if method == "initialize":
            _respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "docs-eval", "version": "1.0"},
            })
        elif method in ("notifications/initialized", "initialized"):
            pass  # notifications don't need a response
        elif method == "ping":
            _respond(req_id, {})
        elif method == "tools/list":
            _respond(req_id, {"tools": tool_schemas})
        elif method == "tools/call":
            name = params.get("name", "")
            tool_args = params.get("arguments", {})
            try:
                if name == "list_files":
                    result = _list_files(work_dir)
                elif name == "read_file":
                    result = _read_file(work_dir, tool_args["path"])
                elif name == "write_file":
                    result = _write_file(work_dir, tool_args["path"], tool_args["content"])
                elif name == "run_grader":
                    result = _run_grader(args.grader_script, work_dir, grader_env)
                elif name == "fetch_url" and args.enable_fetch:
                    result = _fetch_url(tool_args["url"])
                else:
                    result = json.dumps({"error": f"unknown tool: {name}"})
                _tool_result(req_id, result)
            except Exception as e:
                _tool_result(req_id, json.dumps({"error": str(e)}), is_error=True)
        elif req_id is not None:
            _error(req_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    _main()
