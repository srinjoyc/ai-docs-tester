"""Optional qualitative AI review for completed eval cells."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from openai import OpenAI

from .config import Target, UseCase
from .runner import RunResult


MAX_SOURCE_CHARS = 70_000
MAX_TRANSCRIPT_CHARS = 35_000
MAX_GRADER_CHARS = 20_000


RUBRIC = {
    "use_case_fulfillment": 20,
    "zerodev_account_abstraction_correctness": 20,
    "web3_security_and_safety": 15,
    "gas_sponsorship_paymaster_correctness": 10,
    "async_ux_and_error_handling": 10,
    "typescript_code_quality": 10,
    "docs_usage_and_evidence_fidelity": 10,
    "grader_alignment_and_limitations": 5,
}


WEB3_ECOSYSTEM_CONTEXT = """
Common Web3/account-abstraction context for evaluation:
- Ethereum accounts are either EOAs controlled by private keys or contract accounts controlled by code. Smart accounts are contract accounts that expose wallet-like behavior.
- EIP-4337 account abstraction routes user intent through a smart account. A UserOperation enters a dedicated mempool, bundlers simulate/collect operations, EntryPoint validates and executes them, and a transaction receipt is eventually produced.
- The EIP-4337 signature should bind to chain ID and EntryPoint address to avoid replay across chains or EntryPoint versions. Chain and EntryPoint consistency matter.
- A smart account integration is not the same as an EOA transaction. Look for smart account creation/configuration, validator configuration, UserOperation submission or smart-account client transaction helpers, and receipt handling.
- Bundlers accept UserOperations and submit bundles on-chain. They must be able to attribute simulation failures to sender, factory, or paymaster. Runtime failures can come from validation, gas estimates, bad nonce, wrong chain, unsupported EntryPoint, or paymaster policy.
- Paymasters sponsor gas or validate paymaster data. A sponsored mint should use a paymaster/sponsor flow, not simply call the NFT contract from the connected EOA or hide gas language in the UI.
- Paymaster sponsorship is a production risk surface. Public sponsorship endpoints should be scoped by policy, chain, contract, method, value, rate limits, allowlists, or server-side checks to reduce abuse.
- EntryPoint version, smart account/kernel version, validator type, bundler/paymaster endpoint, and chain must be internally consistent. Mixed versions can pass type checks but fail against live infrastructure.
- Wallet libraries such as wagmi and viem commonly provide connected wallet state, wallet clients, public clients, chain state, contract encoding, transaction sending, and transports.
- viem concepts: a Public Client reads chain state; a Wallet Client signs and sends with an account; transports include HTTP and custom EIP-1193 providers; chain definitions should match the intended network.
- wagmi concepts: hooks commonly expose wallet connection, account, wallet client, chain ID, configured chains, switchChain, send/write contract flows, and async status/error states.
- permissionless-style and vendor AA SDKs often provide smart account clients, `sendUserOperation`, `waitForUserOperationReceipt`, entry point utilities, paymaster clients, and helpers to bridge viem clients into AA clients.
- ZeroDev Kernel patterns commonly involve a Kernel account, an ECDSA validator or other validator plugin, a public client, a bundler RPC URL, a paymaster RPC/client for sponsorship, and a kernel account client that sends the operation.
- In a browser dapp, the user's connected wallet should normally sign messages/typed data or authorize operations. Do not embed private keys, mnemonics, or admin credentials in frontend code.
- Public project IDs, bundler URLs, and paymaster URLs may be acceptable only when the provider intends them to be public and the real authority is enforced by paymaster/dashboard/server policy.
- Contract writes should use the correct ABI, target address, chain, calldata, value, and connected account. Simulating or estimating before write is useful but not sufficient for AA paymaster flows.
- The UI should make irreversible or value-bearing actions explicit. For a mint, it should prevent double-submit, show pending state, show actionable failure messages, and show a clear success artifact.
- Transaction hash and UserOperation hash are different concepts. A production app should label which hash is displayed and, when possible, link to an explorer or show the final transaction receipt.
- Chain mismatch is a common dapp failure mode. Production-grade apps usually check the connected wallet chain and either switch, disable the action, or show a precise unsupported-chain message.
- Strong Web3 frontend code validates required env/config values, avoids empty-string URL footguns, distinguishes public config from secrets, and fails closed with clear setup errors.
- Security review should consider access control, least privilege, replay/signer assumptions, approval/permission scope, paymaster abuse, reentrancy only when contracts are authored/modified, and whether tests cover realistic edge cases.
- Passing a mock Playwright test is useful evidence for wiring and UI, but it does not prove live bundler, paymaster policy, chain switching, RPC behavior, wallet prompts, or contract execution.

