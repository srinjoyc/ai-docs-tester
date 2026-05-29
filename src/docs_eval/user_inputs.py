"""Deterministic user-input provider for benchmark agents.

The benchmark should reward agents that ask for missing vendor-specific
configuration, but it should not require a human to sit in the loop. This
module maps well-formed requests for known test values to stable responses.
"""
from __future__ import annotations

import os
import re
from typing import Any


_TEST_PROJECT_ID = "docs-eval-zerodev-project"
_ARBITRUM_SEPOLIA_CHAIN_ID = "421614"


def _normalize(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def _public_defaults(env: dict[str, str]) -> dict[str, str]:
    project_id = (
        env.get("DOCS_EVAL_ZERODEV_PROJECT_ID")
        or env.get("ZERODEV_PROJECT_ID")
        or _TEST_PROJECT_ID
    )
    rpc_url = f"https://staging-rpc.zerodev.app/api/v3/{project_id}/chain/{_ARBITRUM_SEPOLIA_CHAIN_ID}"
    return {
        "ZERODEV_PROJECT_ID": project_id,
        "BUNDLER_URL": env.get("BUNDLER_URL") or env.get("ZERODEV_BUNDLER_URL") or rpc_url,
        "PAYMASTER_URL": env.get("PAYMASTER_URL") or env.get("ZERODEV_PAYMASTER_URL") or rpc_url,
    }


_ALIASES = {
    "ZERO_DEV_PROJECT_ID": "ZERODEV_PROJECT_ID",
    "PROJECT_ID": "ZERODEV_PROJECT_ID",
    "ZERODEV_PROJECT": "ZERODEV_PROJECT_ID",
    "ZERODEV_PROJECT_KEY": "ZERODEV_PROJECT_ID",
    "BUNDLER_RPC_URL": "BUNDLER_URL",
    "ZERODEV_BUNDLER_URL": "BUNDLER_URL",
    "ZERODEV_BUNDLER_RPC_URL": "BUNDLER_URL",
    "PAYMASTER_RPC_URL": "PAYMASTER_URL",
    "ZERODEV_PAYMASTER_URL": "PAYMASTER_URL",
    "ZERODEV_PAYMASTER_RPC_URL": "PAYMASTER_URL",
}


_SECRET_MARKERS = (
    "ADMIN",
    "API_KEY",
    "SECRET",
    "TOKEN",
    "PRIVATE_KEY",
    "MNEMONIC",
    "SEED",
)


def _looks_secret(name: str, item: dict[str, Any]) -> bool:
    if item.get("secret") is True:
        return True
    return any(marker in name for marker in _SECRET_MARKERS)


def provide_user_input(
    args: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return benchmark-provided values for a tool request.

    Public ZeroDev runtime config is safe to provide by default. Secret or
    admin-style values are only provided when explicitly enabled, since tool
    transcripts are written to disk as test artifacts.
    """
    source_env = env if env is not None else os.environ
    requested_values = args.get("requested_values") or []
    defaults = _public_defaults(source_env)
    allow_secrets = source_env.get("DOCS_EVAL_ALLOW_SECRET_USER_INPUT") == "1"

    provided: dict[str, str] = {}
    missing: list[dict[str, str]] = []

    for item in requested_values:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name", "")).strip()
        if not raw_name:
            continue
        normalized = _normalize(raw_name)
        canonical = _ALIASES.get(normalized, normalized)

        env_value = (
            source_env.get(f"DOCS_EVAL_USER_INPUT_{canonical}")
            or source_env.get(canonical)
        )
        if _looks_secret(canonical, item):
            if allow_secrets and env_value:
                provided[raw_name] = env_value
            else:
                missing.append({
                    "name": raw_name,
                    "why_not_provided": (
                        "This looks like a secret/admin value. Set "
                        f"DOCS_EVAL_USER_INPUT_{canonical} and "
                        "DOCS_EVAL_ALLOW_SECRET_USER_INPUT=1 to provide it."
                    ),
                })
            continue

        value = env_value or defaults.get(canonical)
        if value:
            provided[raw_name] = value
        else:
            missing.append({
                "name": raw_name,
                "why_not_provided": "No benchmark value is configured for this request.",
            })

    response = {
        "provided": bool(provided),
        "values": provided,
        "missing": missing,
        "message": (
            "Use these benchmark-provided values exactly as user-supplied ZeroDev configuration."
            if provided
            else "No benchmark value was available for the requested inputs."
        ),
        "request": {
            "reason": str(args.get("reason", "")),
            "requested_values": requested_values,
        },
    }
    return response
