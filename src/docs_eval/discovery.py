"""Discovery phase: probe a docs site for AI-facing resources.

Run before each eval cell. Produces a SiteCapabilities object that the runner
can disclose to the agent (auto-informed) or withhold (auto-blind).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

_TIMEOUT = 10.0

# Ordered: probe llms-full.txt before llms.txt so the flag is set correctly.
_TEXT_PROBES: list[tuple[str, str]] = [
    ("llms-full.txt", "/llms-full.txt"),
    ("llms.txt", "/llms.txt"),
    ("skill.md", "/skill.md"),
]

_AI_LINK_KEYWORDS = ("llm", "ai", "mcp", "skill", "agent", "context", "copilot")


@dataclass
class ResourceProvenance:
    url: str
    resource_type: str        # "llms-full.txt", "llms.txt", "skill.md", "mcp", …
    sha256: str
    raw_char_count: int
    injected_char_count: int  # updated by runner after truncation
    truncated: bool           # updated by runner
    first_500: str
    last_500: str


@dataclass
class SiteCapabilities:
    base_url: str
    has_llms_txt: bool = False
    has_llms_full_txt: bool = False
    has_skill_md: bool = False
    has_mcp: bool = False
    mcp_url: str | None = None
    markdown_suffixes: list[str] = field(default_factory=list)
    discovered_links: list[str] = field(default_factory=list)
    resources: list[ResourceProvenance] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def agent_summary(self) -> str:
        """Human-readable summary for injection into the agent's context."""
        lines = [f"AI-facing resources discovered at {self.base_url}:"]
        for r in self.resources:
            lines.append(f"  [{r.resource_type}] {r.url}  ({r.raw_char_count:,} chars)")
        if self.has_mcp and self.mcp_url:
            lines.append(f"  [mcp] {self.mcp_url}")
        elif self.has_mcp:
            lines.append(f"  [mcp] MCP endpoint available")
        if self.markdown_suffixes:
            lines.append(
                f"  [markdown] Pages available with suffix: {', '.join(self.markdown_suffixes)}"
            )
        if not self.resources and not self.has_mcp:
            lines.append("  (none found — rely on web browsing or prior knowledge)")
        return "\n".join(lines)


# ── Probing helpers ─────────────────────────────────────────────────────────

def _fetch_probe(url: str, rtype: str) -> ResourceProvenance | None:
    try:
        r = httpx.get(
            url, timeout=_TIMEOUT, follow_redirects=True,
            headers={"User-Agent": "docs-eval-discovery/0.1"},
        )
        if r.status_code >= 400:
            return None
        text = r.text
        sha = hashlib.sha256(text.encode()).hexdigest()
        return ResourceProvenance(
            url=url,
            resource_type=rtype,
            sha256=sha,
            raw_char_count=len(text),
            injected_char_count=0,
            truncated=False,
            first_500=text[:500],
            last_500=text[-500:] if len(text) > 500 else text,
        )
    except Exception:
        return None


def _check_mcp(explicit_endpoint: str | None, base_url: str) -> tuple[bool, str | None]:
    """Return (reachable, url). Probes explicit endpoint first, then /mcp."""
    candidates = []
    if explicit_endpoint:
        candidates.append(explicit_endpoint)
    candidates.append(base_url.rstrip("/") + "/mcp")
    for url in candidates:
        try:
            r = httpx.get(
                url, timeout=_TIMEOUT, follow_redirects=True,
                headers={"User-Agent": "docs-eval-discovery/0.1",
                         "Accept": "application/json, text/event-stream"},
            )
            # MCP returns 4xx for GET (expects POST/SSE), but anything other than
            # a CDN 404 means the endpoint exists.
            if r.status_code != 404:
                return True, url
        except Exception:
            pass
    return False, None


def _extract_ai_links(base_url: str) -> list[str]:
    try:
        r = httpx.get(
            base_url, timeout=_TIMEOUT, follow_redirects=True,
            headers={"User-Agent": "docs-eval-discovery/0.1"},
        )
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', r.text)
        links: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            low = href.lower()
            if not any(kw in low for kw in _AI_LINK_KEYWORDS):
                continue
            full = href if href.startswith("http") else (
                base_url.rstrip("/") + href if href.startswith("/") else None
            )
            if full and full not in seen:
                seen.add(full)
                links.append(full)
            if len(links) >= 10:
                break
        return links
    except Exception:
        return []


# ── Public API ───────────────────────────────────────────────────────────────

def discover(
    base_url: str,
    mcp_endpoint: str | None = None,
    markdown_suffix: str | None = None,
) -> SiteCapabilities:
    """Probe a docs site and return a structured capability object."""
    base = base_url.rstrip("/")
    caps = SiteCapabilities(base_url=base_url)

    for rtype, path in _TEXT_PROBES:
        prov = _fetch_probe(base + path, rtype)
        if prov:
            caps.resources.append(prov)
            if rtype == "llms-full.txt":
                caps.has_llms_full_txt = True
            elif rtype == "llms.txt":
                caps.has_llms_txt = True
            elif rtype == "skill.md":
                caps.has_skill_md = True

    caps.has_mcp, caps.mcp_url = _check_mcp(mcp_endpoint, base_url)

    if markdown_suffix:
        caps.markdown_suffixes = [markdown_suffix]

    caps.discovered_links = _extract_ai_links(base_url)
    return caps


# Simple in-process cache — one discovery probe per target per process run.
_cache: dict[str, SiteCapabilities] = {}


def get_capabilities(
    base_url: str,
    mcp_endpoint: str | None,
    markdown_suffix: str | None,
    target_name: str,
    *,
    force: bool = False,
) -> SiteCapabilities:
    """Cached wrapper around discover()."""
    if force or target_name not in _cache:
        _cache[target_name] = discover(base_url, mcp_endpoint, markdown_suffix)
    return _cache[target_name]