Important ecosystem references and vendor patterns:
- Rhinestone: smart wallet SDK and cross-chain intent API; commonly relevant for ERC-7579 modular accounts, modules, signers, passkeys, session keys, fee sponsorship, and cross-chain transactions/intents.
- Safe: widely used smart account with multisig at the core, secure defaults, modules for alternative access patterns, guards/fallback handlers, batched transactions, and flexible execution through contract-account logic.
- Privy: embedded wallet and auth infrastructure. Common primitives include email/social/passkey auth, self-custodial embedded wallets, wallet fleets, delegated/server sessions, policies, transaction controls, webhooks, and gas sponsorship.
- Dynamic: authentication and wallet SDK. Common primitives include wallet/social/email/SMS/passkey auth, embedded wallets, external wallet connection, JWT/session handling, access control, MFA, and bring-your-own-auth flows.
- Magic: embedded/white-label wallet infrastructure with publishable API keys, email OTP/social-style onboarding, generated starter apps, wallet methods, and broad chain support.
- Web3Auth / MetaMask Embedded Wallets: embedded wallet infrastructure using familiar OAuth-style onboarding and non-custodial wallet control. It is commonly used to remove seed phrase friction while preserving user control.
- MetaMask: self-custodial wallet ecosystem. Dapps usually interact through EIP-1193/browser providers, MetaMask Connect/SDK, embedded wallets, or Smart Accounts Kit. Watch for wallet permission, chain, and user-consent flows.
- WalletConnect: wallet-to-app connectivity layer. It is commonly used directly or through SDKs such as Reown AppKit, Privy, Dynamic, RainbowKit, and ConnectKit to support many wallets and chains.
- Pimlico: ERC-4337 infrastructure provider and maintainer of common AA tooling such as permissionless.js and Alto bundler. Common primitives include bundler RPCs, verifying/ERC-20 paymasters, gas sponsorship, batching, and social-login signer integrations through providers such as Privy, Dynamic, Magic, and Web3Auth.
- Alchemy: account abstraction and wallet infrastructure provider. Common primitives include Account Kit, smart accounts such as Light Account and Modular Account, bundler clients, paymaster/gas manager policy IDs, signer abstractions, session keys, and viem-compatible smart account packages.
- When judging integrations with these providers, distinguish wallet/auth onboarding from account abstraction execution. A connected embedded wallet or MetaMask EOA alone does not imply a smart account, paymaster sponsorship, or gasless UserOperation flow.
- Treat these products as adjacent context and competitors/similar tools. For a ZeroDev task, do not reward replacing ZeroDev with a competitor. Use competitor knowledge to recognize common concepts, expected ergonomics, and documentation gaps.
"""


def review_result(
    result: RunResult,
    use_case: UseCase,
    target: Target,
    out_dir: Path,
    model: str,
    timeout_seconds: int = 120,
    backend: str = "openai",
) -> dict[str, Any]:
    """Judge one completed result and persist a structured JSON artifact."""
    review_dir = out_dir / "ai_reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    out_path = review_dir / f"{result.transcript_path.stem}.json"

    payload = _build_review_payload(result, use_case, target)
    prompt_path = review_dir / f"{result.transcript_path.stem}.prompt.txt"
    prompt = _review_prompt(payload)
    prompt_path.write_text(prompt)

    if backend == "codex":
        review = _run_codex_review(prompt, review_dir, result.transcript_path.stem, model, timeout_seconds)
    elif backend == "openai":
        review = _run_openai_review(prompt, model, timeout_seconds)
    else:
        raise ValueError(f"unknown AI review backend: {backend}")

    review["_metadata"] = {
        "backend": backend,
        "model": model,
        "prompt_path": str(prompt_path),
        "result_transcript": str(result.transcript_path),
        "code_dir": str(result.code_dir),
    }
    out_path.write_text(json.dumps(review, indent=2))
    result.ai_review = review
    result.ai_review_path = out_path
    return review


def _run_openai_review(prompt: str, model: str, timeout_seconds: int) -> dict[str, Any]:
    api_key = os.environ.get("CHAT_GPT_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("AI review requires CHAT_GPT_API_KEY or OPENAI_API_KEY, or use --review-backend codex")

    client = OpenAI(api_key=api_key, timeout=timeout_seconds)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an objective senior Web3 and frontend evaluator. "
                    "Assess whether an AI-generated implementation really satisfies "
                    "the requested use case, not just whether it passed a mock grader. "
                    "Return only valid JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )
    content = response.choices[0].message.content or "{}"
    try:
        review = json.loads(content)
    except json.JSONDecodeError:
        review = {
            "overall_score": 0,
            "verdict": "poor",
            "passed_grader": False,
            "would_likely_work_real_world": False,
            "scores": {},
            "strengths": [],
            "issues": [{
                "severity": "major",
                "category": "judge_output",
                "evidence": "The judge did not return valid JSON.",
                "recommendation": "Inspect the raw judge output.",
            }],
            "raw_output": content,
        }
    return review


def _run_codex_review(
    prompt: str,
    review_dir: Path,
    stem: str,
    model: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    schema_path = review_dir / f"{stem}.schema.json"
    stdout_path = review_dir / f"{stem}.codex.jsonl"
    stderr_path = review_dir / f"{stem}.codex.stderr.txt"
    final_path = review_dir / f"{stem}.codex.final.txt"
    schema_path.write_text(json.dumps(_review_schema(), indent=2))

    cmd = [
        "codex", "exec",
        "--model", model,
        "--sandbox", "read-only",
        "--ephemeral",
        "--ignore-rules",
        "--output-schema", str(schema_path),
        "--output-last-message", str(final_path),
        "--color", "never",
        "--json",
        "-",
    ]
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    stdout_path.write_text(proc.stdout or "")
    stderr_path.write_text(proc.stderr or "")
    final = final_path.read_text(errors="replace") if final_path.exists() else ""
    try:
        review = json.loads(final)
    except json.JSONDecodeError:
        review = {
            "overall_score": 0,
            "verdict": "poor",
            "passed_grader": False,
            "would_likely_work_real_world": False,
            "scores": {},
            "strengths": [],
            "issues": [{
                "severity": "major",
                "category": "judge_output",
                "evidence": "Codex review did not return valid JSON.",
                "recommendation": "Inspect the raw Codex review artifacts.",
            }],
            "raw_output": final,
        }
    review.setdefault("_codex_returncode", proc.returncode)
    review.setdefault("_codex_stdout_path", str(stdout_path))
    review.setdefault("_codex_stderr_path", str(stderr_path))
    review.setdefault("_codex_final_path", str(final_path))
    return review


def _review_schema() -> dict[str, Any]:
    score_keys = list(RUBRIC)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "overall_score", "verdict", "passed_grader",
            "would_likely_work_real_world", "scores", "strengths",
            "issues", "web3_safety_notes", "docs_usage_assessment",
            "zerodev_docs_helpfulness", "grader_limitations", "confidence",
        ],
        "properties": {
            "overall_score": {"type": "number", "minimum": 0, "maximum": 100},
            "verdict": {"type": "string"},
            "passed_grader": {"type": "boolean"},
            "would_likely_work_real_world": {"type": "boolean"},
            "scores": {
                "type": "object",
                "additionalProperties": False,
                "required": score_keys,
                "properties": {key: {"type": "number", "minimum": 0, "maximum": max_score}
                               for key, max_score in RUBRIC.items()},
            },
            "strengths": {"type": "array", "items": {"type": "string"}},
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["severity", "category", "evidence", "recommendation"],
                    "properties": {
                        "severity": {"type": "string"},
                        "category": {"type": "string"},
                        "evidence": {"type": "string"},
                        "recommendation": {"type": "string"},
                    },
                },
            },
            "web3_safety_notes": {"type": "array", "items": {"type": "string"}},
            "docs_usage_assessment": {"type": "string"},
            "zerodev_docs_helpfulness": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "score", "assessment", "helpful_parts",
                    "missing_or_hard_to_find", "competitor_context_notes",
                ],
                "properties": {
                    "score": {"type": "number", "minimum": 0, "maximum": 100},
                    "assessment": {"type": "string"},
                    "helpful_parts": {"type": "array", "items": {"type": "string"}},
                    "missing_or_hard_to_find": {"type": "array", "items": {"type": "string"}},
                    "competitor_context_notes": {"type": "array", "items": {"type": "string"}},
                },
            },
            "grader_limitations": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


def _build_review_payload(result: RunResult, use_case: UseCase, target: Target) -> dict[str, Any]:
    return {
        "use_case": {
            "id": use_case.id,
            "title": use_case.title,
            "prompt": use_case.prompt,
            "expected": use_case.expected,
            "human_check": use_case.human_check,
        },
        "target": {
            "name": target.name,
            "base_url": target.base_url,
            "llms_txt": target.llms_txt,
            "mcp_endpoint": target.mcp_endpoint,
            "markdown_suffix": target.markdown_suffix,
            "notes": target.notes,
        },
        "deterministic_result": {
            "passed": result.passed,
            "pass_at_1": result.pass_at_1,
            "turns": result.turns,
            "wall_seconds": result.wall_seconds,
            "final_grader_stdout": _clip(result.final_grader_stdout, MAX_GRADER_CHARS),
            "final_grader_stderr": _clip(result.final_grader_stderr, MAX_GRADER_CHARS),
            "failure_category": result.failure_category,
        },
        "agent_self_report": result.agent_self_report,
        "self_report_mismatches": result.self_report_mismatches,
        "doc_resources": result.doc_resources,
        "source_files": _collect_source_files(result.code_dir),
        "transcript_excerpt": _read_tail(result.transcript_path, MAX_TRANSCRIPT_CHARS),
        "codex_artifacts": _collect_codex_artifact_refs(result.transcript_path),
    }


def _review_prompt(payload: dict[str, Any]) -> str:
    return f"""Review this docs-eval result as a second-layer qualitative judge.

