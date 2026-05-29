"""The agent runner.

Runs Claude in a loop that resembles how a coding assistant would consume the
docs and produce code. The "agent" here is intentionally simple — we're not
trying to match Cursor or Claude Code feature-for-feature, we're trying to
produce a reproducible signal for "how easy is it for an LLM to use these docs."

Tools exposed to the agent:
- list_files(): list files in the starter app (excluding node_modules)
- read_file(path): read an existing file in the work directory
- write_file(path, content): add or overwrite a file in the work directory
- run_grader(): invoke the grader and get stdout/stderr back
- web_search/web_fetch (mode=web): native Claude web tools
- read_docs(query) (mode=mcp): wrapper around the target's MCP server

The agent loop ends when:
- the grader passes (success)
- max_turns reached (failure: ran out of budget)
- max_seconds reached (failure: timeout)
- agent declares done without passing (failure: gave up)
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

from .user_inputs import provide_user_input

def _is_claude_model(model: str) -> bool:
    return model.startswith("claude")

# AgentMail is optional — only imported when AGENTMAIL_API_KEY is set.
try:
    from agentmail import AgentMail as _AgentMailClient
    _AGENTMAIL_AVAILABLE = True
except ImportError:
    _AGENTMAIL_AVAILABLE = False

from .config import Target, UseCase
from . import llms_txt


# Model choice: Sonnet is the realistic default for coding agents; if you want
# to compare "what would Claude Code likely do" use the same Sonnet model it
# uses. Override via env var so you can A/B test models too.
DEFAULT_MODEL = os.environ.get("DOCS_EVAL_MODEL", "claude-opus-4-7")


@dataclass
class RunResult:
    use_case_id: str
    target_name: str
    mode: str
    run_idx: int
    passed: bool
    pass_at_1: bool                 # did the FIRST grader call pass
    turns: int                       # number of agent iterations
    wall_seconds: float
    final_grader_stdout: str
    final_grader_stderr: str
    failure_category: str | None     # set when passed=False
    transcript_path: Path
    code_dir: Path
    # Useful for diagnostics
    llms_txt_truncated: bool = False
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    # Human review fields — only populated when --human-review is set
    human_review_passed: bool | None = None
    human_review_notes: str = ""
    # Observability counters
    file_reads: int = 0
    file_writes: int = 0
    grader_calls: int = 0
    turns_to_first_grader: int | None = None
    turns_to_success: int | None = None
    # Discovery & capability tracking (populated for all modes)
    discovered_capabilities: dict | None = None
    disclosed_to_agent: bool = False   # True for auto-informed only
    # Resource inventory — all doc URLs the agent accessed
    doc_resources: list = field(default_factory=list)
    # Agent self-report (auto modes only) and mismatch analysis
    agent_self_report: dict | None = None
    self_report_mismatches: list = field(default_factory=list)
    requested_user_inputs: list = field(default_factory=list)
    # Optional qualitative AI review, produced after deterministic grading.
    ai_review: dict | None = None
    ai_review_path: Path | None = None


@dataclass
class RunnerConfig:
    work_root: Path                  # parent of per-cell code dirs
    transcript_root: Path
    model: str = DEFAULT_MODEL
    backend: str = os.environ.get("DOCS_EVAL_BACKEND", "auto")
    verbose: bool = False
    human_review: bool = False       # pause for human inspection after grader passes
    agentmail_api_key: str = field(
        default_factory=lambda: os.environ.get("AGENTMAIL_API_KEY", "")
    )


# --- Tool definitions exposed to the agent ---------------------------------

def _fn_tool(name: str, description: str, params: dict[str, Any]) -> dict[str, Any]:
    """Wrap a tool definition in OpenAI's function-calling format."""
    return {"type": "function", "function": {"name": name, "description": description,
                                              "parameters": params}}


