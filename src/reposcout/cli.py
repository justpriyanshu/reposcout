"""Command-line interface: ``reposcout index | ask | search | review | review-pr | serve | eval | status``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import Settings, load_env_file
from .errors import RepoScoutError
from .review import Severity
from .review.scanners import working_tree_loader
from .services import Services, build_services

SEVERITIES = [s.value for s in Severity]


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings()
    if args.repo:
        settings = settings.with_repo(args.repo)
    return settings.with_overrides(
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        embed_provider=args.embed_provider,
        embed_model=args.embed_model,
    )


def _services(args: argparse.Namespace, *, use_llm: bool = True, **kwargs) -> Services:
    return build_services(_settings(args), use_llm=use_llm, **kwargs)


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=120)
    except OSError as exc:
        raise RepoScoutError(f"Could not run git: {exc}") from exc
    if proc.returncode != 0:
        raise RepoScoutError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


# --------------------------------------------------------------------------- commands


def cmd_index(args) -> int:
    services = _services(args, use_llm=False)
    progress = (lambda msg: print(f"  {msg}", file=sys.stderr)) if args.verbose else None
    print(f"Indexing {services.settings.repo_path} with {services.embedder.name} ...", file=sys.stderr)
    stats = services.indexer().index(services.settings.repo_path, rebuild=args.rebuild, progress=progress)
    print(stats.summary())
    for error in stats.errors[:10]:
        print(f"  warning: {error}", file=sys.stderr)
    return 0


def _warn_if_empty(services: Services) -> None:
    if services.store.stats()["chunks"] == 0:
        print("note: the index is empty; run `reposcout index` first.", file=sys.stderr)


def cmd_ask(args) -> int:
    services = _services(args, use_llm=not args.no_llm)
    _warn_if_empty(services)
    answer = services.assistant.ask(args.question, args.k)
    if args.json:
        _print_json(answer.to_dict())
        return 0
    print(answer.answer)
    if answer.sources and answer.used_llm:
        print("\nSources:")
        for s in answer.sources:
            mark = "*" if s.ref in answer.cited else " "
            name = f"  {s.name}" if s.name else ""
            print(f" {mark}[{s.ref}] {s.path}:{s.start_line}-{s.end_line}{name}")
    print(f"\n({answer.latency_ms} ms)", file=sys.stderr)
    return 0


def cmd_search(args) -> int:
    services = _services(args, use_llm=False)
    _warn_if_empty(services)
    results = services.assistant.search(args.query, args.k)
    if args.json:
        _print_json([r.__dict__ for r in results])
        return 0
    for r in results:
        name = f"  {r.kind} {r.name}" if r.name else ""
        print(f"{r.location}{name}  (score {r.score:.4f})")
        preview = r.text.splitlines()[:6]
        for line in preview:
            print(f"    {line}")
        print()
    return 0


def _read_diff(args, repo: Path) -> str:
    if args.diff:
        if args.diff == "-":
            return sys.stdin.read()
        return Path(args.diff).read_text(encoding="utf-8", errors="replace")
    if args.staged:
        return _git(repo, "diff", "--cached", "-U5")
    if args.git:
        return _git(repo, "diff", f"{args.git}...HEAD", "-U5")
    return _git(repo, "diff", "HEAD", "-U5")


def _output_review(result, fmt: str) -> None:
    if fmt == "json":
        _print_json(result.to_dict())
    elif fmt == "markdown":
        print(result.to_markdown())
    else:
        print(result.to_text())


def _exit_code(result, fail_on: str | None) -> int:
    if not fail_on:
        return 0
    worst = result.worst()
    return 1 if worst is not None and worst.rank >= Severity(fail_on).rank else 0


def cmd_review(args) -> int:
    services = _services(
        args,
        use_llm=not args.no_llm,
        min_severity=Severity(args.min_severity),
        use_context=not args.no_context,
    )
    diff = _read_diff(args, services.settings.repo_path)
    if not diff.strip():
        print("Nothing to review: the diff is empty.")
        return 0
    # A diff from git describes the working tree, so whole-file scanners can read it there.
    loader = None if args.diff else working_tree_loader(services.settings.repo_path)
    result = services.reviewer.review(diff, load_source=loader)
    _output_review(result, args.format)
    return _exit_code(result, args.fail_on)


def cmd_review_pr(args) -> int:
    from .github import GitHubClient, parse_pr_ref

    services = _services(
        args,
        use_llm=not args.no_llm,
        min_severity=Severity(args.min_severity),
        use_context=not args.no_context,
    )
    settings = services.settings
    ref = parse_pr_ref(args.pr)
    client = GitHubClient(settings.github_token, settings.github_api_url)
    pr = client.get_pull_request(ref)
    diff = client.get_pull_request_diff(ref)
    result = services.reviewer.review(
        diff, pr.get("title", ""), pr.get("body") or "", load_source=client.source_loader(pr)
    )
    _output_review(result, args.format)
    if args.post:
        if not args.yes and not sys.stdin.isatty():
            print(
                "\nNot posted: posting needs confirmation. Run it in a terminal, or add --yes "
                "(for example in CI on your own repository).",
                file=sys.stderr,
            )
            return 2  # fail, so a CI run without --yes shows that nothing was posted
        if args.yes or _confirm_post(client, ref, result):
            review = client.post_review(ref, pr["head"]["sha"], result)
            print(f"\nPosted review: {review.get('html_url', ref)}", file=sys.stderr)
        else:
            print("Not posted.", file=sys.stderr)
    return _exit_code(result, args.fail_on)


def _confirm_post(client, ref, result) -> bool:
    """Ask before writing to GitHub: the review appears on someone's pull request under
    your account."""
    from .github import MAX_INLINE_COMMENTS

    who = client.whoami()
    inline = min(len(result.findings), MAX_INLINE_COMMENTS)
    question = (
        f"\nPost this review on {ref} as {'@' + who if who else 'the GITHUB_TOKEN account'} "
        f"(1 review, {inline} inline comment{'s' if inline != 1 else ''})? [y/N] "
    )
    try:
        answer = input(question)
    except EOFError:
        answer = ""
    return answer.strip().lower() in ("y", "yes")


def cmd_serve(args) -> int:
    import uvicorn

    from .observability import init_sentry
    from .server import create_app

    init_sentry("local")
    settings = _settings(args)
    app = create_app(settings)
    print(f"RepoScout serving {settings.repo_path} on http://{args.host}:{args.port}", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info" if args.verbose else "warning")
    return 0


def cmd_serve_public(args) -> int:
    import uvicorn

    from .observability import init_sentry

    init_sentry("public")
    from .embeddings import make_embedder
    from .web.app import build_public_app
    from .web.config import PublicSettings

    public = PublicSettings()
    app = build_public_app(public, make_embedder(_settings(args)))
    print(
        f"RepoScout public app on http://{args.host}:{args.port} (data in {public.data_dir})",
        file=sys.stderr,
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info" if args.verbose else "warning",
        proxy_headers=public.trust_proxy,
    )
    return 0


def cmd_grammars(args) -> int:
    from . import treesitter

    ready = treesitter.prefetch()
    missing = sorted((set(treesitter.GRAMMARS.values()) | {"tsx"}) - set(ready))
    print(f"tree-sitter grammars ready: {', '.join(ready) or 'none'}")
    if missing:
        print(f"unavailable (regex chunking will be used): {', '.join(missing)}", file=sys.stderr)
    return 0 if ready else 1


def cmd_eval(args) -> int:
    from .evaluation import eval_retrieval, eval_review, load_cases

    cases = load_cases(args.dataset)
    if args.kind == "retrieval":
        services = _services(args, use_llm=False)
        _warn_if_empty(services)
        report = eval_retrieval(services.assistant, cases, k=args.k)
    else:
        services = _services(args, use_llm=not args.no_llm, use_context=not args.no_context)
        report = eval_review(services.reviewer, cases)
    if args.json:
        _print_json(report)
        return 0
    for key, value in report.items():
        if key not in ("misses", "details"):
            print(f"{key:>30}: {value}")
    if report.get("misses"):
        print("\nMisses:")
        for miss in report["misses"]:
            print(f"  - {miss['question']}\n      expected {miss['expected']}\n      got      {miss['got']}")
    if report.get("details") and args.verbose:
        print("\nPer case:")
        for d in report["details"]:
            print(
                f"  {d['name']}: found {d['found']}/{d['expected']}, false positives {d['false_positives']}"
            )
            for f in d["findings"]:
                print(f"      {f}")
    return 0


def cmd_status(args) -> int:
    services = _services(args, use_llm=False)
    stats = services.store.stats()
    stats["repo"] = str(services.settings.repo_path)
    stats["index_path"] = str(services.settings.index_path)
    _print_json(stats)
    return 0


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reposcout",
        description="Ask questions about a codebase and review pull requests with AI.",
    )
    parser.add_argument("--version", action="version", version=f"reposcout {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", help="repository path (default: REPOSCOUT_REPO_PATH or current dir)")
    common.add_argument("--llm-provider", choices=["ollama", "openai"])
    common.add_argument("--llm-model")
    common.add_argument("--embed-provider", choices=["ollama", "openai", "hash"])
    common.add_argument("--embed-model")
    common.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", parents=[common], help="index (or re-index) the repository")
    p.add_argument("--rebuild", action="store_true", help="drop the index and start over")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("ask", parents=[common], help="ask a question about the code")
    p.add_argument("question")
    p.add_argument("-k", type=int, default=None, help="number of snippets to retrieve")
    p.add_argument("--no-llm", action="store_true", help="only list the most relevant code")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("search", parents=[common], help="hybrid keyword + semantic code search")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    review_opts = argparse.ArgumentParser(add_help=False)
    review_opts.add_argument("--no-llm", action="store_true", help="rules only (fast, offline)")
    review_opts.add_argument(
        "--no-context", action="store_true", help="don't add related repo code to prompts"
    )
    review_opts.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    review_opts.add_argument("--min-severity", choices=SEVERITIES, default="nit")
    review_opts.add_argument(
        "--fail-on",
        choices=SEVERITIES,
        help="exit with code 1 if any finding is at least this severe (for CI)",
    )

    p = sub.add_parser("review", parents=[common, review_opts], help="review local changes or a diff file")
    source = p.add_mutually_exclusive_group()
    source.add_argument("--diff", help="diff file to review ('-' reads stdin)")
    source.add_argument("--git", metavar="BASE", help="review BASE...HEAD, e.g. --git main")
    source.add_argument("--staged", action="store_true", help="review staged changes")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("review-pr", parents=[common, review_opts], help="review a GitHub pull request")
    p.add_argument("pr", help="owner/repo#123 or a pull request URL")
    p.add_argument(
        "--post", action="store_true", help="post the review as PR comments (asks for confirmation first)"
    )
    p.add_argument("-y", "--yes", action="store_true", help="with --post: post without asking (CI)")
    p.set_defaults(func=cmd_review_pr)

    p = sub.add_parser("serve", parents=[common], help="run the web UI, API and GitHub webhook")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser(
        "serve-public",
        parents=[common],
        help="run the public multi-repo web app (visitors bring their own key)",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve_public)

    p = sub.add_parser(
        "grammars", parents=[common], help="download tree-sitter grammars ahead of time (Docker, offline use)"
    )
    p.set_defaults(func=cmd_grammars)

    p = sub.add_parser("eval", parents=[common], help="measure retrieval or review quality")
    p.add_argument("kind", choices=["retrieval", "review"])
    p.add_argument("dataset", help="JSON dataset (see evals/)")
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--no-context", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("status", parents=[common], help="show index statistics")
    p.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env_file(os.environ.get("REPOSCOUT_ENV_FILE", ".env"))
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        return args.func(args)
    except RepoScoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
