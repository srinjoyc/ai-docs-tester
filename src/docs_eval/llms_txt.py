"""Fetch and cache llms-full.txt content.

Cached on disk so we don't re-download for every cell. Cache key is the URL.
TTL is generous (24h) because doc deployments don't change that fast and we'd
rather have fast reruns. Pass `force=True` to bust.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import httpx

CACHE_DIR = Path.home() / ".cache" / "docs-eval" / "llms-txt"
TTL_SECONDS = 24 * 3600


def _cache_path(url: str) -> Path:
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{h}.txt"


def fetch_llms_txt(url: str, *, force: bool = False, timeout: float = 30.0) -> str:
    """Fetch the URL, return its text. Caches on disk.

    Raises httpx.HTTPError on failure — caller decides what to do (most callers
    will downgrade the mode to `web` and log a warning).
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = _cache_path(url)

    if not force and cp.exists():
        age = time.time() - cp.stat().st_mtime
        if age < TTL_SECONDS:
            return cp.read_text()

    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    text = resp.text
    cp.write_text(text)
    return text


def truncate_for_context(text: str, max_chars: int = 40_000) -> tuple[str, bool]:
    """Cap llms-full.txt size so we don't blow the context window.

    Returns (text, was_truncated). 40k chars ≈ 10k tokens — fast and cheap
    for OpenAI. The head of llms-full.txt is usually quickstart + core API
    which is the highest-value content. A smarter version could do BM25
    against the prompt and pick relevant chunks; that's a v2.
    """
    if len(text) <= max_chars:
        return text, False
    # Keep the head — table of contents and quickstart are usually at the top
    # and are the highest-value content for an agent. A smarter version could
    # do BM25 against the prompt and pick relevant chunks; that's a v2.
    return text[:max_chars], True
