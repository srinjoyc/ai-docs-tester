"""docs-eval CLI.

Two subcommands:
- `run`: execute cells and write results
- `report`: regenerate a markdown report from saved results.json

Kept deliberately thin — orchestration only, no business logic.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console
from rich.progress import (BarColumn, Progress, SpinnerColumn,
                            TaskProgressColumn, TextColumn, TimeElapsedColumn)
from rich.table import Table

from .config import CLASSIC_MODES, MODES, load_targets, load_use_cases
from .reporter import (load_summary_json, render_markdown, render_rich_summary,
                        write_summary_json)
from .runner import RunnerConfig, run_cell
from .smoke import run_smoke

console = Console()


@click.group()
def main():
    """Internal eval for AI-agent doc consumption."""


@main.command()
@click.option("--use-cases", "use_case_patterns", multiple=True,
              help="Glob(s) under use_cases/, e.g. 'zerodev/*' or "
                   "'zerodev/01-*.yaml'. Repeatable. Defaults to all.")
@click.option("--use-cases-root", default="use_cases", show_default=True,
              type=click.Path(file_okay=False, exists=True))
@click.option("--targets", "target_names", default="",
              help="Comma-separated target names. Defaults to all enabled.")
@click.option("--targets-file", default="targets/targets.yaml", show_default=True,
              type=click.Path(dir_okay=False, exists=True))
@click.option("--modes", default=",".join(CLASSIC_MODES), show_default=True,
              help="Comma-separated subset of: " + ",".join(MODES))
@click.option("--runs", type=int, default=3, show_default=True,
              help="Runs per cell. LLMs are stochastic; >1 gives variance.")
@click.option("--out", "out_dir", default=None,
              type=click.Path(file_okay=False),
              help="Output directory. Defaults to results/YYYYMMDD-HHMM.")
@click.option("--model", default=None,
              help="Override model. Default from $DOCS_EVAL_MODEL.")
@click.option("--backend", default=None,
              type=click.Choice(["auto", "claude", "openai", "codex"]),
              help="Agent backend. Defaults to $DOCS_EVAL_BACKEND or auto.")
@click.option("--verbose", is_flag=True, help="Print agent/grader output live.")
@click.option("--human-review", "human_review", is_flag=True,
              help="After each passing cell, start the app and ask you to confirm "
                   "it works. Requires human_check defined in the use case YAML.")
@click.option("--dry-run", is_flag=True, help="Print the plan and exit.")
@click.option("--skip-smoke", is_flag=True,
              help="Skip smoke tests (scaffold/typecheck/mcp checks) before running.")
def run(use_case_patterns, use_cases_root, target_names, targets_file,
        modes, runs, out_dir, model, backend, verbose, human_review, dry_run, skip_smoke):
    """Execute the eval matrix."""
    uc_root = Path(use_cases_root)
    patterns = list(use_case_patterns) or None
    cases = load_use_cases(uc_root, patterns)
    if not cases:
        console.print("[red]No use cases matched.[/red]")
        sys.exit(1)

    names = [n.strip() for n in target_names.split(",") if n.strip()] or None
    targets = load_targets(Path(targets_file), names)
    if not targets:
        console.print("[red]No targets matched.[/red]")
        sys.exit(1)

    mode_list = [m.strip() for m in modes.split(",") if m.strip()]
    bad = [m for m in mode_list if m not in MODES]
    if bad:
        console.print(f"[red]Unknown modes: {bad}. Valid: {MODES}[/red]")
        sys.exit(1)

    # Only run cells where use_case.vendor matches target.vendor — otherwise
    # you'd test "Privy use case against ZeroDev docs" which is nonsense.
    plan = []
    for uc in cases:
        for t in targets:
            if uc.vendor != t.vendor:
                continue
            for mode in mode_list:
                for run_idx in range(runs):
                    plan.append((uc, t, mode, run_idx))

    if not plan:
        console.print("[red]Plan is empty — check vendor matches between use cases and targets.[/red]")
        sys.exit(1)

    # Show the plan
    table = Table(title="Run plan", show_lines=False)
    table.add_column("use case")
    table.add_column("target")
    table.add_column("mode")
    table.add_column("runs", justify="right")
    seen = set()
    for uc, t, mode, _ in plan:
        k = (uc.id, t.name, mode)
        if k in seen:
            continue
        seen.add(k)
        table.add_row(uc.id, t.name, mode, str(runs))
    console.print(table)
    console.print(f"Total cells: [bold]{len(plan)}[/bold]")

    if dry_run:
        return

    # ── Smoke tests: fast infra checks before spending any LLM tokens ─────────
    if not skip_smoke:
        unique_cases = list({uc.id: uc for uc, _, _, _ in plan}.values())
        project_root = Path(use_cases_root).parent.resolve()
        console.print(f"\n[bold]Smoke tests[/bold] ({len(unique_cases)} use case(s))…")
        smoke_results = run_smoke(unique_cases, project_root, verbose=verbose, check_grader=False)
        failures = [r for r in smoke_results if not r.passed]
        for r in smoke_results:
            icon = "[green]✓[/green]" if r.passed else "[red]✗[/red]"
            console.print(f"  {icon} {r.use_case_id}  {r.summary_line()}")
        if failures:
            console.print(f"\n[red bold]{len(failures)} smoke test(s) failed — fix infra before running LLM cells.[/red bold]")
            for r in failures:
                console.print(f"  [red]{r.use_case_id}:[/red] {r.first_error()[:200]}")
            sys.exit(1)
        console.print("[green]All smoke tests passed.[/green]\n")

    if out_dir is None:
        out_dir = Path("results") / datetime.now().strftime("%Y%m%d-%H%M")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = out_dir / "code"
    transcript_root = out_dir / "transcripts"

    cfg = RunnerConfig(
        work_root=work_root,
        transcript_root=transcript_root,
        model=model or RunnerConfig.model,
        backend=backend or RunnerConfig.backend,
        verbose=verbose,
        human_review=human_review,
    )

    if human_review:
        console.print("[yellow]Human review mode on — you'll be asked to confirm "
                      "each passing cell.[/yellow]")

    results = []
    # Human review needs interactive stdin, so we can't use Rich Progress at the
    # same time (it captures the terminal). Run without the progress bar when
    # human_review is active.
    if human_review:
        for i, (uc, t, mode, run_idx) in enumerate(plan, 1):
            console.print(f"  [{i}/{len(plan)}] {uc.id} / {t.name} / {mode} / r{run_idx}")
            try:
                result = run_cell(uc, t, mode, run_idx, cfg)
                results.append(result)
                status = "[green]✓[/green]" if result.passed else "[red]✗[/red]"
                hr = ""
                if result.human_review_passed is True:
                    hr = " · [green]human: ✓[/green]"
                elif result.human_review_passed is False:
                    hr = f" · [red]human: ✗[/red] {result.human_review_notes}"
                elif result.human_review_notes == "skipped":
                    hr = " · [dim]human: skipped[/dim]"
                console.print(
                    f"  {status} {uc.id} / {t.name} / {mode} / r{run_idx} "
                    f"— {result.turns}t / {result.wall_seconds:.0f}s{hr}"
                )
            except Exception as e:
                console.print(f"  [red]CRASH[/red] {uc.id}/{t.name}/{mode}/r{run_idx}: {e}")
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as prog:
            task = prog.add_task("Running cells", total=len(plan))
            for uc, t, mode, run_idx in plan:
                prog.update(task, description=f"{uc.id} / {t.name} / {mode} / r{run_idx}")
                try:
                    result = run_cell(uc, t, mode, run_idx, cfg)
                    results.append(result)
                    status = "[green]✓[/green]" if result.passed else "[red]✗[/red]"
                    console.print(
                        f"  {status} {uc.id} / {t.name} / {mode} / r{run_idx} "
                        f"— {result.turns}t / {result.wall_seconds:.0f}s "
                        f"{'' if result.passed else '— ' + (result.failure_category or '?')}"
                    )
                except Exception as e:
                    console.print(f"  [red]CRASH[/red] {uc.id}/{t.name}/{mode}/r{run_idx}: {e}")
                prog.advance(task)

    # Persist
    summary_path = out_dir / "summary.json"
    write_summary_json(results, summary_path)
    report_md = render_markdown(results)
    (out_dir / "report.md").write_text(report_md)

    # Terminal summary
    render_rich_summary(results, console)
    console.print(f"\n[bold]Saved to:[/bold] {out_dir}")
    console.print(f"  [dim]report.md · summary.json · transcripts/ · code/[/dim]")


@main.command()
@click.option("--use-cases", "use_case_patterns", multiple=True,
              help="Glob(s) under use_cases/. Defaults to all.")
@click.option("--use-cases-root", default="use_cases", show_default=True,
              type=click.Path(file_okay=False, exists=True))
@click.option("--no-grader", is_flag=True,
              help="Skip the grader check (even if a reference solution exists).")
@click.option("--verbose", is_flag=True)
def smoke(use_case_patterns, use_cases_root, no_grader, verbose):
    """Run scaffold/typecheck/mcp/grader smoke tests without any LLM calls."""
    uc_root = Path(use_cases_root)
    patterns = list(use_case_patterns) or None
    cases = load_use_cases(uc_root, patterns)
    if not cases:
        console.print("[red]No use cases matched.[/red]")
        sys.exit(1)

    project_root = uc_root.parent.resolve()
    console.print(f"[bold]Smoke tests[/bold] — {len(cases)} use case(s), "
                  f"grader={'off' if no_grader else 'on (if solution exists)'}")

    from rich.table import Table
    results = run_smoke(cases, project_root, verbose=verbose, check_grader=not no_grader)

    table = Table(show_lines=False)
    table.add_column("use case")
    table.add_column("scaffold", justify="center")
    table.add_column("typecheck", justify="center")
    table.add_column("mcp", justify="center")
    table.add_column("grader", justify="center")

    def cell(v: bool | None) -> str:
        if v is True:
            return "[green]✓[/green]"
        if v is False:
            return "[red]✗[/red]"
        return "[dim]—[/dim]"

    for r in results:
        table.add_row(r.use_case_id,
                      cell(r.scaffold_ok),
                      cell(r.typecheck_ok),
                      cell(r.mcp_ok),
                      cell(r.grader_ok))

    console.print(table)

    failures = [r for r in results if not r.passed]
    if failures:
        console.print(f"\n[red bold]{len(failures)} failure(s):[/red bold]")
        for r in failures:
            console.print(f"  [red]{r.use_case_id}:[/red] {r.first_error()[:300]}")
        sys.exit(1)
    else:
        console.print(f"[green]All {len(results)} smoke tests passed.[/green]")


@main.command()
@click.argument("results_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--cell", default=None, help="Filter to one cell (partial match on filename).")
def show(results_dir, cell):
    """Print a readable transcript from a results directory."""
    import json as _json
    transcript_dir = Path(results_dir) / "transcripts"
    if not transcript_dir.exists():
        console.print(f"[red]No transcripts/ in {results_dir}[/red]")
        sys.exit(1)

    files = sorted(transcript_dir.glob("*.jsonl"))
    if cell:
        files = [f for f in files if cell in f.name]
    if not files:
        console.print("[red]No matching transcripts.[/red]")
        sys.exit(1)

    for path in files:
        console.print(f"\n[bold cyan]── {path.stem} ──[/bold cyan]")
        for line in path.open():
            e = _json.loads(line)
            kind = e.get("kind", "")
            data = e.get("data", {})
            if kind == "meta":
                console.print(f"  use_case={data.get('use_case')}  target={data.get('target')}  mode={data.get('mode')}")
            elif kind == "assistant":
                turn = data.get("turn", "?")
                text = (data.get("content") or "").strip()
                calls = data.get("tool_calls") or []
                if text:
                    console.print(f"\n  [bold]turn {turn}[/bold]  {text[:200]}")
                for tc in calls:
                    name = tc.get("name", "").replace("mcp__docs-eval__", "")
                    try:
                        args = _json.loads(tc.get("arguments", "{}"))
                    except Exception:
                        args = {}
                    arg = args.get("path") or args.get("url") or ""
                    console.print(f"  [blue]  → {name}[/blue] {arg}")
            elif kind == "tool_result":
                tool = data.get("tool", "")
                summary = data.get("result_summary", "")
                if tool == "run_grader":
                    color = "green" if summary == "pass" else "red"
                    console.print(f"  [{color}]  ← grader: {summary}[/{color}]")
                elif tool in ("fetch_url",):
                    console.print(f"  [dim]  ← {tool}: {str(summary)[:80]}[/dim]")
            elif kind == "resource_access":
                url = data.get("url", "")
                rtype = data.get("resource_type", "")
                console.print(f"  [yellow]  [source] {rtype}: {url}[/yellow]")
            elif kind == "summary":
                passed = data.get("passed")
                turns = data.get("turns")
                grader_calls = data.get("grader_calls", 0)
                icon = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
                console.print(f"\n  {icon}  {turns} turns  {grader_calls} grader calls")


@main.command()
@click.argument("results_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--format", "fmt", default="markdown",
              type=click.Choice(["markdown", "json"]))
def report(results_dir, fmt):
    """Regenerate a report from an existing results directory."""
    summary = Path(results_dir) / "summary.json"
    if not summary.exists():
        console.print(f"[red]No summary.json in {results_dir}[/red]")
        sys.exit(1)
    results = load_summary_json(summary)
    render_rich_summary(results, console)
    if fmt == "markdown":
        click.echo(render_markdown(results))
    else:
        click.echo(summary.read_text())


if __name__ == "__main__":
    main()
