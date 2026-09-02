"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .interceptor import Policy
from .mcp_client import McpSession, ResourceTools, SessionWithResources
from .report import render_report
from .transcript import safe_name
from .fetch import download_media, fetch_outputs
from .review import render_review
from .verdicts import load_verdicts, record_verdict
from .runner_loop import run_suite
from .tasks import load_tasks


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="first-try", description="Usability benchmark for agent-facing APIs")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the suite against an MCP server")
    run.add_argument("--tasks", default="tasks", help="task file or directory")
    run.add_argument("--only", default="", help="comma-separated task ids")
    run.add_argument("--runner", default="claude", choices=["claude", "openai"])
    run.add_argument("--model", default="",
                     help="default claude-sonnet-5; opus costs several times more per task")
    run.add_argument("--no-cache", action="store_true",
                     help="disable prompt caching (slower and much more expensive)")
    run.add_argument("--base-url", default="",
                     help="API root for the openai runner, e.g. https://openrouter.ai/api/v1")
    run.add_argument("--api-key-env", default="OPENAI_API_KEY",
                     help="environment variable holding the key, e.g. OPENROUTER_API_KEY")
    run.add_argument("--stdio", default="", help='server command, e.g. "npx -y flux-mcp"')
    run.add_argument("--http", default="", help="server URL")
    run.add_argument("--header", action="append", default=[], help="Name: value, repeatable")
    run.add_argument("--dry-run", action="store_true", help="block every billable call")
    run.add_argument("--budget", type=float, default=5.0, help="run ceiling in USD")
    run.add_argument("--per-call-cap", type=float, default=1.0, help="block any single call above this")
    run.add_argument("--out", default="results", help="output directory")
    run.add_argument("--resume", action="store_true",
                     help="skip tasks already recorded in the output directory for this runner")
    run.add_argument("--call-timeout", type=float, default=300.0,
                     help="seconds to wait for one tool call before giving up on the transport")
    run.add_argument(
        "--resources", default="tools", choices=["tools", "none"],
        help="expose the server's MCP resources to the model as tools (default), "
             "or not at all, which reproduces a client that cannot reach them",
    )

    rep = sub.add_parser("report", help="re-render a report from saved results")
    rep.add_argument("--results", default="results/results.json")

    jud = sub.add_parser("judge", help="record a verdict on a task awaiting review")
    jud.add_argument("task_id")
    jud.add_argument("verdict", choices=["pass", "fail", "partial"])
    jud.add_argument("--note", default="")
    jud.add_argument("--out", default="results")

    pro = sub.add_parser("probe", help="read one resource or call one free tool, and print it")
    pro.add_argument("--resource", default="")
    pro.add_argument("--tool", default="")
    pro.add_argument("--args", default="{}", help="JSON arguments for --tool")
    pro.add_argument("--stdio", default="")
    pro.add_argument("--http", default="")
    pro.add_argument("--header", action="append", default=[])

    fet = sub.add_parser("fetch", help="resolve pending generations into finished images")
    fet.add_argument("--out", default="results")
    fet.add_argument("--stdio", default="")
    fet.add_argument("--http", default="")
    fet.add_argument("--header", action="append", default=[])
    fet.add_argument("--force", action="store_true",
                     help="re-resolve calls that already have media recorded")
    fet.add_argument("--no-download", action="store_true",
                     help="keep remote URLs only; they are signed and will expire")

    rev = sub.add_parser("review", help="build a contact sheet for the outstanding judgement calls")
    rev.add_argument("--out", default="results")

    sub.add_parser("tasks", help="list the suite").add_argument("--tasks", default="tasks")
    return p