def _build_tools(mode: str, target: Target) -> list[dict[str, Any]]:
    """Tool schemas for the OpenAI chat completions API."""
    tools: list[dict[str, Any]] = [
        _fn_tool("list_files",
                 "List all files in the starter app (excluding node_modules). "
                 "Call this first to understand the existing project structure.",
                 {"type": "object", "properties": {}}),
        _fn_tool("read_file",
                 "Read the contents of an existing file in the work directory.",
                 {"type": "object",
                  "properties": {"path": {"type": "string",
                                          "description": "Relative path, e.g. 'src/components/MintButton.tsx'."}},
                  "required": ["path"]}),
        _fn_tool("write_file",
                 "Write or overwrite a file in the work directory.",
                 {"type": "object",
                  "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                  "required": ["path", "content"]}),
        _fn_tool("run_grader",
                 "Verify the project: typecheck and run automated tests (may include "
                 "an E2E browser test). Returns stdout, stderr, and pass/fail. "
                 "Fix any errors reported and call this again.",
                 {"type": "object", "properties": {}}),
    ]

    if os.environ.get("AGENTMAIL_API_KEY"):
        tools += [
            _fn_tool("create_inbox",
                     "Create a temporary email inbox. Returns an @agentmail.to address. "
                     "Call once per run — subsequent calls return the same inbox.",
                     {"type": "object", "properties": {}}),
            _fn_tool("list_messages",
                     "List the most recent emails in the test inbox.",
                     {"type": "object",
                      "properties": {"limit": {"type": "integer",
                                               "description": "Max messages (default 5)."}}}),
            _fn_tool("get_message",
                     "Fetch the full body of an email by message_id.",
                     {"type": "object",
                      "properties": {"message_id": {"type": "string"}},
                      "required": ["message_id"]}),
        ]

    if mode in ("web", "web-ai-informed", "auto-informed", "auto-blind"):
        tools.append(_fn_tool(
            "fetch_url",
            "Fetch the text content of any URL (documentation page, API reference, etc.). "
            "Use this to read the vendor's docs before writing code.",
            {"type": "object",
             "properties": {"url": {"type": "string", "description": "The URL to fetch."}},
             "required": ["url"]},
        ))

    if mode in ("mcp", "auto-informed") and target.mcp_endpoint:
        tools += [
            _fn_tool("search_docs",
                     f"Search the {target.vendor} documentation. Returns relevant excerpts "
                     "and page titles. Use this first to find relevant pages.",
                     {"type": "object",
                      "properties": {"query": {"type": "string"}},
                      "required": ["query"]}),
            _fn_tool("query_docs_filesystem",
                     f"Run read-only shell commands against a virtual filesystem of the "
                     f"{target.vendor} docs (rg, cat, head, tree, ls, grep). "
                     "Use to read full page content after finding it via search_docs. "
                     "Example: 'head -150 /embedded-wallets/overview.mdx'",
                     {"type": "object",
                      "properties": {"command": {"type": "string",
                                                  "description": "Shell command, e.g. 'head -150 /quickstart.mdx'"}},
                      "required": ["command"]}),
        ]

    return tools


# --- System prompt construction --------------------------------------------

_SELF_REPORT_INSTRUCTION = """
--- SELF-REPORT (fill out when done, whether you succeeded or not) ---
After your final action, output a JSON block exactly like this (no other text around it):
```json
{
  "used_llms_txt": false,
  "used_llms_full_txt": false,
  "used_mcp": false,
  "used_skill_md": false,
  "used_regular_docs": false,
  "used_prior_knowledge": false,
  "most_useful_resource": null,
  "approach_summary": "",
  "steps_taken": [],
  "challenges_faced": [],
  "how_challenges_were_overcome": [],
  "key_apis_used": [],
  "missing_information": [],
  "requested_user_inputs": [],
  "difficult_information": [],
  "resource_urls": []
}
```
Set booleans based on what you actually used. List every doc URL you fetched in resource_urls.
Use approach_summary, steps_taken, challenges_faced, how_challenges_were_overcome,
and key_apis_used to explain how you solved the task.
--- END SELF-REPORT INSTRUCTION ---"""

_USER_INPUT_INSTRUCTION = """
--- USER INPUT REQUESTS ---
If you cannot complete the task without user/project/vendor configuration, stop before
implementing fake values and output a JSON block like this:
```json
{
  "needs_user_input": [
    {"name": "BUNDLER_URL", "why_needed": "ZeroDev bundler RPC URL for this project"},
    {"name": "PAYMASTER_URL", "why_needed": "ZeroDev paymaster RPC URL with sponsorship policy"}
  ]
}
```
For this ZeroDev gas-sponsorship flow, full project-scoped BUNDLER_URL and
PAYMASTER_URL values are sufficient. Ask for ZERODEV_PROJECT_ID only if you
need to construct those URLs yourself or call a project-id-based API.
The benchmark runner will reply as the user if the request is specific enough.
--- END USER INPUT REQUESTS ---"""


def _resource_type_from_url(url: str) -> str:
    """Best-effort resource type for agent-reported documentation URLs."""
    lower = url.lower()
    if "llms-full.txt" in lower:
        return "llms-full.txt"
    if "llms.txt" in lower:
        return "llms.txt"
    if "skill.md" in lower:
        return "skill.md"
    if lower.endswith((".md", ".mdx")):
        return "markdown"
    return "docs"


def _docs_location_hints(target: Target) -> list[str]:
    """Human-style pointers to docs resources without injecting their contents."""
    base = target.base_url.rstrip("/")
    hints = [
        f"Docs live at: {target.base_url}",
        f"LLM-friendly docs index: {base}/llms.txt",
    ]
    if target.llms_txt and target.llms_txt != f"{base}/llms.txt":
        hints.append(f"Full LLM docs bundle: {target.llms_txt}")
    if target.markdown_suffix:
        hints.append(f"Individual docs pages may also be readable with `{target.markdown_suffix}` appended.")
    if target.mcp_endpoint:
        hints.append(f"Docs MCP endpoint, if useful: {target.mcp_endpoint}")
    return hints


def _agent_prompt(
    use_case: UseCase,
    target: Target,
    mode: str,
    tool_list: str,
    llms_txt_content: str | None,
    skill_content: str | None = None,
    capabilities: Any | None = None,
    backend_note: str | None = None,
) -> str:
    parts = [
        "You are a coding agent extending an existing starter app to integrate "
        f"a {target.vendor} SDK feature. The starter app is already scaffolded in the "
        "work directory.",
        "",
        "Task:",
        use_case.prompt,
        "",
        f"Your tools: {tool_list}. Use ONLY these tools and stay inside the scaffolded app.",
        "",
        "Process (stay within budget — do not over-explore):",
        "1. Inspect the project structure and the files most relevant to the task.",
        "2. Use the provided docs context or docs location hints for target-product APIs.",
        "3. Implement the smallest complete solution for the requested workflow.",
        "4. Run the available validation path. Fix errors and rerun validation if needed.",
        "5. When validation passes or you are out of budget, reply with a short summary and the required JSON self-report.",
        "",
        "Rules:",
        "- Use the target docs you're given. Do not invent APIs or guess package names.",
        "- Treat the target docs location as authoritative for this run.",
        "- Write TypeScript with proper types — no `any`, no missing imports.",
        "- Read config/env from existing files; don't hardcode secrets.",
        "- If BUNDLER_URL, PAYMASTER_URL, or any other vendor project/dashboard/paymaster/sponsorship config needed for the chosen implementation is empty or missing, stop and output a JSON block with `needs_user_input` listing the exact missing values and why you need them. For ZeroDev, a separate ZERODEV_PROJECT_ID is only needed if you must construct project-scoped URLs yourself or call project-id-based APIs; full BUNDLER_URL and PAYMASTER_URL values already contain the project context for normal bundler/paymaster transports. Do this before adding fallback runtime errors. Do not invent IDs, admin tokens, paymaster policies, or sponsorship credentials.",
        "- Preserve existing starter code unless the task says to change it.",
        "- Do not edit benchmark runner files, graders, use_cases, or files outside this app.",
    ]
    if backend_note:
        parts += ["", backend_note]

    if mode == "llms-txt" and llms_txt_content:
        parts += [
            "",
            f"--- BEGIN {target.vendor.upper()} DOCS (llms-full.txt) ---",
            llms_txt_content,
            f"--- END {target.vendor.upper()} DOCS ---",
        ]
    elif mode == "web":
        parts += ["", f"Docs live at: {target.base_url}"]
    elif mode == "web-ai-informed":
        parts += [""] + _docs_location_hints(target)
    elif mode == "mcp":
        parts += [
            "",
            f"You have search_docs and query_docs_filesystem to look up {target.vendor} docs.",
        ]
    elif mode == "skill" and skill_content:
        parts += [
            "",
            f"--- BEGIN {target.vendor.upper()} SKILL REFERENCE ---",
            skill_content,
            f"--- END {target.vendor.upper()} SKILL REFERENCE ---",
        ]
    elif mode == "auto-informed" and capabilities is not None:
        llms_url = next(
            (r.url for r in capabilities.resources
             if r.resource_type in ("llms-full.txt", "llms.txt")),
            None,
        )
        hints = [f"Docs live at: {target.base_url}."]
        if llms_url:
            hints.append(f"An llms.txt is also available at {llms_url}.")
        skill_url = next(
            (r.url for r in capabilities.resources if r.resource_type == "skill.md"),
            None,
        )
        if skill_url:
            hints.append(f"A skill.md reference is available at {skill_url}.")
        if capabilities.has_mcp and capabilities.mcp_url:
            hints.append(f"An MCP endpoint is available at {capabilities.mcp_url}.")
        parts += ["", " ".join(hints)]
    elif mode == "auto-blind":
        parts += [""] + _docs_location_hints(target)

    if os.environ.get("AGENTMAIL_API_KEY"):
        parts += [
            "",
            "Email tools: you have create_inbox / list_messages / get_message available.",
            "Use create_inbox to get a real @agentmail.to address for any signup or OTP flow.",
        ]

    parts.append(_USER_INPUT_INSTRUCTION)
    parts.append(_SELF_REPORT_INSTRUCTION)
    return "\n".join(parts)


def _tool_list_for_mode(mode: str, target: Target) -> str:
    return (
        "list_files, read_file, write_file, run_grader"
        + (", fetch_url" if mode in ("web", "web-ai-informed", "auto-informed", "auto-blind") else "")
        + (
            ", search_docs, query_docs_filesystem"
            if mode in ("mcp", "auto-informed") and target.mcp_endpoint
            else ""
        )
    )


def _system_prompt(use_case: UseCase, target: Target, mode: str,
                   llms_txt_content: str | None,
                   skill_content: str | None = None,
                   capabilities: Any | None = None) -> str:
    return _agent_prompt(
        use_case,
        target,
        mode,
        _tool_list_for_mode(mode, target),
        llms_txt_content,
        skill_content,
        capabilities,
    )


# --- Tool execution --------------------------------------------------------

class _AgentState:
    """Mutable state for one agent run."""

    def __init__(self, work_dir: Path, use_case: UseCase, target: Target,
                 transcript_fp, agentmail_client: Any | None = None):
        self.work_dir = work_dir
        self.use_case = use_case
        self.target = target
        self.transcript_fp = transcript_fp
        self.grader_calls = 0
        self.first_grader_pass: bool | None = None
        self.last_stdout = ""
        self.last_stderr = ""
        self.last_pass: bool = False
        self.last_human_review: dict[str, Any] | None = None
        # AgentMail: one client shared across all tool calls in this run.
        self.agentmail_client: Any | None = agentmail_client
        self.agentmail_inbox_id: str | None = None
        # Observability counters
        self.file_reads: int = 0
        self.file_writes: int = 0
        self.turns_to_first_grader: int | None = None   # turn# of first run_grader call
        self.turns_to_success: int | None = None         # turn# when grader first passed
        # Resource inventory: url -> {url, resource_type, access_method, times_accessed}
        self.doc_resource_inventory: dict[str, dict[str, Any]] = {}
        self.requested_user_inputs: list[dict[str, Any]] = []
        # Raw text of the last assistant message (for self-report extraction)
        self.last_assistant_text: str = ""

    def log(self, kind: str, data: Any) -> None:
        self.transcript_fp.write(json.dumps({"kind": kind, "data": data}) + "\n")
        self.transcript_fp.flush()

    def track_resource(self, url: str, resource_type: str, access_method: str) -> None:
        """Record a documentation resource access."""
        if url not in self.doc_resource_inventory:
            self.doc_resource_inventory[url] = {
                "url": url,
                "resource_type": resource_type,
                "access_method": access_method,
                "times_accessed": 0,
            }
        self.doc_resource_inventory[url]["times_accessed"] += 1
        self.log("resource_access", {
            "url": url, "resource_type": resource_type,
            "access_method": access_method,
        })


def _run_grader(state: _AgentState) -> dict[str, Any]:
    """Invoke the grader script. Returns dict the agent will see."""
    grader_cfg = state.use_case.grader
    run_script = Path(grader_cfg["run"])
    if not run_script.is_absolute():
        # Resolve relative to the use case's source file's project root.
        # We assume use_case.source_path.parents[2] is the project root
        # (use_cases/<vendor>/foo.yaml -> project root).
        project_root = state.use_case.source_path.parents[2]
        run_script = project_root / run_script

    # Merge any env vars declared in the grader config (e.g. REQUIRE_GAS_SPONSORED)
    grader_env = os.environ.copy()
    grader_env.update({k: str(v) for k, v in grader_cfg.get("env", {}).items()})

    # E2E tests (Playwright + dev server) can take longer than a plain tsc run
    grader_type = grader_cfg.get("type", "compile")
    grader_timeout = 180 if grader_type == "e2e" else 120

    state.grader_calls += 1
    try:
        proc = subprocess.run(
            ["bash", str(run_script), str(state.work_dir)],
            capture_output=True, text=True, timeout=grader_timeout,
            env=grader_env,
        )
        passed = proc.returncode == 0
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        passed = False
        stdout = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = f"GRADER TIMEOUT after {grader_timeout}s\n" + (
            (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        )

    # If tsc passed, also verify expected imports and calls are present in source.
    # This catches "agent gave up and left the scaffold unchanged" false passes.
    if passed:
        code_files = [
            p for p in state.work_dir.rglob("*.ts")
            if "node_modules" not in p.parts
        ] + [
            p for p in state.work_dir.rglob("*.tsx")
            if "node_modules" not in p.parts
        ]
        code = "\n".join(p.read_text(errors="replace") for p in code_files)
        expected = state.use_case.expected
        missing_imports = [i for i in expected.get("imports", []) if i not in code]
        missing_calls   = [c for c in expected.get("calls",   []) if c not in code]
        if missing_imports or missing_calls:
            passed = False
            check_msg = ""
            if missing_imports:
                check_msg += f"Missing expected imports: {missing_imports}\n"
            if missing_calls:
                check_msg += f"Missing expected calls: {missing_calls}\n"
            stdout = check_msg + stdout
            stderr = stderr

    state.last_pass = passed
    state.last_stdout = stdout
    state.last_stderr = stderr
    if state.first_grader_pass is None:
        state.first_grader_pass = passed

    state.log("grader", {"pass": passed, "stdout": stdout[-4000:], "stderr": stderr[-4000:],
                          "call_number": state.grader_calls})
    return {
        "pass": passed,
        # Truncate so we don't fill the agent's context with a 50-line tsc dump
        "stdout": stdout[-3000:],
        "stderr": stderr[-3000:],
    }


def _write_file(state: _AgentState, path: str, content: str) -> dict[str, Any]:
    # Block path traversal — agent stays in its work dir.
    target = (state.work_dir / path).resolve()
    if not str(target).startswith(str(state.work_dir.resolve())):
        return {"error": "path escapes work directory"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    state.file_writes += 1
    state.log("write_file", {"path": path, "bytes": len(content)})
    return {"ok": True, "path": path, "bytes": len(content)}


def _create_inbox(state: _AgentState) -> dict[str, Any]:
    if state.agentmail_client is None:
        return {"error": "AgentMail not configured (AGENTMAIL_API_KEY not set)"}
    if state.agentmail_inbox_id:
        return {"email": state.agentmail_inbox_id}
    try:
        inbox = state.agentmail_client.inboxes.create()
        # inbox_id is already the full address (e.g. foo@agentmail.to)
        state.agentmail_inbox_id = inbox.inbox_id
        state.log("agentmail_create_inbox", {"email": inbox.inbox_id})
        return {"email": inbox.inbox_id}
    except Exception as e:
        return {"error": str(e)}


def _list_messages(state: _AgentState, limit: int = 5) -> dict[str, Any]:
    if state.agentmail_client is None:
        return {"error": "AgentMail not configured"}
    if not state.agentmail_inbox_id:
        return {"error": "No inbox — call create_inbox first"}
    try:
        msgs = state.agentmail_client.inboxes.messages.list(
            state.agentmail_inbox_id, limit=limit
        )
        items = [
            {
                "message_id": m.message_id,
                "from": m.from_,
                "subject": m.subject,
                "received_at": str(m.received_at),
            }
            for m in (msgs.messages or [])
        ]
        state.log("agentmail_list_messages", {"count": len(items)})
        return {"messages": items}
    except Exception as e:
        return {"error": str(e)}


def _get_message(state: _AgentState, message_id: str) -> dict[str, Any]:
    if state.agentmail_client is None:
        return {"error": "AgentMail not configured"}
    if not state.agentmail_inbox_id:
        return {"error": "No inbox — call create_inbox first"}
    try:
        msg = state.agentmail_client.inboxes.messages.get(
            state.agentmail_inbox_id, message_id
        )
        state.log("agentmail_get_message", {"message_id": message_id})
        return {
            "message_id": msg.message_id,
            "from": msg.from_,
            "subject": msg.subject,
            "text": msg.text or "",
            "html": msg.html or "",
        }
    except Exception as e:
        return {"error": str(e)}


def _classify_url(url: str) -> str:
    """Classify a fetched URL into a resource type for the inventory."""
    low = url.lower()
    if "llms-full.txt" in low:
        return "llms-full.txt"
    if "llms.txt" in low:
        return "llms.txt"
    if "skill.md" in low:
        return "skill.md"
    if low.endswith(".mdx"):
        return "mdx-page"
    if low.endswith(".md"):
        return "markdown-page"
    return "docs-page"


def _fetch_url(state: _AgentState, url: str) -> dict[str, Any]:
    """Fetch a URL and return plain text (strips HTML tags)."""
    state.log("fetch_url", {"url": url})
    state.track_resource(url, _classify_url(url), "fetch_url")
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "docs-eval/0.1"})
        text = r.text
        # Strip HTML tags roughly so the model gets readable text
        import re
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        # Cap at ~12k chars so it doesn't flood context
        truncated = len(text) > 12000
        return {"url": url, "status": r.status_code, "text": text[:12000],
                "truncated": truncated}
    except Exception as e:
        return {"error": str(e), "url": url}


_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
_mcp_req_id = 0


def _mcp_call(endpoint: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Send one JSON-RPC request to an MCP HTTP+SSE endpoint and return the result."""
    global _mcp_req_id
    _mcp_req_id += 1
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": _mcp_req_id}
    r = httpx.post(endpoint, headers=_MCP_HEADERS, json=payload, timeout=30,
                   follow_redirects=True)
    r.raise_for_status()
    # Response is SSE: parse `data: {...}` lines
    for line in r.text.splitlines():
        if line.startswith("data:"):
            obj = json.loads(line[len("data:"):].strip())
            if "result" in obj:
                return obj["result"]
            if "error" in obj:
                return {"error": obj["error"]}
    return {"error": "no data in MCP response", "raw": r.text[:500]}


def _mcp_tool_names(target: Target) -> tuple[str, str]:
    """Return the actual tool names the MCP server uses for search and filesystem."""
    # Mintlify namespaces tools as search_<slug> and query_docs_filesystem_<slug>.
    # mcp_tool_slug overrides the default vendor-derived slug.
    if target.mcp_tool_slug:
        slug = target.mcp_tool_slug
    else:
        slug = target.vendor.lower().replace("-", "_") + "_docs"
    return f"search_{slug}", f"query_docs_filesystem_{slug}"


def _claude_mcp_server_args(
    use_case: UseCase,
    target: Target,
    mode: str,
    work_dir: Path,
) -> list[str]:
    grader_cfg = use_case.grader
    run_script = Path(grader_cfg["run"])
    if not run_script.is_absolute():
        project_root = use_case.source_path.parents[2]
        run_script = (project_root / run_script).resolve()  # must be absolute: subprocess runs from HOME
    grader_env_extra = {k: str(v) for k, v in grader_cfg.get("env", {}).items()}

    args = [
        "-m", "docs_eval.mcp_server",
        "--work-dir", str(work_dir.resolve()),
        "--grader-script", str(run_script),
        "--grader-env", json.dumps(grader_env_extra),
    ]
    if mode in ("web", "web-ai-informed", "auto-informed", "auto-blind"):
        args.append("--enable-fetch")
    return args


def _read_docs_mcp(state: _AgentState, query: str) -> dict[str, Any]:
    """Search the MCP server for relevant doc chunks."""
    state.log("search_docs", {"query": query})
    if state.target.mcp_endpoint:
        state.track_resource(state.target.mcp_endpoint, "mcp", "search_docs")
    endpoint = state.target.mcp_endpoint
    if not endpoint:
        return {"error": "no MCP endpoint configured for this target"}
    search_tool, _ = _mcp_tool_names(state.target)
    try:
        return _mcp_call(endpoint, "tools/call",
                         {"name": search_tool, "arguments": {"query": query}})
    except Exception as e:
        return {"error": str(e)}


def _query_docs_filesystem_mcp(state: _AgentState, command: str) -> dict[str, Any]:
    """Run a shell command against the MCP virtual docs filesystem."""
    state.log("query_docs_filesystem", {"command": command})
    if state.target.mcp_endpoint:
        state.track_resource(state.target.mcp_endpoint, "mcp", "query_docs_filesystem")
    endpoint = state.target.mcp_endpoint
    if not endpoint:
        return {"error": "no MCP endpoint configured for this target"}
    _, fs_tool = _mcp_tool_names(state.target)
    try:
        return _mcp_call(endpoint, "tools/call",
                         {"name": fs_tool, "arguments": {"command": command}})
    except Exception as e:
        return {"error": str(e)}


def _list_files(state: _AgentState) -> dict[str, Any]:
    """Return all project files, excluding node_modules and .git."""
    files: list[str] = []
    for p in sorted(state.work_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(state.work_dir)
        if any(part in ("node_modules", ".git", ".next") for part in rel.parts):
            continue
        files.append(str(rel))
    state.log("list_files", {"count": len(files), "files": files})
    return {"files": files}


def _read_file(state: _AgentState, path: str) -> dict[str, Any]:
    """Read a file from the work directory."""
    target = (state.work_dir / path).resolve()
    if not str(target).startswith(str(state.work_dir.resolve())):
        return {"error": "path escapes work directory"}
    if not target.exists():
        return {"error": f"file not found: {path}"}
    content = target.read_text()
    state.file_reads += 1
    state.log("read_file", {"path": path, "bytes": len(content)})
    return {"path": path, "content": content}


def _handle_tool(state: _AgentState, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "list_files":
        return _list_files(state)
    if name == "read_file":
        return _read_file(state, args["path"])
    if name == "write_file":
        return _write_file(state, args["path"], args["content"])
    if name == "run_grader":
        return _run_grader(state)
    if name == "fetch_url":
        return _fetch_url(state, args["url"])
    if name == "search_docs":
        return _read_docs_mcp(state, args["query"])
    if name == "query_docs_filesystem":
        return _query_docs_filesystem_mcp(state, args["command"])
    if name == "create_inbox":
        return _create_inbox(state)
    if name == "list_messages":
        return _list_messages(state, int(args.get("limit", 5)))
    if name == "get_message":
        return _get_message(state, args["message_id"])
    return {"error": f"unknown tool: {name}"}


# --- Helpers ---------------------------------------------------------------

def _summarize_tool_result(tool_name: str, result: dict[str, Any]) -> str:
    """One-line summary of a tool result for the transcript (keeps logs scannable)."""
    if tool_name == "list_files":
        # mcp_server returns {"files": [...]}
        files = result.get("files", [])
        return f"{len(files)} files"
    if tool_name == "read_file":
        # mcp_server returns raw text (not JSON), so result here is {"raw": text[:200]}
        raw = result.get("raw", "")
        return f"{len(raw)} chars — {result.get('path', '')}"
    if tool_name == "write_file":
        return f"{result.get('bytes', 0)} bytes written — {result.get('path', '')}"
    if tool_name == "run_grader":
        return "pass" if result.get("pass") else "fail"
    if tool_name == "read_docs":
        return f"query: {result.get('query', '')[:60]}"
    return str(result)[:80]


# --- Human review ----------------------------------------------------------

def _human_review(state: "_AgentState", use_case: "UseCase",
                  cfg: "RunnerConfig") -> tuple[bool | None, str]:
    """Start the app, open the browser, and ask the human if it works.

    Returns (passed, notes).  Skips gracefully if human_check is not defined.
    """
    hc = use_case.human_check
    if not hc:
        return True, "no human_check defined — skipped"

    start_cmd: str = hc.get("start_command", "")
    url: str = hc.get("url", "")
    checklist: list[str] = hc.get("checklist", [])
    what_to_do: str = hc.get("what_to_do", "")

    state.log("human_review_start", {
        "url": url,
        "start_command": start_cmd,
        "checklist": checklist,
    })

    dev_proc = None
    if start_cmd:
        print(f"\n  [human-review] Starting app: {start_cmd}")
        try:
            dev_proc = subprocess.Popen(
                start_cmd, shell=True, cwd=state.work_dir,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            # Brief pause so the dev server can bind its port
            time.sleep(4)
        except Exception as e:
            print(f"  [human-review] WARNING: could not start app: {e}")

    if url:
        print(f"  [human-review] Opening {url}")
        webbrowser.open(url)

    # Print review instructions
    print("\n" + "─" * 60)
    print(f"  HUMAN REVIEW  —  {use_case.id}")
    print("─" * 60)
    if what_to_do:
        print(f"\n  What to do:\n  {what_to_do}\n")
    if checklist:
        print("  Checklist:")
        for item in checklist:
            print(f"    [ ] {item}")

    # Show the files the agent wrote so the reviewer can also read the code
    project_files = [
        str(p.relative_to(state.work_dir))
        for p in sorted(state.work_dir.rglob("*"))
        if p.is_file() and "node_modules" not in p.parts
        and p.suffix in (".ts", ".tsx", ".js", ".jsx")
    ]
    if project_files:
        print(f"\n  Files to inspect: {', '.join(project_files[:8])}")
        if len(project_files) > 8:
            print(f"    ... and {len(project_files) - 8} more in {state.work_dir}")

    print(f"\n  Code is at: {state.work_dir}")
    print("─" * 60)

    # Ask for confirmation
    while True:
        try:
            raw = input("\n  Did the feature work? [y / n / s(kip)] ").strip().lower()
        except EOFError:
            # Non-interactive context — auto-pass with a note
            print("  (non-interactive — auto-passing human review)")
            passed: bool | None = True
            notes = "non-interactive"
            break
        if raw in ("y", "yes"):
            passed = True
            notes = input("  Optional notes (press Enter to skip): ").strip()
            break
        if raw in ("n", "no"):
            passed = False
            notes = input("  What failed? ").strip()
            break
        if raw in ("s", "skip"):
            passed = None
            notes = "skipped"
            break
        print("  Please type y, n, or s.")

    if dev_proc is not None:
        dev_proc.terminate()
        try:
            dev_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            dev_proc.kill()

    state.log("human_review_result", {"passed": passed, "notes": notes})
    return passed, notes


# --- Self-report extraction and mismatch detection -------------------------

def _extract_self_report(text: str) -> dict[str, Any] | None:
    """Parse a JSON self-report block from assistant text output."""
    import re
    # Try ```json ... ``` first (structured block)
    for block in reversed(re.findall(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)):
        try:
            d = json.loads(block)
            if "used_llms_txt" in d or "resource_urls" in d:
                return d
        except (json.JSONDecodeError, ValueError):
            pass
    # Fallback: any JSON object with a self-report key
    for block in reversed(re.findall(r'\{[^{}]{20,}\}', text, re.DOTALL)):
        try:
            d = json.loads(block)
            if "used_llms_txt" in d or "resource_urls" in d:
                return d
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _extract_user_input_request(text: str) -> dict[str, Any] | None:
    """Parse an agent JSON block asking the mock user for values."""
    import re

    candidates: list[str] = []
    candidates.extend(re.findall(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL))
    candidates.extend(re.findall(r'\{[^{}]*"needs_user_input"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL))

    for block in reversed(candidates):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        requested = data.get("needs_user_input")
        if isinstance(requested, list) and requested:
            return {
                "reason": str(data.get("reason", "Agent requested missing user configuration.")),
                "requested_values": requested,
            }
    return None


def _infer_user_input_request(text: str) -> dict[str, Any] | None:
    """Infer a mock-user request when the agent reports missing config in prose."""
    upper = text.upper()
    requested: list[dict[str, str]] = []
    for name, why in (
        (
            "ZERODEV_PROJECT_ID",
            "ZeroDev project identifier, only needed when deriving project RPC endpoints or calling project-id APIs",
        ),
        ("BUNDLER_URL", "ZeroDev bundler RPC URL for sponsored user operations"),
        ("PAYMASTER_URL", "ZeroDev paymaster RPC URL with sponsorship policy"),
    ):
        if name in upper:
            requested.append({"name": name, "why_needed": why})
    if not requested:
        return None
    return {
        "reason": "Agent reported missing ZeroDev configuration.",
        "requested_values": requested,
    }


def _mock_user_reply(state: _AgentState, request: dict[str, Any]) -> str:
    """Return a user-message string with deterministic benchmark values."""
    response = provide_user_input(request)
    entry = {"request": request, "response": response}
    state.requested_user_inputs.append(entry)
    state.log("mock_user_input", entry)
    return (
        "Mock user response: here are the requested values from my project/config.\n"
        "Use these values exactly where appropriate, do not invent additional credentials.\n"
        "```json\n"
        f"{json.dumps(response, indent=2)}\n"
        "```"
    )


def _detect_mismatches(
    state: _AgentState, report: dict[str, Any]
) -> list[str]:
    """Compare agent self-report against observed tool logs. Return discrepancies."""
    mismatches: list[str] = []
    observed = state.doc_resource_inventory

    def _any_url_matches(keyword: str) -> bool:
        return any(keyword in u.lower() for u in observed)

    def _any_method(method: str) -> bool:
        return any(r["access_method"] == method for r in observed.values())

    checks = [
        ("used_llms_full_txt", lambda: _any_url_matches("llms-full"),
         "llms-full.txt fetch"),
        ("used_llms_txt", lambda: _any_url_matches("llms.txt"),
         "llms.txt fetch"),
        ("used_skill_md", lambda: _any_url_matches("skill.md"),
         "skill.md fetch"),
        ("used_mcp",
         lambda: _any_method("search_docs") or _any_method("query_docs_filesystem"),
         "MCP tool call"),
    ]
    for key, observed_fn, label in checks:
        claimed = bool(report.get(key))
        saw = observed_fn()
        if claimed and not saw:
            mismatches.append(f"claimed {key}=true but no {label} observed")
        elif not claimed and saw:
            mismatches.append(f"claimed {key}=false but {label} was observed")

    reported_urls = set(report.get("resource_urls") or [])
    observed_urls = set(observed.keys())
    extra = reported_urls - observed_urls
    missed = observed_urls - reported_urls
    if extra:
        mismatches.append(f"reported URLs not observed in tool logs: {sorted(extra)}")
    if missed:
        mismatches.append(f"observed URLs not in agent's resource_urls: {sorted(missed)}")

    return mismatches


# --- Provider-specific agent loops -----------------------------------------
#
# We have two backends:
#
#   _run_loop_openai  — uses the OpenAI Python SDK directly (needs CHAT_GPT_API_KEY)
#   _run_loop_claude  — uses the `claude -p` CLI subprocess via MCP
#
# The Claude path exists because we don't have a direct Anthropic API key; we
# rely on the Claude Code CLI's existing authentication instead. The MCP server
# (mcp_server.py) runs as a stdio subprocess and exposes the same tools
# (list_files, read_file, write_file, run_grader, fetch_url) that the OpenAI
# path implements inline. Claude's internal tool loop handles multi-turn; we
# parse its --output-format stream-json transcript afterward.


def _run_loop_openai(
    state: _AgentState,
    use_case: "UseCase",
    target: "Target",
    mode: str,
    system: str,
    tools: list[dict[str, Any]],
    cfg: "RunnerConfig",
) -> tuple[int, int, int]:
    """Run the agent loop using the OpenAI chat completions API.

    Returns (turns, total_input_tokens, total_output_tokens).
    """
    client = OpenAI(
        api_key=os.environ.get("CHAT_GPT_API_KEY") or os.environ.get("OPENAI_API_KEY")
    )
    if cfg.verbose:
        print("  [prompt] BEGIN OpenAI prompt", flush=True)
        print(system, flush=True)
        print("  [prompt] END OpenAI prompt", flush=True)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Start the task."},
    ]

    total_in = total_out = 0
    turns = 0
    mock_user_rounds = 0
    max_mock_user_rounds = int(os.environ.get("DOCS_EVAL_MOCK_USER_ROUNDS", "1"))
    start = time.time()
    deadline = start + use_case.max_seconds

    for turn in range(use_case.max_turns):
        turns = turn + 1
        elapsed_so_far = time.time() - start
        if time.time() > deadline:
            state.log("timeout", {"after_turn": turn, "elapsed_seconds": elapsed_so_far})
            if cfg.verbose:
                print(f"  [agent] TIMEOUT after turn {turn} ({elapsed_so_far:.0f}s)")
            break

        if cfg.verbose:
            print(f"  [agent] turn {turns}/{use_case.max_turns} "
                  f"({elapsed_so_far:.0f}s elapsed, "
                  f"{total_in:,}in/{total_out:,}out tokens so far)")

        _newer_api = any(cfg.model.startswith(p) for p in ("gpt-5", "o1", "o3", "o4"))
        _token_kwarg = "max_completion_tokens" if _newer_api else "max_tokens"

        for _retry in range(5):
            try:
                resp = client.chat.completions.create(
                    model=cfg.model,
                    **{_token_kwarg: 4096},
                    tools=tools or None,
                    messages=messages,
                )
                break
            except Exception as _e:
                if "rate_limit" in str(_e).lower() or "429" in str(_e):
                    wait = 20 * (_retry + 1)
                    if cfg.verbose:
                        print(f"  [agent]   rate limit — waiting {wait}s")
                    time.sleep(wait)
                else:
                    raise
        else:
            raise RuntimeError("rate limit retries exhausted")

        msg = resp.choices[0].message
        finish_reason = resp.choices[0].finish_reason
        total_in += resp.usage.prompt_tokens
        total_out += resp.usage.completion_tokens

        assistant_entry: dict[str, Any] = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        state.log("assistant", {
            "turn": turns,
            "stop_reason": finish_reason,
            "input_tokens": resp.usage.prompt_tokens,
            "output_tokens": resp.usage.completion_tokens,
            "content": msg.content,
            "tool_calls": [
                {"name": tc.function.name, "arguments": tc.function.arguments}
                for tc in (msg.tool_calls or [])
            ],
        })

        tool_calls = msg.tool_calls or []
        if msg.content:
            state.last_assistant_text = msg.content
        if cfg.verbose and tool_calls:
            print(f"  [agent]   tools: {[tc.function.name for tc in tool_calls]}")

        if finish_reason == "stop" and not tool_calls:
            if msg.content and mock_user_rounds < max_mock_user_rounds:
                request = (
                    _extract_user_input_request(msg.content)
                    or _infer_user_input_request(msg.content)
                )
                if request:
                    mock_user_rounds += 1
                    reply = _mock_user_reply(state, request)
                    messages.append({"role": "user", "content": reply})
                    if cfg.verbose:
                        print(f"  [mock-user] replied to request {mock_user_rounds}/{max_mock_user_rounds}", flush=True)
                        print(reply, flush=True)
                    continue
            if state.first_grader_pass is None:
                state.log("note", "agent stopped without grading; running grader")
                if cfg.verbose:
                    print("  [agent]   stopped without grading — running grader now")
                _run_grader(state)
            break

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            result = _handle_tool(state, name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })
            state.log("tool_result", {
                "turn": turns,
                "tool": name,
                "result_summary": _summarize_tool_result(name, result),
            })
            if name == "run_grader":
                if state.turns_to_first_grader is None:
                    state.turns_to_first_grader = turns
                if result.get("pass") and state.turns_to_success is None:
                    state.turns_to_success = turns
            if cfg.verbose and name == "run_grader":
                status = "PASS" if result.get("pass") else "FAIL"
                print(f"  [grader]  {status}")
                if not result.get("pass") and result.get("stderr"):
                    for line in result["stderr"].strip().splitlines()[:5]:
                        print(f"            {line}")

        if state.last_pass:
            if cfg.verbose:
                print(f"  [agent]   grader passed — done in {turns} turn(s)")
            break

    return turns, total_in, total_out


def _run_loop_claude(
    state: _AgentState,
    use_case: "UseCase",
    target: "Target",
    mode: str,
    system: str,
    tools: list[dict[str, Any]],
    cfg: "RunnerConfig",
    work_dir: Path,
    setup_elapsed: float,
) -> tuple[int, int, int]:
    """Run the agent loop via the `claude -p` CLI subprocess.

    We cannot call the Anthropic API directly because we don't have a raw API
    key — instead we rely on the Claude Code CLI's existing authentication.
    The tool loop runs inside `claude` itself; we supply custom tools via an
    MCP server (mcp_server.py) started as a stdio subprocess.

    Returns (turns, total_input_tokens, total_output_tokens).
    """
    import shutil
    import tempfile
    import sys

    if shutil.which("claude") is None:
        raise RuntimeError(
            "claude CLI not found in PATH — install Claude Code: https://claude.ai/code"
        )

    # Build the MCP server invocation.  We launch it as a stdio subprocess so
    # Claude Code manages the lifecycle; each cell gets its own server instance
    # scoped to that cell's work directory.
    mcp_server_args = [sys.executable] + _claude_mcp_server_args(
        use_case, target, mode, work_dir
    )

    mcp_config = {
        "mcpServers": {
            "docs-eval": {
                "type": "stdio",
                "command": mcp_server_args[0],
                "args": mcp_server_args[1:],
            }
        }
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix="docs_eval_mcp_"
    ) as f:
        json.dump(mcp_config, f)
        mcp_config_path = f.name

    # Claude -p takes one prompt; the shared agent prompt already includes the
    # task, docs context/hints, rules, and self-report instructions.
    full_prompt = system
    if cfg.verbose:
        print("  [prompt] BEGIN Claude prompt", flush=True)
        print(full_prompt, flush=True)
        print("  [prompt] END Claude prompt", flush=True)

    allowed_mcp = (
        "mcp__docs-eval__list_files,mcp__docs-eval__read_file,"
        "mcp__docs-eval__write_file,mcp__docs-eval__run_grader"
        + (",mcp__docs-eval__fetch_url" if mode in ("web", "web-ai-informed", "auto-informed", "auto-blind") else "")
        + (
            ",mcp__docs-eval__search_docs,mcp__docs-eval__query_docs_filesystem"
            if mode in ("mcp", "auto-informed") and target.mcp_endpoint
            else ""
        )
    )
    # Block native claude-code tools so the agent is isolated to the MCP sandbox.
    # Critically: block Skill so the agent can't use session skills (e.g. the
    # built-in zerodev skill) — we want to measure what the docs alone provide,
    # not what the LLM already knows via pre-loaded skill references.
    _native_blocked = "Read,Write,Edit,MultiEdit,Bash,Glob,Grep,LS,TodoRead,TodoWrite,NotebookRead,NotebookEdit,Skill"
    cmd = [
        "claude", "-p", full_prompt,
        "--mcp-config", mcp_config_path,
        "--output-format", "stream-json",
        "--max-turns", str(use_case.max_turns + 3),  # +3 absorbs ToolSearch internal overhead
        "--model", cfg.model,
        "--allowedTools", allowed_mcp,
        "--disallowedTools", _native_blocked,
        "--verbose",  # required by claude CLI when using --output-format stream-json
    ]

    if cfg.verbose:
        print(f"  [claude-cli] running: claude -p ... --model {cfg.model}")

    # Run from HOME, not the docs-eval project root. If we ran from the project
    # root, claude would find the project git repo and inject project-specific
    # memory (our debug notes about ZeroDev APIs, root causes, etc.) into the
    # agent's context — contaminating the doc-quality signal.
    # Running from HOME keeps auth (keychain) working while avoiding project memory.
    _cwd = Path.home()

    start = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=_cwd,
            timeout=use_case.max_seconds + 30,  # slight buffer over agent budget
        )
        raw_output = proc.stdout
    except subprocess.TimeoutExpired as e:
        raw_output = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        if cfg.verbose:
            print(f"  [claude-cli] TIMEOUT after {use_case.max_seconds}s")
    finally:
        try:
            os.unlink(mcp_config_path)
        except OSError:
            pass

    # Parse the stream-json transcript.
    #
    # Observed format from `claude --output-format stream-json --verbose`:
    #   {"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_xxx",
    #     "name":"mcp__docs-eval__run_grader","input":{}}],"usage":{"input_tokens":N,...}}}
    #   {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_xxx",
    #     "content":[{"type":"text","text":"{\"pass\":false,...}"}]}]}}
    #   {"type":"result","num_turns":N,"usage":{"input_tokens":N,"output_tokens":N,...}}
    #
    # Key gotcha: tool_result "content" is a list of text blocks, not a plain string.
    turns = 0
    total_in = total_out = 0
    # Map tool_use_id -> short tool name so we can label tool results correctly.
    tool_id_to_name: dict[str, str] = {}

    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = event.get("type")

        if etype == "assistant":
            elapsed = time.time() - start
            msg = event.get("message", {})
            usage = msg.get("usage", {})
            # input_tokens is just the new tokens; cache tokens are tracked separately.
            in_tok = (usage.get("input_tokens", 0)
                      + usage.get("cache_creation_input_tokens", 0)
                      + usage.get("cache_read_input_tokens", 0))
            out_tok = usage.get("output_tokens", 0)
            total_in += in_tok
            total_out += out_tok

            content = msg.get("content", [])
            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            text_block = next((b.get("text") for b in content if b.get("type") == "text"), None)

            # Build the id→name map for this turn's tool calls.
            short_names = []
            for tu in tool_uses:
                tid = tu.get("id", "")
                short = tu.get("name", "").replace("mcp__docs-eval__", "")
                tool_id_to_name[tid] = short
                short_names.append(short)
                # Track observability counters from tool call inputs (Claude path).
                # Tools execute inside the MCP subprocess so we can't intercept them
                # via _handle_tool — we infer them from the stream-json events instead.
                if short == "fetch_url":
                    url_arg = tu.get("input", {}).get("url", "")
                    if url_arg:
                        state.track_resource(url_arg, _classify_url(url_arg), "fetch_url")
                elif short == "read_file":
                    state.file_reads += 1
                elif short == "write_file":
                    state.file_writes += 1
                elif short in ("search_docs", "query_docs_filesystem"):
                    if state.target.mcp_endpoint:
                        state.track_resource(
                            state.target.mcp_endpoint, "mcp", short
                        )

            # Skip internal-only turns (ToolSearch / deferred-tool loader) —
            # they don't represent real agent work and shouldn't burn the budget.
            _internal = {"ToolSearch", "WebSearch", "WebFetch"}
            real_tools = [n for n in short_names if n not in _internal]
            if real_tools or text_block:
                turns += 1
                if text_block:
                    state.last_assistant_text = text_block
                    # Detect and log needs_user_input blocks (observability only —
                    # the Claude CLI is a single subprocess so we can't inject a
                    # mid-run reply the way the OpenAI/Codex paths do).
                    _user_req = (
                        _extract_user_input_request(text_block)
                        or _infer_user_input_request(text_block)
                    )
                    if _user_req:
                        _mock_user_reply(state, _user_req)
                state.log("assistant", {
                    "turn": turns,
                    "stop_reason": msg.get("stop_reason"),
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "content": text_block,
                    "tool_calls": [
                        {"name": tu.get("name", ""),
                         "arguments": json.dumps(tu.get("input", {}))}
                        for tu in tool_uses
                    ],
                })
                if cfg.verbose:
                    print(f"  [agent] turn {turns}/{use_case.max_turns} "
                          f"({elapsed:.0f}s elapsed, "
                          f"{total_in:,}in/{total_out:,}out tokens so far)")
                    if text_block:
                        # Show first line of agent reasoning so we can follow its thinking
                        first_line = text_block.strip().splitlines()[0][:120]
                        print(f"  [agent]   → {first_line}")
                    for tu in tool_uses:
                        short = tu.get("name", "").replace("mcp__docs-eval__", "")
                        inp = tu.get("input", {})
                        arg = inp.get("path") or inp.get("url") or ""
                        suffix = f" {arg}" if arg else ""
                        print(f"  [agent]   {short}{suffix}")

        elif etype == "user":
            # Tool results fed back as user messages.
            content = event.get("message", {}).get("content", [])
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                tool_id = block.get("tool_use_id", "")
                tool_name = tool_id_to_name.get(tool_id, tool_id)

                # content is a list of text blocks: [{"type":"text","text":"..."}]
                raw_content = block.get("content", "")
                if isinstance(raw_content, list):
                    result_text = next(
                        (b.get("text", "") for b in raw_content if b.get("type") == "text"), ""
                    )
                else:
                    result_text = str(raw_content)

                try:
                    result: Any = json.loads(result_text)
                except (json.JSONDecodeError, TypeError):
                    result = {"raw": result_text[:200]}

                state.log("tool_result", {
                    "turn": turns,
                    "tool": tool_name,
                    "result_summary": _summarize_tool_result(tool_name, result)
                                      if isinstance(result, dict) else result_text[:80],
                })

                if isinstance(result, dict) and "pass" in result:
                    grader_pass = bool(result["pass"])
                    state.last_pass = grader_pass
                    state.last_stdout = result.get("stdout", "")
                    state.last_stderr = result.get("stderr", "")
                    if state.first_grader_pass is None:
                        state.first_grader_pass = grader_pass
                        state.turns_to_first_grader = turns
                    if grader_pass and state.turns_to_success is None:
                        state.turns_to_success = turns
                    state.grader_calls += 1
                    if cfg.verbose:
                        print(f"  [grader]  {'PASS' if grader_pass else 'FAIL'}")
                        if not grader_pass and result.get("stderr"):
                            for ln in result["stderr"].strip().splitlines()[:5]:
                                print(f"            {ln}")

        elif etype == "result":
            # Final summary event — use num_turns as authoritative turn count
            # and pull aggregate token usage if available.
            num_turns = event.get("num_turns")
            if num_turns is not None:
                turns = num_turns
            agg = event.get("usage", {})
            if agg.get("input_tokens") or agg.get("output_tokens"):
                total_in = (agg.get("input_tokens", 0)
                            + agg.get("cache_creation_input_tokens", 0)
                            + agg.get("cache_read_input_tokens", 0))
                total_out = agg.get("output_tokens", 0)

    # Log claude CLI stderr if any (MCP startup errors, auth issues, etc.)
    if 'proc' in dir() and proc.stderr:
        state.log("claude_cli_stderr", proc.stderr[:2000])

    return turns, total_in, total_out


def _run_loop_codex(
    state: _AgentState,
    use_case: "UseCase",
    target: "Target",
    mode: str,
    system: str,
    cfg: "RunnerConfig",
    work_dir: Path,
    llms_txt_content: str | None = None,
    skill_content: str | None = None,
    capabilities: Any | None = None,
) -> tuple[int, int, int]:
    """Run the agent once through `codex exec`.

    Unlike the OpenAI and Claude paths, Codex CLI owns its own tool loop and
    edits the worktree directly. We therefore run Codex to completion, then run
    this benchmark's grader ourselves so the pass/fail contract stays identical.
    """
    import shutil
    import tempfile

    if shutil.which("codex") is None:
        raise RuntimeError(
            "codex CLI not found in PATH — install or authenticate Codex CLI first"
        )

    grader_cfg = use_case.grader
    run_script = Path(grader_cfg["run"])
    if not run_script.is_absolute():
        project_root = use_case.source_path.parents[2]
        run_script = project_root / run_script

    codex_model = os.environ.get("DOCS_EVAL_CODEX_MODEL", "")
    if not codex_model and cfg.model and not cfg.model.startswith("claude"):
        codex_model = cfg.model
    if not codex_model:
        codex_model = "gpt-5.5"

    prompt = _agent_prompt(
        use_case,
        target,
        mode,
        _tool_list_for_mode(mode, target),
        llms_txt_content,
        skill_content=skill_content,
        capabilities=capabilities,
        backend_note=(
            "Codex-specific validation contract: the benchmark runner will run the grader after you exit. "
            "Do not start dev servers, run Playwright, run the benchmark grader, or run npm build. "
            "If you need a quick check, run only TypeScript typecheck once. "
            "Codex may also use its native file and shell tools when needed, including network requests to the provided docs URLs, "
            "but keep all edits inside the scaffold directory."
        ),
    )
    start = time.time()
    codex_timed_out = False
    codex_returncode: int | None = None
    codex_tokens_used = 0
    transcript_base = Path(state.transcript_fp.name).with_suffix("")
    try:
        codex_timeout = int(
            os.environ.get(
                "DOCS_EVAL_CODEX_TIMEOUT",
                str(min(use_case.max_seconds, 180)),
            )
        )
    except ValueError:
        codex_timeout = min(use_case.max_seconds, 180)

    max_mock_user_rounds = int(os.environ.get("DOCS_EVAL_MOCK_USER_ROUNDS", "1"))
    codex_sandbox = os.environ.get("DOCS_EVAL_CODEX_SANDBOX", "danger-full-access")
    mock_replies: list[str] = []
    rounds_run = 0

    for mock_round in range(max_mock_user_rounds + 1):
        rounds_run = mock_round + 1
        prompt_for_round = prompt
        if mock_replies:
            prompt_for_round += "\n\n--- MOCK USER FEEDBACK SO FAR ---\n"
            prompt_for_round += "\n\n".join(mock_replies)
            prompt_for_round += "\n--- END MOCK USER FEEDBACK ---\n"

        suffix = "" if mock_round == 0 else f".mock{mock_round}"
        codex_prompt_path = transcript_base.with_suffix(f".codex{suffix}.prompt.txt")
        codex_events_path = transcript_base.with_suffix(f".codex{suffix}.jsonl")
        codex_stderr_path = transcript_base.with_suffix(f".codex{suffix}.stderr.txt")
        codex_final_path = transcript_base.with_suffix(f".codex{suffix}.final.txt")
        codex_prompt_path.write_text(prompt_for_round, encoding="utf-8")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="docs_eval_codex_last_"
        ) as f:
            last_message_path = f.name

        cmd = ["codex"]
        if mode in ("web", "web-ai-informed", "auto-informed", "auto-blind"):
            cmd.append("--search")
        cmd += [
            "exec",
            "--cd", str(work_dir.resolve()),
            "--sandbox", codex_sandbox,
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--json",
            "--output-last-message", last_message_path,
            "--color", "never",
        ]
        if codex_model:
            cmd += ["--model", codex_model]
        cmd.append(prompt_for_round)

        if cfg.verbose:
            model_note = f" --model {codex_model}" if codex_model else ""
            print(f"  [codex-cli] running round {mock_round + 1}: codex exec ...{model_note}", flush=True)
            print(f"  [prompt] BEGIN Codex prompt round {mock_round + 1}", flush=True)
            print(prompt_for_round, flush=True)
            print(f"  [prompt] END Codex prompt round {mock_round + 1}", flush=True)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=codex_timeout,
            )
            codex_returncode = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            codex_events_path.write_text(stdout, encoding="utf-8")
            codex_stderr_path.write_text(stderr, encoding="utf-8")
            import re
            token_match = re.search(r"tokens used\s+([\d,]+)", stdout + "\n" + stderr)
            if token_match:
                codex_tokens_used += int(token_match.group(1).replace(",", ""))
            try:
                final_text = Path(last_message_path).read_text(errors="replace")
            except OSError:
                final_text = ""
            codex_final_path.write_text(final_text, encoding="utf-8")
            if final_text:
                state.last_assistant_text = final_text
            state.log("codex_cli", {
                "round": mock_round + 1,
                "returncode": proc.returncode,
                "prompt_path": str(codex_prompt_path),
                "events_path": str(codex_events_path),
                "stderr_path": str(codex_stderr_path),
                "final_message_path": str(codex_final_path),
                "stdout_tail": stdout[-4000:],
                "stderr_tail": stderr[-4000:],
                "final_message": final_text[-4000:],
                "tokens_used": codex_tokens_used,
                "wall_seconds": round(time.time() - start, 2),
            })
            if proc.returncode != 0 and cfg.verbose:
                print(f"  [codex-cli] exited {proc.returncode}")
                for ln in (stderr or stdout).strip().splitlines()[-8:]:
                    print(f"            {ln}")
        except subprocess.TimeoutExpired as e:
            codex_timed_out = True
            stdout = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            codex_events_path.write_text(stdout, encoding="utf-8")
            codex_stderr_path.write_text(stderr, encoding="utf-8")
            try:
                final_text = Path(last_message_path).read_text(errors="replace")
            except OSError:
                final_text = ""
            codex_final_path.write_text(final_text, encoding="utf-8")
            if final_text:
                state.last_assistant_text = final_text
            state.log("codex_cli_timeout", {
                "round": mock_round + 1,
                "prompt_path": str(codex_prompt_path),
                "events_path": str(codex_events_path),
                "stderr_path": str(codex_stderr_path),
                "final_message_path": str(codex_final_path),
                "stdout_tail": stdout[-4000:],
                "stderr_tail": stderr[-4000:],
                "after_seconds": codex_timeout,
            })
            if cfg.verbose:
                print(f"  [codex-cli] TIMEOUT after {codex_timeout}s")
        finally:
            try:
                os.unlink(last_message_path)
            except OSError:
                pass

        if codex_returncode not in (0, None) or codex_timed_out:
            break

        request = (
            _extract_user_input_request(state.last_assistant_text)
            or _infer_user_input_request(state.last_assistant_text)
        )
        if not request or mock_round >= max_mock_user_rounds:
            break
        reply = _mock_user_reply(state, request)
        mock_replies.append(reply)
        if cfg.verbose:
            print(f"  [mock-user] replied to request {mock_round + 1}/{max_mock_user_rounds}", flush=True)
            print(reply, flush=True)

    if codex_returncode not in (0, None):
        if cfg.verbose:
            print("  [grader] skipped after Codex CLI failure")
        return 1, 0, 0

    if cfg.verbose:
        suffix = " after Codex timeout" if codex_timed_out else " after Codex"
        print(f"  [grader] running final grader{suffix}")
    result = _run_grader(state)
    state.turns_to_first_grader = state.turns_to_first_grader or 1
    if result.get("pass"):
        state.turns_to_success = state.turns_to_success or 1
    if cfg.verbose:
        print(f"  [grader]  {'PASS' if result.get('pass') else 'FAIL'}")

    # Codex CLI reports a single aggregate token count, not input/output split.
    # Store it in input_tokens so existing reports include the usage total.
    return rounds_run, codex_tokens_used, 0


# --- Main run loop ---------------------------------------------------------

def run_cell(use_case: UseCase, target: Target, mode: str, run_idx: int,
             cfg: RunnerConfig) -> RunResult:
    """Run a single (use_case, target, mode, run_idx) cell."""
    cell_id = f"{use_case.id}__{target.name}__{mode}__r{run_idx}"
    work_dir = cfg.work_root / cell_id
    work_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = cfg.transcript_root / f"{cell_id}.jsonl"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Step 1: scaffold the starter app ─────────────────────────────────────
    if cfg.verbose:
        print(f"  [scaffold] creating starter app in {work_dir}")
    setup_start = time.time()
    setup_script = Path(use_case.scaffold["setup"])
    if not setup_script.is_absolute():
        project_root = use_case.source_path.parents[2]
        setup_script = project_root / setup_script
    setup_proc = subprocess.run(
        ["bash", str(setup_script), str(work_dir)],
        capture_output=True, text=True,
    )
    setup_elapsed = time.time() - setup_start
    if setup_proc.returncode != 0:
        # Emit a failed RunResult so one broken setup doesn't kill the whole matrix.
        return RunResult(
            use_case_id=use_case.id, target_name=target.name, mode=mode,
            run_idx=run_idx, passed=False, pass_at_1=False, turns=0,
            wall_seconds=setup_elapsed,
            final_grader_stdout=setup_proc.stdout,
            final_grader_stderr=f"SETUP FAILED (exit {setup_proc.returncode}):\n"
                                 + setup_proc.stderr,
            failure_category="setup_failed",
            transcript_path=transcript_path, code_dir=work_dir,
        )
    if cfg.verbose:
        print(f"  [scaffold] done in {setup_elapsed:.1f}s")

    # ── Step 2: fetch docs context for llms-txt mode ──────────────────────────
    llms_txt_content = None
    truncated = False
    if mode == "llms-txt":
        if not target.llms_txt:
            return RunResult(
                use_case_id=use_case.id, target_name=target.name, mode=mode,
                run_idx=run_idx, passed=False, pass_at_1=False, turns=0,
                wall_seconds=0.0, final_grader_stdout="", final_grader_stderr="",
                failure_category="no_llms_txt", transcript_path=transcript_path,
                code_dir=work_dir,
            )
        if cfg.verbose:
            print(f"  [llms-txt] fetching {target.llms_txt}")
        try:
            raw = llms_txt.fetch_llms_txt(target.llms_txt)
            llms_txt_content, truncated = llms_txt.truncate_for_context(raw)
            if cfg.verbose:
                trunc_note = " (truncated)" if truncated else ""
                print(f"  [llms-txt] {len(llms_txt_content):,} chars loaded{trunc_note}")
        except Exception as e:
            return RunResult(
                use_case_id=use_case.id, target_name=target.name, mode=mode,
                run_idx=run_idx, passed=False, pass_at_1=False, turns=0,
                wall_seconds=0.0, final_grader_stdout="",
                final_grader_stderr=f"llms_txt fetch failed: {e}",
                failure_category="llms_txt_fetch_failed",
                transcript_path=transcript_path, code_dir=work_dir,
            )

    if mode == "mcp" and not target.mcp_endpoint:
        return RunResult(
            use_case_id=use_case.id, target_name=target.name, mode=mode,
            run_idx=run_idx, passed=False, pass_at_1=False, turns=0,
            wall_seconds=0.0, final_grader_stdout="", final_grader_stderr="",
            failure_category="no_mcp_endpoint", transcript_path=transcript_path,
            code_dir=work_dir,
        )

    # ── Step 2b: load skill file for skill mode ───────────────────────────────
    skill_content: str | None = None
    if mode == "skill":
        project_root = use_case.source_path.parents[2]
        skill_path = project_root / ".claude" / "skills" / target.vendor.lower() / "SKILL.md"
        if skill_path.exists():
            skill_content = skill_path.read_text()
            if cfg.verbose:
                print(f"  [skill] loaded {skill_path} ({len(skill_content):,} chars)")
        else:
            if cfg.verbose:
                print(f"  [skill] no skill file at {skill_path} — running without context")

    # ── Step 2c: discovery phase ─────────────────────────────────────────────
    from .discovery import get_capabilities as _get_caps
    if cfg.verbose:
        print(f"  [discovery] probing {target.base_url} …")
    try:
        discovered_caps = _get_caps(
            target.base_url, target.mcp_endpoint, target.markdown_suffix, target.name
        )
        if cfg.verbose:
            flags = []
            if discovered_caps.has_llms_full_txt: flags.append("llms-full.txt")
            if discovered_caps.has_llms_txt: flags.append("llms.txt")
            if discovered_caps.has_skill_md: flags.append("skill.md")
            if discovered_caps.has_mcp: flags.append("mcp")
            print(f"  [discovery] found: {flags or ['(none)']}")
    except Exception as _e:
        discovered_caps = None
        if cfg.verbose:
            print(f"  [discovery] failed: {_e}")

    # ── Step 3: run the agent loop ────────────────────────────────────────────
    tools = _build_tools(mode, target)
    system = _system_prompt(
        use_case, target, mode, llms_txt_content, skill_content,
        capabilities=discovered_caps if mode == "auto-informed" else None,
    )

    # Build AgentMail client once per cell (shared across all turns).
    agentmail_client = None
    if cfg.agentmail_api_key and _AGENTMAIL_AVAILABLE:
        agentmail_client = _AgentMailClient(api_key=cfg.agentmail_api_key)

    transcript_fp = transcript_path.open("w")
    state = _AgentState(work_dir, use_case, target, transcript_fp,
                        agentmail_client=agentmail_client)
    disclosed_to_agent = mode == "auto-informed"
    state.log("meta", {
        "use_case": use_case.id,
        "target": target.name,
        "mode": mode,
        "run_idx": run_idx,
        "model": cfg.model,
        "backend": cfg.backend,
        "llms_txt_truncated": truncated,
        "setup_seconds": round(setup_elapsed, 2),
        "work_dir": str(work_dir),
        "transcript": str(transcript_path),
        "discovered_capabilities": discovered_caps.to_dict() if discovered_caps else None,
        "disclosed_to_agent": disclosed_to_agent,
    })
    state.log("user", use_case.prompt)

    start = time.time()
    total_in = total_out = 0
    turns = 0

    if cfg.verbose:
        print(f"  [agent] starting — budget: {use_case.max_turns} turns / "
              f"{use_case.max_seconds}s", flush=True)

    backend = cfg.backend
    if backend == "auto":
        backend = "claude" if _is_claude_model(cfg.model) else "openai"

    try:
        if backend == "codex":
            turns, total_in, total_out = _run_loop_codex(
                state, use_case, target, mode, system, cfg, work_dir,
                llms_txt_content=llms_txt_content,
                skill_content=skill_content,
                capabilities=discovered_caps if mode == "auto-informed" else None,
            )
        elif backend == "claude":
            turns, total_in, total_out = _run_loop_claude(
                state, use_case, target, mode, system, tools, cfg,
                work_dir, setup_elapsed,
            )
        elif backend == "openai":
            turns, total_in, total_out = _run_loop_openai(
                state, use_case, target, mode, system, tools, cfg,
            )
        else:
            raise ValueError(f"unknown backend: {cfg.backend}")

        # ── Step 4: optional human review ────────────────────────────────────
        if state.last_pass and cfg.human_review and use_case.human_check:
            hr_passed, hr_notes = _human_review(state, use_case, cfg)
            state.last_human_review = {"passed": hr_passed, "notes": hr_notes}
            state.log("human_review", state.last_human_review)
    finally:
        # Extract self-report from agent's last text message.
        self_report: dict[str, Any] | None = None
        mismatches: list[str] = []
        if state.last_assistant_text:
            self_report = _extract_self_report(state.last_assistant_text)
            if self_report:
                if state.doc_resource_inventory:
                    mismatches = _detect_mismatches(state, self_report)
                state.log("self_report", {"report": self_report, "mismatches": mismatches})

        doc_resources = list(state.doc_resource_inventory.values())
        if self_report:
            reported_urls = self_report.get("resource_urls") or []
            if isinstance(reported_urls, list):
                known_urls = {r.get("url") for r in doc_resources}
                for url in reported_urls:
                    url = str(url)
                    if url and url not in known_urls:
                        doc_resources.append({
                            "url": url,
                            "resource_type": _resource_type_from_url(url),
                            "access_method": "agent_self_report",
                            "times_accessed": 1,
                        })
                        known_urls.add(url)

        state.log("summary", {
            "turns": turns,
            "passed": state.last_pass,
            "grader_calls": state.grader_calls,
            "pass_at_1": state.first_grader_pass,
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "wall_seconds": round(time.time() - start, 2),
            "file_reads": state.file_reads,
            "file_writes": state.file_writes,
            "turns_to_first_grader": state.turns_to_first_grader,
            "turns_to_success": state.turns_to_success,
            "doc_resources": doc_resources,
            "requested_user_inputs": state.requested_user_inputs,
        })
        transcript_fp.close()

    elapsed = time.time() - start
    passed = state.last_pass
    failure_category = None
    if not passed:
        failure_category = _categorize_failure(state, use_case)

    # Pull human review result out of the transcript (logged inside the loop)
    hr_passed: bool | None = None
    hr_notes = ""
    if cfg.human_review and state.last_pass:
        hr_entry = state.last_human_review
        if hr_entry:
            hr_passed = hr_entry.get("passed")
            hr_notes = hr_entry.get("notes", "")

    return RunResult(
        use_case_id=use_case.id,
        target_name=target.name,
        mode=mode,
        run_idx=run_idx,
        passed=passed,
        pass_at_1=bool(state.first_grader_pass),
        turns=turns,
        wall_seconds=elapsed,
        final_grader_stdout=state.last_stdout,
        final_grader_stderr=state.last_stderr,
        failure_category=failure_category,
        transcript_path=transcript_path,
        code_dir=work_dir,
        llms_txt_truncated=truncated,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        human_review_passed=hr_passed,
        human_review_notes=hr_notes,
        file_reads=state.file_reads,
        file_writes=state.file_writes,
        grader_calls=state.grader_calls,
        turns_to_first_grader=state.turns_to_first_grader,
        turns_to_success=state.turns_to_success,
        discovered_capabilities=discovered_caps.to_dict() if discovered_caps else None,
        disclosed_to_agent=disclosed_to_agent,
        doc_resources=doc_resources,
        agent_self_report=self_report,
        self_report_mismatches=mismatches,
        requested_user_inputs=state.requested_user_inputs,
    )


# --- Failure categorization ------------------------------------------------

def _categorize_failure(state: _AgentState, use_case: UseCase) -> str:
    """Heuristic bucketing of why a cell failed. Useful for the report.

    Buckets (in priority order):
    - never_graded: agent never called run_grader
    - hallucinated_api: tsc complains about missing exports from real packages
    - hallucinated_package: tsc can't resolve the imported package at all
    - missing_expected_import: agent didn't import what we required
    - typecheck_error: any other tsc failure
    - exhausted_turns: ran out of budget
    """
    stderr = state.last_stderr or ""
    stdout = state.last_stdout or ""
    combined = stderr + stdout

    if state.first_grader_pass is None:
        return "never_graded"
    if "Cannot find module" in combined or "Module not found" in combined:
        return "hallucinated_package"
    if "has no exported member" in combined or "is not exported" in combined:
        return "hallucinated_api"
    expected_imports = use_case.expected.get("imports", [])
    # Search all TypeScript files in the project (excluding node_modules).
    code_files = [
        p for p in state.work_dir.rglob("*.ts")
        if "node_modules" not in p.parts
    ] + [
        p for p in state.work_dir.rglob("*.tsx")
        if "node_modules" not in p.parts
    ]
    if code_files:
        # Concatenate all project source files for the import/forbidden checks.
        code = "\n".join(p.read_text(errors="replace") for p in code_files)
        missing = [imp for imp in expected_imports if imp not in code]
        if missing:
            return "missing_expected_import"
        forbidden = use_case.expected.get("forbidden", [])
        used_forbidden = [f for f in forbidden if f in code]
        if used_forbidden:
            return "used_forbidden_api"
    # Distinguish tsc failures from Playwright/runtime failures
    tsc_patterns = ("error TS", "Type error", "Cannot find name", "Property", "Argument of type")
    e2e_patterns = ("expect(", "toBeVisible", "Timeout", "Playwright", "Test failed", "1 failed")
    if any(p in combined for p in e2e_patterns):
        return "e2e_failure"
    if any(p in combined for p in tsc_patterns) or combined:
        return "typecheck_error"
    return "exhausted_turns"