This review is secondary to the deterministic Playwright/typecheck grader. Do not mark the run as failed only because of style preferences. Do not reward it merely because it passed a mock grader. Judge how well the AI accomplished the end user's requested docs-eval task at the level requested by the prompt and benchmark.

Important calibration:
- This is not a full production-readiness audit unless the use-case prompt explicitly asks for production hardening.
- Production concerns should usually appear as caveats or recommendations, not heavy score penalties, when the prompt only asks for a working use-case implementation.
- The main implementation score should answer: did the agent use the target product correctly, complete the requested workflow, pass meaningful checks, and avoid obvious unsafe or misleading behavior?
- The docs-helpfulness score should separately answer: did the target docs help a first-time agent discover and complete the workflow?

Score calibration:
- 90-100: cleanly completes the requested docs-eval task, uses the target product correctly, passes checks, and has only minor caveats relative to the prompt.
- 75-89: completes the core task and passes checks, but has some implementation ambiguity, limited evidence, or moderate gaps.
- 60-74: partially completes the task or relies too much on assumptions/mocks, with gaps that could affect the requested workflow.
- 40-59: substantial implementation risk, unclear target-product usage, or missing important requested behavior.
- 0-39: does not accomplish the task, is unsafe, uses the wrong product, or is mostly misleading.