def _make_runner(args):
    """Build the runner. Raises SystemExit with something actionable."""
    try:
        if args.runner == "claude":
            from .runners import ClaudeRunner
            return ClaudeRunner(model=args.model or "claude-sonnet-5",
                                cache=not args.no_cache)
        from .runners import OpenAICompatRunner
        if not args.model:
            sys.exit("--model is required for the openai runner")
        return OpenAICompatRunner(model=args.model, base_url=args.base_url or None,
                                  api_key_env=args.api_key_env)
    except RuntimeError as exc:
        sys.exit(f"\n{exc}\n")
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

    if args.command == "judge":
        entry = record_verdict(Path(args.out), args.task_id, args.verdict, args.note)
        print(f"{entry['task_id']}: {entry['verdict']}" + (f" - {entry['note']}" if entry["note"] else ""))
        return 0

    if args.command == "probe":
        if not args.stdio and not args.http:
            sys.exit("supply --stdio or --http")
        if not args.resource and not args.tool:
            sys.exit("supply --resource or --tool")
        session = McpSession()
        try:
            if args.stdio:
                parts = args.stdio.split()
                session.connect_stdio(parts[0], parts[1:])
            else:
                headers = dict(h.split(":", 1) for h in args.header)
                session.connect_http(args.http, {k: v.strip() for k, v in headers.items()})
            if args.resource:
                print(json.dumps(session.read_resource(args.resource), indent=2)[:200000])
            else:
                print(json.dumps(session.call_tool(args.tool, json.loads(args.args)), indent=2)[:200000])
        finally:
            session.close()
        return 0

    if args.command == "fetch":
        if not args.stdio and not args.http:
            sys.exit("supply --stdio or --http")
        session = McpSession()
        try:
            if args.stdio:
                parts = args.stdio.split()
                session.connect_stdio(parts[0], parts[1:])
            else:
                headers = dict(h.split(":", 1) for h in args.header)
                session.connect_http(args.http, {k: v.strip() for k, v in headers.items()})
            log = lambda m: print(m, file=sys.stderr)
            n = fetch_outputs(Path(args.out), session, log=log, force=args.force)
            print(f"resolved media for {n} job(s)")
            if not args.no_download:
                saved = download_media(Path(args.out), log=log)
                print(f"saved {saved} file(s) to {Path(args.out) / 'media'}")
        finally:
            session.close()
        return 0

    if args.command == "review":
        out = Path(args.out)
        rows = json.loads((out / "results.json").read_text(encoding="utf-8"))
        transcripts = {}
        for path in out.glob("transcript-*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            transcripts[data["task_id"]] = data
        page = out / "review.html"
        page.write_text(render_review(rows, transcripts, load_verdicts(out)), encoding="utf-8")
        print(f"wrote {page}")
        return 0

    if args.command == "report":
        rows = json.loads(Path(args.results).read_text(encoding="utf-8"))
        print(render_report(rows))
        return 0

    return _run_command(args)


def _run_command(args) -> int:
    """The run path: everything cheap and local happens before we connect."""
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

    session = McpSession(call_timeout=args.call_timeout)
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

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results_path, report_path = out / "results.json", out / "report.md"

    # Rows already on disk for this runner. Resuming is not a convenience: a
    # long live run that dies at task 14 would otherwise have to be paid for
    # twice.
    done: list[dict] = []
    if args.resume and results_path.exists():
        previous = json.loads(results_path.read_text(encoding="utf-8"))
        done = [r for r in previous if r.get("runner") == runner.name]
        already = {r["task_id"] for r in done}
        skipped = [t.id for t in tasks if t.id in already]
        tasks = [t for t in tasks if t.id not in already]
        if skipped:
            print(f"resuming, skipping {len(skipped)}: {', '.join(skipped)}", file=sys.stderr)
        spent_before = sum(r.get("spend_usd", 0.0) for r in done)
        policy = Policy(dry_run=policy.dry_run,
                        budget_usd=max(policy.budget_usd - spent_before, 0.0),
                        per_call_cap_usd=policy.per_call_cap_usd)

    accumulated: list[dict] = list(done)

    def progress(row):
        mark = "review" if row["needs_review"] else ("pass" if row["passed"] else "FAIL")
        print(f"  {row['task_id']:<5} {mark:<6} ${row['intended_usd']:.3f}  {row['first_tool'] or '-'}",
              file=sys.stderr)
        # Persist after every task. A run that is interrupted, times out or has
        # its machine put to sleep keeps everything it already earned.
        #
        # The scored row is recorded first and the transcript second. They were
        # the other way round, and a runner id containing a slash made the
        # transcript write fail, which threw away a task that had already run
        # and cost money.
        accumulated.append({k: v for k, v in row.items() if k != "transcript"})
        results_path.write_text(json.dumps(accumulated, indent=2, default=str), encoding="utf-8")
        report_path.write_text(render_report(accumulated), encoding="utf-8")
        try:
            name = f"transcript-{safe_name(row['task_id'])}-{safe_name(row['runner'])}.json"
            (out / name).write_text(row["transcript"].to_json(), encoding="utf-8")
        except OSError as exc:
            print(f"  (could not write the transcript for {row['task_id']}: {exc})", file=sys.stderr)

    if not tasks:
        print("nothing left to run", file=sys.stderr)
        print(render_report(accumulated))
        return 0

    print(f"running {len(tasks)} tasks against {runner.name}"
          f"{' (dry run)' if args.dry_run else ''}"
          f", resources={args.resources}", file=sys.stderr)
    try:
        run_suite(tasks, runner, backend, policy, on_result=progress,
                  resource_mode=args.resources)
    except KeyboardInterrupt:
        print("\ninterrupted; results so far are saved. Re-run with --resume.", file=sys.stderr)
    finally:
        session.close()

    report = render_report(accumulated)
    report_path.write_text(report, encoding="utf-8")

    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
