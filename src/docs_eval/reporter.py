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


def write_summary_json(results: list[RunResult], out_path: Path) -> None:
    """Persist raw results so the report can be regenerated without rerunning."""
    serialized = []
    for r in results:
        d = asdict(r)
        # Paths aren't JSON-serializable
        d["transcript_path"] = str(r.transcript_path)
        d["code_dir"] = str(r.code_dir)
        serialized.append(d)
    out_path.write_text(json.dumps(serialized, indent=2))


def load_summary_json(path: Path) -> list[RunResult]:
    raw = json.loads(path.read_text())
    out = []
    for d in raw:
        d["transcript_path"] = Path(d["transcript_path"])
        d["code_dir"] = Path(d["code_dir"])
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