Severity calibration:
- critical: unsafe, exploitable, likely fund/key loss, or the primary task cannot work.
- major: likely to break or seriously degrade the requested benchmark task or the target-product integration.
- minor: production hardening, polish, observability, or edge-case gaps that do not usually block the requested task.

For chain validation, paymaster policy, explorer links, config validation, and other production-hardening concerns: treat them as minor unless they are explicitly required by the prompt or there is evidence they would break the benchmark task.

Evaluate the result using agent-ready developer platform principles:
- Progressive disclosure: did the agent have and use lightweight entry points first, then fetch task-specific detail only when needed?
- Information: were machine-readable docs, markdown, llms.txt, API/SDK references, and installed types sufficient and discoverable?
- Guidance: did task-oriented docs or prompts give the agent an actionable workflow without duplicating stale reference material?
- Action: did SDKs, CLIs, MCP, or APIs expose predictable interfaces and actionable errors the agent could work against?
- Verification: did the implementation pass meaningful checks, and what real-world paths remain unverified?
- Measurement: does the output make it clear what resources the agent used and where it struggled?

Use this ecosystem background when judging the implementation:
{WEB3_ECOSYSTEM_CONTEXT}

ZeroDev perspective:
- You are judging this as a ZeroDev docs/product eval, not as a generic Web3 benchmark.
- For ZeroDev use cases, the best implementation should use ZeroDev concepts and APIs where the task asks for them.
- Adjacent products such as Pimlico, Alchemy, Safe, Rhinestone, Privy, Dynamic, Magic, Web3Auth, MetaMask, and WalletConnect are context for what an expert would know and for comparing docs ergonomics, but they are not substitutes for ZeroDev unless the task explicitly asks for them.
- Separate implementation quality from docs helpfulness. If the agent solved the task mostly from installed types or prior knowledge, say that the implementation may be good while the docs-helpfulness evidence is weak.
- Do not require a production deployment plan, paymaster dashboard policy, or exhaustive chain UX unless the prompt requested it. Capture those as caveats.
- If the scaffold lacks ZeroDev project, bundler, paymaster, policy, or admin credentials, reward agents that explicitly identify the missing ZeroDev-specific configuration and ask for it. Penalize agents that invent project IDs, hardcode fake credentials, or pretend live sponsorship can work without required dashboard/paymaster setup.
- Assess whether ZeroDev docs helped a first-time user understand the necessary workflow: wallet/signer source, Kernel account creation, validator selection, EntryPoint/kernel versions, bundler URL, paymaster sponsorship, sending/waiting for UserOperations or smart-account transactions, and interpreting hashes/receipts.
- Call out docs gaps as product feedback: missing discoverable path, unclear versioning, hard-to-find package names, ambiguous signer/wallet integration, unclear paymaster policy setup, or insufficient production caveats.

