"""Generate human-readable reports from RunResult lists.

Output is markdown by default. Structure:
- Headline table: pass rate per (target × mode) averaged across use cases & runs
- Per-use-case breakdown
- Failure category breakdown
- Cost summary
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .runner import RunResult


def _mean(xs: list[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def _med_int(xs: list[int]) -> int:
    return int(statistics.median(xs)) if xs else 0


def write_summary_json(results: list[RunResult], out_path: Path) -> None:
    """Persist raw results so the report can be regenerated without rerunning."""
    serialized = []
    for r in results:
        d = asdict(r)
        # Paths aren't JSON-serializable
        d["transcript_path"] = str(r.transcript_path)
        d["code_dir"] = str(r.code_dir)
        if r.ai_review_path is not None:
            d["ai_review_path"] = str(r.ai_review_path)
        serialized.append(d)
    out_path.write_text(json.dumps(serialized, indent=2))


def load_summary_json(path: Path) -> list[RunResult]:
    raw = json.loads(path.read_text())
    out = []
    _known = {f.name for f in RunResult.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    for d in raw:
        d["transcript_path"] = Path(d["transcript_path"])
        d["code_dir"] = Path(d["code_dir"])
        if d.get("ai_review_path"):
            d["ai_review_path"] = Path(d["ai_review_path"])
        # Drop unknown keys so old summaries load cleanly after schema changes.
        d = {k: v for k, v in d.items() if k in _known}
        out.append(RunResult(**d))
    return out


def render_rich_summary(results: list[RunResult], console: Console) -> None:
    """Print a compact summary table to the terminal after a run completes."""
    if not results:
        console.print("[yellow]No results to summarize.[/yellow]")
        return

    targets = sorted({r.target_name for r in results})
    modes = sorted({r.mode for r in results})
    use_cases = sorted({r.use_case_id for r in results})

    # ── Pass rate table ──────────────────────────────────────────────────────
    pass_table = Table(title="Pass rate by target × mode", show_lines=True)
    pass_table.add_column("target", style="bold")
    for mode in modes:
        pass_table.add_column(mode, justify="center")
    pass_table.add_column("overall", justify="center", style="bold")

    for target in targets:
        row: list[Text | str] = [target]
        for mode in modes:
            cells = [r for r in results if r.target_name == target and r.mode == mode]
            row.append(_rich_pass_rate(cells))
        all_cells = [r for r in results if r.target_name == target]
        row.append(_rich_pass_rate(all_cells))
        pass_table.add_row(*row)

    console.print()
    console.print(pass_table)

    # ── Median time table ────────────────────────────────────────────────────
    time_table = Table(title="Median time-to-pass (seconds, passing cells only)",
                       show_lines=True)
    time_table.add_column("target", style="bold")
    for mode in modes:
        time_table.add_column(mode, justify="center")
    time_table.add_column("overall", justify="center", style="bold")

    for target in targets:
        row = [target]
        for mode in modes:
            cells = [r for r in results if r.target_name == target
                     and r.mode == mode and r.passed]
            row.append(_fmt_median_time(cells) if cells else "—")
        all_cells = [r for r in results if r.target_name == target and r.passed]
        row.append(_fmt_median_time(all_cells) if all_cells else "—")
        time_table.add_row(*row)

    console.print(time_table)

    # ── Capabilities table (targets that have been discovered) ────────────────
    targets_with_caps = [
        r for r in results if r.discovered_capabilities is not None
    ]
    if targets_with_caps:
        cap_seen: dict[str, dict] = {}
        for r in targets_with_caps:
            if r.target_name not in cap_seen and r.discovered_capabilities:
                cap_seen[r.target_name] = r.discovered_capabilities
        cap_table = Table(title="Discovered site capabilities", show_lines=True)
        cap_table.add_column("target", style="bold")
        cap_table.add_column("llms-full.txt", justify="center")
        cap_table.add_column("llms.txt", justify="center")
        cap_table.add_column("skill.md", justify="center")
        cap_table.add_column("mcp", justify="center")
        cap_table.add_column("markdown", justify="center")
        for tname, caps in sorted(cap_seen.items()):
            cap_table.add_row(
                tname,
                "[green]✓[/green]" if caps.get("has_llms_full_txt") else "[dim]·[/dim]",
                "[green]✓[/green]" if caps.get("has_llms_txt") else "[dim]·[/dim]",
                "[green]✓[/green]" if caps.get("has_skill_md") else "[dim]·[/dim]",
                "[green]✓[/green]" if caps.get("has_mcp") else "[dim]·[/dim]",
                ", ".join(caps.get("markdown_suffixes") or []) or "[dim]·[/dim]",
            )
        console.print(cap_table)

    # ── Observability summary ────────────────────────────────────────────────
    obs_table = Table(title="Agent observability (medians)", show_lines=True)
    obs_table.add_column("target", style="bold")
    obs_table.add_column("mode", justify="center")
    obs_table.add_column("reads", justify="right")
    obs_table.add_column("writes", justify="right")
    obs_table.add_column("grader calls", justify="right")
    obs_table.add_column("turns→grader", justify="right")
    for target in targets:
        for mode in modes:
            cells = [r for r in results if r.target_name == target and r.mode == mode]
            if not cells:
                continue
            obs_table.add_row(
                target, mode,
                f"{_med_int([r.file_reads for r in cells])}",
                f"{_med_int([r.file_writes for r in cells])}",
                f"{_med_int([r.grader_calls for r in cells])}",
                f"{_med_int([r.turns_to_first_grader for r in cells if r.turns_to_first_grader])}",
            )
    console.print(obs_table)

    # ── Failure summary ──────────────────────────────────────────────────────
    failed = [r for r in results if not r.passed]
    if failed:
        cats: dict[str, int] = defaultdict(int)
        for r in failed:
            cats[r.failure_category or "unknown"] += 1
        parts = ", ".join(f"{cat}: {n}" for cat, n in
                          sorted(cats.items(), key=lambda x: -x[1]))
        console.print(f"\n[red]Failures ({len(failed)}/{len(results)}):[/red] {parts}")
    else:
        console.print(f"\n[green]All {len(results)} cells passed.[/green]")

    # ── Quick stats ──────────────────────────────────────────────────────────
    total_in = sum(r.total_input_tokens for r in results)
    total_out = sum(r.total_output_tokens for r in results)
    console.print(
        f"[dim]Use cases: {len(use_cases)}  |  "
        f"Targets: {len(targets)}  |  "
        f"Tokens: {total_in + total_out:,} in+out[/dim]"
    )


def _rich_pass_rate(cells: list[RunResult]) -> Text:
    if not cells:
        return Text("—", style="dim")
    n_pass = sum(1 for c in cells if c.passed)
    rate = n_pass / len(cells)
    label = f"{rate*100:.0f}% ({n_pass}/{len(cells)})"
    if rate >= 0.8:
        return Text(label, style="green")
    if rate >= 0.5:
        return Text(label, style="yellow")
    return Text(label, style="red")


def render_markdown(results: list[RunResult]) -> str:
    if not results:
        return "# docs-eval report\n\n_No results._\n"

    targets = sorted({r.target_name for r in results})
    modes = sorted({r.mode for r in results})
    use_cases = sorted({r.use_case_id for r in results})

    lines: list[str] = []
    lines.append("# docs-eval report")
    lines.append("")
    lines.append(f"- Use cases: {len(use_cases)}")
    lines.append(f"- Targets: {len(targets)}")
    lines.append(f"- Modes: {', '.join(modes)}")
    lines.append(f"- Total cells: {len(results)}")
    lines.append("")

    # ---- Headline: target × mode pass rate ----
    lines.append("## Pass rate by target × mode")
    lines.append("")
    header = ["target"] + modes + ["all modes"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")

    for target in targets:
        row = [target]
        for mode in modes:
            cells = [r for r in results if r.target_name == target and r.mode == mode]
            row.append(_fmt_pass_rate(cells))
        all_cells = [r for r in results if r.target_name == target]
        row.append(_fmt_pass_rate(all_cells))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # ---- Time-to-working ----
    lines.append("## Median time-to-working-code (seconds, passing cells only)")
    lines.append("")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for target in targets:
        row = [target]
        for mode in modes:
            cells = [r for r in results if r.target_name == target
                     and r.mode == mode and r.passed]
            row.append(_fmt_median_time(cells))
        all_cells = [r for r in results if r.target_name == target and r.passed]
        row.append(_fmt_median_time(all_cells))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # ---- Pass@1 (strict) ----
    lines.append("## Pass@1 rate (first grader call passed — strict one-shot)")
    lines.append("")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for target in targets:
        row = [target]
        for mode in modes:
            cells = [r for r in results if r.target_name == target and r.mode == mode]
            row.append(_fmt_pass_at_1(cells))
        all_cells = [r for r in results if r.target_name == target]
        row.append(_fmt_pass_at_1(all_cells))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # ---- Discovered capabilities ----
    cap_seen: dict[str, dict] = {}
    for r in results:
        if r.target_name not in cap_seen and r.discovered_capabilities:
            cap_seen[r.target_name] = r.discovered_capabilities
    if cap_seen:
        lines.append("## Discovered site capabilities")
        lines.append("")
        lines.append("| target | llms-full.txt | llms.txt | skill.md | mcp | markdown |")
        lines.append("|---|---|---|---|---|---|")
        for tname, caps in sorted(cap_seen.items()):
            lines.append(
                f"| {tname} "
                f"| {'✓' if caps.get('has_llms_full_txt') else '·'} "
                f"| {'✓' if caps.get('has_llms_txt') else '·'} "
                f"| {'✓' if caps.get('has_skill_md') else '·'} "
                f"| {'✓' if caps.get('has_mcp') else '·'} "
                f"| {', '.join(caps.get('markdown_suffixes') or []) or '·'} |"
            )
        lines.append("")

    # ---- Observability: agent effort ----
    lines.append("## Agent effort (medians per target × mode)")
    lines.append("")
    lines.append("| target | mode | file reads | file writes | grader calls | turns→grader | turns→pass |")
    lines.append("|---|---|---|---|---|---|---|")
    for target in targets:
        for mode in modes:
            cells = [r for r in results if r.target_name == target and r.mode == mode]
            if not cells:
                continue
            ttg = [r.turns_to_first_grader for r in cells if r.turns_to_first_grader]
            tts = [r.turns_to_success for r in cells if r.turns_to_success]
            lines.append(
                f"| {target} | {mode} "
                f"| {_med_int([r.file_reads for r in cells])} "
                f"| {_med_int([r.file_writes for r in cells])} "
                f"| {_med_int([r.grader_calls for r in cells])} "
                f"| {_med_int(ttg) if ttg else '—'} "
                f"| {_med_int(tts) if tts else '—'} |"
            )
    lines.append("")

    # ---- Resource inventory ----
    resource_rows = [(r, res) for r in results for res in r.doc_resources]
    if resource_rows:
        lines.append("## Documentation resource usage")
        lines.append("")
        # Aggregate by (target, mode, url)
        agg: dict[tuple[str, str, str], dict] = {}
        for r, res in resource_rows:
            key = (r.target_name, r.mode, res["url"])
            if key not in agg:
                agg[key] = {**res, "target": r.target_name, "mode": r.mode, "run_count": 0}
            agg[key]["run_count"] += 1
            agg[key]["times_accessed"] = (
                agg[key].get("times_accessed", 0) + res.get("times_accessed", 0)
            )
        lines.append("| target | mode | resource_type | url | times accessed |")
        lines.append("|---|---|---|---|---|")
        for key, entry in sorted(agg.items()):
            url_short = entry["url"][:80]
            lines.append(
                f"| {entry['target']} | {entry['mode']} "
                f"| {entry['resource_type']} | {url_short} "
                f"| {entry.get('times_accessed', '—')} |"
            )
        lines.append("")

    # ---- User input requests ----
    input_rows = [r for r in results if r.requested_user_inputs]
    if input_rows:
        lines.append("## User input requests")
        lines.append("")
        lines.append("| target | mode | run | requested | provided | missing |")
        lines.append("|---|---|---|---|---|---|")
        for r in sorted(input_rows, key=lambda r: (r.target_name, r.mode, r.run_idx)):
            requested: list[str] = []
            provided: list[str] = []
            missing: list[str] = []
            for entry in r.requested_user_inputs:
                request = entry.get("request", entry)
                response = entry.get("response", {})
                for item in request.get("requested_values") or []:
                    if isinstance(item, dict) and item.get("name"):
                        requested.append(str(item["name"]))
                provided.extend(str(k) for k in (response.get("values") or {}).keys())
                missing.extend(
                    str(item.get("name", ""))
                    for item in (response.get("missing") or [])
                    if isinstance(item, dict)
                )
            lines.append(
                f"| {r.target_name} | {r.mode} | r{r.run_idx} "
                f"| {', '.join(requested) or '—'} "
                f"| {', '.join(provided) or '—'} "
                f"| {', '.join(missing) or '—'} |"
            )
        lines.append("")

    # ---- Self-report summary ----
    self_reported = [r for r in results if r.agent_self_report is not None]
    if self_reported:
        lines.append("## Agent self-reports")
        lines.append("")
        lines.append("| target | mode | run | most_useful | used_prior_knowledge | mismatches |")
        lines.append("|---|---|---|---|---|---|")
        for r in sorted(self_reported, key=lambda r: (r.target_name, r.mode, r.run_idx)):
            sr = r.agent_self_report or {}
            most_useful = sr.get("most_useful_resource") or "—"
            if isinstance(most_useful, str) and len(most_useful) > 60:
                most_useful = most_useful[:57] + "…"
            mismatches = len(r.self_report_mismatches)
            lines.append(
                f"| {r.target_name} | {r.mode} | r{r.run_idx} "
                f"| {most_useful} "
                f"| {'yes' if sr.get('used_prior_knowledge') else 'no'} "
                f"| {mismatches} |"
            )
        lines.append("")
        # Mismatch details
        mismatched = [r for r in self_reported if r.self_report_mismatches]
        if mismatched:
            lines.append("### Self-report mismatches (tool logs vs. agent claims)")
            lines.append("")
            for r in mismatched:
                lines.append(f"**{r.use_case_id} / {r.target_name} / {r.mode} / r{r.run_idx}**")
                for m in r.self_report_mismatches:
                    lines.append(f"- {m}")
            lines.append("")
        lines.append("### Agent solution notes")
        lines.append("")
        for r in sorted(self_reported, key=lambda r: (r.target_name, r.mode, r.run_idx)):
            sr = r.agent_self_report or {}
            lines.append(f"**{r.use_case_id} / {r.target_name} / {r.mode} / r{r.run_idx}**")
            if sr.get("approach_summary"):
                lines.append(f"- Approach: {sr['approach_summary']}")
            for label, key in (
                ("Steps", "steps_taken"),
                ("Challenges", "challenges_faced"),
                ("How overcome", "how_challenges_were_overcome"),
                ("Key APIs", "key_apis_used"),
            ):
                values = sr.get(key) or []
                if values:
                    if isinstance(values, list):
                        lines.append(f"- {label}: " + "; ".join(str(v) for v in values))
                    else:
                        lines.append(f"- {label}: {values}")
            lines.append("")

    # ---- AI review summary ----
    ai_reviewed = [r for r in results if r.ai_review is not None]
    if ai_reviewed:
        lines.append("## AI quality review")
        lines.append("")
        lines.append("| target | mode | run | score | verdict | likely real-world | critical/major issues |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in sorted(ai_reviewed, key=lambda r: (r.target_name, r.mode, r.run_idx)):
            review = r.ai_review or {}
            issues = review.get("issues") or []
            issue_count = 0
            if isinstance(issues, list):
                issue_count = sum(
                    1 for issue in issues
                    if isinstance(issue, dict) and issue.get("severity") in ("critical", "major")
                )
            likely = "yes" if review.get("would_likely_work_real_world") else "no"
            lines.append(
                f"| {r.target_name} | {r.mode} | r{r.run_idx} "
                f"| {review.get('overall_score', '—')} "
                f"| {review.get('verdict', '—')} "
                f"| {likely} "
                f"| {issue_count} |"
            )
        lines.append("")
        for r in sorted(ai_reviewed, key=lambda r: (r.target_name, r.mode, r.run_idx)):
            review = r.ai_review or {}
            issues = review.get("issues") or []
            if not issues:
                continue
            lines.append(f"### AI review issues: {r.use_case_id} / {r.target_name} / {r.mode} / r{r.run_idx}")
            lines.append("")
            for issue in issues[:8]:
                if not isinstance(issue, dict):
                    continue
                severity = issue.get("severity", "issue")
                category = issue.get("category", "")
                evidence = issue.get("evidence", "")
                recommendation = issue.get("recommendation", "")
                lines.append(f"- {severity}: {category} — {evidence}")
                if recommendation:
                    lines.append(f"  Recommendation: {recommendation}")
            lines.append("")
        docs_reviews = [
            r for r in ai_reviewed
            if isinstance((r.ai_review or {}).get("zerodev_docs_helpfulness"), dict)
        ]
        if docs_reviews:
            lines.append("### ZeroDev docs helpfulness")
            lines.append("")
            lines.append("| target | mode | run | score | assessment |")
            lines.append("|---|---|---|---|---|")
            for r in sorted(docs_reviews, key=lambda r: (r.target_name, r.mode, r.run_idx)):
                docs_review = (r.ai_review or {}).get("zerodev_docs_helpfulness") or {}
                assessment = str(docs_review.get("assessment", ""))
                if len(assessment) > 120:
                    assessment = assessment[:117] + "..."
                lines.append(
                    f"| {r.target_name} | {r.mode} | r{r.run_idx} "
                    f"| {docs_review.get('score', '—')} | {assessment} |"
                )
            lines.append("")

    # ---- Human review summary (only shown if any results have it) ----
    reviewed = [r for r in results if r.human_review_passed is not None]
    if reviewed:
        lines.append("## Human review results")
        lines.append("")
        hr_pass = sum(1 for r in reviewed if r.human_review_passed)
        lines.append(f"- Reviewed: {len(reviewed)} cells")
        lines.append(f"- Human-confirmed working: {hr_pass}/{len(reviewed)}")
        lines.append("")
        lines.append("| use case | target | mode | tsc | human | notes |")
        lines.append("|---|---|---|---|---|---|")
        for r in sorted(reviewed, key=lambda r: (r.use_case_id, r.target_name, r.mode)):
            hr_icon = "✅" if r.human_review_passed else "❌"
            lines.append(
                f"| {r.use_case_id} | {r.target_name} | {r.mode} | "
                f"{'✅' if r.passed else '❌'} | {hr_icon} | "
                f"{r.human_review_notes or ''} |"
            )
        lines.append("")

    # ---- Per-use-case breakdown ----
    lines.append("## Per use case")
    lines.append("")
    for uc in use_cases:
        lines.append(f"### {uc}")
        lines.append("")
        has_hr = any(r.human_review_passed is not None
                     for r in results if r.use_case_id == uc)
        if has_hr:
            lines.append("| target | mode | pass | pass@1 | turns | seconds | human | failure |")
            lines.append("|---|---|---|---|---|---|---|---|")
        else:
            lines.append("| target | mode | pass | pass@1 | turns | seconds | failure |")
            lines.append("|---|---|---|---|---|---|---|")
        for r in sorted([r for r in results if r.use_case_id == uc],
                        key=lambda r: (r.target_name, r.mode, r.run_idx)):
            hr_col = ""
            if has_hr:
                if r.human_review_passed is True:
                    hr_col = " ✅ |"
                elif r.human_review_passed is False:
                    hr_col = f" ❌ {r.human_review_notes} |"
                else:
                    hr_col = " — |"
            lines.append(
                f"| {r.target_name} | {r.mode} | "
                f"{'✅' if r.passed else '❌'} | "
                f"{'✓' if r.pass_at_1 else '·'} | "
                f"{r.turns} | {r.wall_seconds:.0f} |"
                + (hr_col if has_hr else "")
                + f" {r.failure_category or ''} |"
            )
        lines.append("")

    # ---- Failure categories ----
    lines.append("## Failure categories")
    lines.append("")
    cats: dict[str, int] = defaultdict(int)
    for r in results:
        if not r.passed and r.failure_category:
            cats[r.failure_category] += 1
    if cats:
        lines.append("| category | count |")
        lines.append("|---|---|")
        for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
            lines.append(f"| {cat} | {n} |")
    else:
        lines.append("_No failures._")
    lines.append("")

    # ---- Cost ----
    total_in = sum(r.total_input_tokens for r in results)
    total_out = sum(r.total_output_tokens for r in results)
    lines.append("## Token usage")
    lines.append("")
    lines.append(f"- Total input tokens: {total_in:,}")
    lines.append(f"- Total output tokens: {total_out:,}")
    lines.append("")

    return "\n".join(lines)


def _fmt_pass_rate(cells: list[RunResult]) -> str:
    if not cells:
        return "—"
    rate = sum(1 for c in cells if c.passed) / len(cells)
    return f"{rate*100:.0f}% ({sum(1 for c in cells if c.passed)}/{len(cells)})"


def _fmt_pass_at_1(cells: list[RunResult]) -> str:
    if not cells:
        return "—"
    rate = sum(1 for c in cells if c.pass_at_1) / len(cells)
    return f"{rate*100:.0f}%"


def _fmt_median_time(cells: list[RunResult]) -> str:
    if not cells:
        return "—"
    med = statistics.median([c.wall_seconds for c in cells])
    return f"{med:.0f}s"
