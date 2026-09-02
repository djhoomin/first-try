"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .interceptor import Policy
from .mcp_client import McpSession, ResourceTools, SessionWithResources
from .report import render_report
from .runner_loop import run_suite
from .tasks import load_tasks


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="first-try", description="Usability benchmark for agent-facing APIs")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the suite against an MCP server")
    run.add_argument("--tasks", default="tasks", help="task file or directory")
    run.add_argument("--only", default="", help="comma-separated task ids")
    run.add_argument("--runner", default="claude", choices=["claude", "openai"])
    run.add_argument("--model", default="")
    run.add_argument("--base-url", default="")
    run.add_argument("--stdio", default="", help='server command, e.g. "npx -y flux-mcp"')
    run.add_argument("--http", default="", help="server URL")
    run.add_argument("--header", action="append", default=[], help="Name: value, repeatable")
    run.add_argument("--dry-run", action="store_true", help="block every billable call")
    run.add_argument("--budget", type=float, default=5.0, help="run ceiling in USD")
    run.add_argument("--per-call-cap", type=float, default=1.0, help="block any single call above this")
    run.add_argument("--out", default="results", help="output directory")
    run.add_argument(
        "--resources", default="tools", choices=["tools", "none"],
        help="expose the server's MCP resources to the model as tools (default), "
             "or not at all, which reproduces a client that cannot reach them",
    )

    rep = sub.add_parser("report", help="re-render a report from saved results")
    rep.add_argument("--results", default="results/results.json")

    sub.add_parser("tasks", help="list the suite").add_argument("--tasks", default="tasks")
    return p


def _make_runner(args):
    """Build the runner. Raises SystemExit with something actionable."""
    try:
        if args.runner == "claude":
            from .runners import ClaudeRunner
            return ClaudeRunner(model=args.model or "claude-opus-5")
        from .runners import OpenAICompatRunner
        if not args.model:
            sys.exit("--model is required for the openai runner")
        return OpenAICompatRunner(model=args.model, base_url=args.base_url or None)
    except ImportError as exc:
        extra = "claude" if args.runner == "claude" else "openai"
        sys.exit(
            f"\nThe {args.runner} runner needs an SDK that is not installed: {exc}\n"
            f'  pip install -e ".[{extra}]"\n'
        )


def connection_help(args, exc: Exception) -> str:
    """Say what went wrong and what to try, rather than unwinding a stack.

    A benchmark that measures whether error messages let you recover has no
    business emitting a traceback when its own connection fails.
    """
    target = args.stdio or args.http
    lines = [
        "",
        f"Could not connect to the MCP server: {type(exc).__name__}: {exc}",
        f"  tried: {target}",
        "",
    ]
    if args.stdio:
        lines += [
            "The server command exited instead of speaking MCP. Usually one of:",
            "",
            "  - the package does not exist. Check the name is real before assuming",
            "    the transport is broken; npm prints its own 404 above this message.",
            "  - the server is hosted only, with no local package to run. Many are.",
            "    Bridge to it over stdio instead:",
            "",
            '      --stdio "npx -y mcp-remote https://<the server>"',
            "",
            "  - it needs credentials in the environment and quit without them.",
            "",
            "For FLUX specifically, the server is hosted and OAuth-only:",
            "",
            '  first-try run --stdio "npx -y mcp-remote https://mcp.bfl.ai" --dry-run',
            "",
            "A browser opens for sign-in on first use; tokens cache in ~/.mcp-auth.",
        ]
    else:
        lines += [
            "Check the URL, and whether the server needs auth headers (--header),",
            "or an OAuth flow this client does not perform. If it is OAuth-only,",
            'bridge over stdio: --stdio "npx -y mcp-remote <url>"',
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "tasks":
        for task in load_tasks(args.tasks):
            print(f"{task.id}  {task.title}  [{', '.join(task.axes)}]"
                  f"{'  (forced dry run)' if task.force_dry_run else ''}")
        return 0

    if args.command == "report":
        rows = json.loads(Path(args.results).read_text(encoding="utf-8"))
        print(render_report(rows))
        return 0

    tasks = load_tasks(args.tasks)
    if args.only:
        wanted = {t.strip() for t in args.only.split(",")}
        tasks = [t for t in tasks if t.id in wanted]
        if not tasks:
            sys.exit(f"no tasks matched {sorted(wanted)}")

    if not args.stdio and not args.http:
        sys.exit("supply --stdio or --http to say which server to benchmark")

    # Everything cheap and local is checked before anything expensive or
    # interactive. Connecting spawns a subprocess and may open a browser for
    # OAuth, and there is no excuse for making someone sit through that only to
    # fail on an import that could have been checked instantly.
    runner = _make_runner(args)

    session = McpSession()
    try:
        if args.stdio:
            parts = args.stdio.split()
            session.connect_stdio(parts[0], parts[1:])
        else:
            headers = dict(h.split(":", 1) for h in args.header)
            session.connect_http(args.http, {k: v.strip() for k, v in headers.items()})
    except Exception as exc:
        session.close()
        sys.exit(connection_help(args, exc))

    backend: object = session
    if args.resources == "tools":
        taken = {t["name"] for t in session.list_tools()}
        backend = SessionWithResources(session, ResourceTools(session, taken))

    policy = Policy(dry_run=args.dry_run, budget_usd=args.budget, per_call_cap_usd=args.per_call_cap)

    def progress(row):
        mark = "review" if row["needs_review"] else ("pass" if row["passed"] else "FAIL")
        print(f"  {row['task_id']:<5} {mark:<6} ${row['intended_usd']:.3f}  {row['first_tool'] or '-'}",
              file=sys.stderr)

    print(f"running {len(tasks)} tasks against {runner.name}"
          f"{' (dry run)' if args.dry_run else ''}"
          f", resources={args.resources}", file=sys.stderr)
    try:
        rows = run_suite(tasks, runner, backend, policy, on_result=progress,
                         resource_mode=args.resources)
    finally:
        session.close()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    serialisable = [{k: v for k, v in row.items() if k != "transcript"} for row in rows]
    (out / "results.json").write_text(json.dumps(serialisable, indent=2, default=str), encoding="utf-8")
    for row in rows:
        (out / f"transcript-{row['task_id']}-{row['runner'].replace(':', '-')}.json").write_text(
            row["transcript"].to_json(), encoding="utf-8"
        )
    report = render_report(serialisable)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