Use these best-practice anchors for Web3/account-abstraction apps:
- Never expose or request private keys, mnemonics, bundler secrets, paymaster secrets, or project admin credentials in client-side code.
- It is acceptable for an agent to ask the user for missing public project IDs, bundler/paymaster URLs, or admin credentials needed for one-time setup. The agent should distinguish public runtime config from secret/admin config and should not place secret/admin values in frontend code.
- Prefer clear wallet connection, explicit user action, visible pending/success/error states, and transaction or UserOperation hashes linked or shown after success.
- Verify the app uses the intended chain, account abstraction entry point/kernel version where relevant, and ZeroDev account/paymaster APIs coherently.
- Gas sponsorship should use a ZeroDev paymaster/sponsor flow, not simply a normal EOA transaction or a misleading UI label.
- Treat mocked RPC/test behavior as limited evidence. Identify gaps that could fail against real RPC, bundler, paymaster, or wallet environments.
- Prefer current docs and installed SDK types over hallucinated APIs. Penalize impossible imports, stale package assumptions, and undocumented magic values.

Rubric, total 100:
{json.dumps(RUBRIC, indent=2)}

Return JSON with exactly this shape:
{{
  "overall_score": 0,
  "verdict": "excellent|good|mixed|poor|unsafe",
  "passed_grader": true,
  "would_likely_work_real_world": true,
  "scores": {{
    "use_case_fulfillment": 0,
    "zerodev_account_abstraction_correctness": 0,
    "web3_security_and_safety": 0,
    "gas_sponsorship_paymaster_correctness": 0,
    "async_ux_and_error_handling": 0,
    "typescript_code_quality": 0,
    "docs_usage_and_evidence_fidelity": 0,
    "grader_alignment_and_limitations": 0
  }},
  "strengths": [],
  "issues": [
    {{
      "severity": "critical|major|minor",
      "category": "",
      "evidence": "",
      "recommendation": ""
    }}
  ],
  "web3_safety_notes": [],
  "docs_usage_assessment": "",
  "zerodev_docs_helpfulness": {{
    "score": 0,
    "assessment": "",
    "helpful_parts": [],
    "missing_or_hard_to_find": [],
    "competitor_context_notes": []
  }},
  "grader_limitations": [],
  "confidence": 0.0
}}

Evidence bundle:
```json
{json.dumps(payload, indent=2)}
```
"""


def _collect_source_files(code_dir: Path) -> list[dict[str, str]]:
    allowed_suffixes = {".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".html", ".md"}
    ignored_parts = {"node_modules", ".next", "dist", "build", ".git"}
    files: list[dict[str, str]] = []
    remaining = MAX_SOURCE_CHARS
    for path in sorted(code_dir.rglob("*")):
        if remaining <= 0:
            break
        if not path.is_file() or path.suffix not in allowed_suffixes:
            continue
        if any(part in ignored_parts for part in path.parts):
            continue
        rel = path.relative_to(code_dir)
        try:
            text = path.read_text(errors="replace")
        except Exception as exc:
            text = f"<unreadable: {exc}>"
        clipped = _clip(text, remaining)
        files.append({"path": str(rel), "content": clipped})
        remaining -= len(clipped)
    return files


def _collect_codex_artifact_refs(transcript_path: Path) -> dict[str, str]:
    stem = transcript_path.with_suffix("")
    artifacts = {}
    for suffix, label in (
        (".codex.prompt.txt", "prompt"),
        (".codex.jsonl", "jsonl"),
        (".codex.stderr.txt", "stderr"),
        (".codex.final.txt", "final"),
    ):
        path = Path(str(stem) + suffix)
        if path.exists():
            artifacts[label] = str(path)
    return artifacts


def _read_tail(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(errors="replace")
    except Exception as exc:
        return f"<unreadable: {exc}>"
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... <truncated> ..."
