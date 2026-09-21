"""Command-line interface.

The commands mirror the pipeline stages so an operator can inspect each one
before trusting the whole chain: ``trends`` shows what was found, ``plan``
shows what would be made and why everything else was rejected, and ``run``
does it. ``doctor`` answers "is this thing configured correctly" without
burning quota.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import timedelta

from .config import load_config
from .models import utcnow
from .scheduler.pipeline import Pipeline
from .store import Store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # These libraries are chatty at INFO and drown out the pipeline's own log.
    for noisy in ("urllib3", "googleapiclient", "google_auth_httplib2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _pipeline(args) -> tuple[Pipeline, Store]:
    config = load_config(args.config_dir)
    store = Store(config.db_path)
    return Pipeline(config, store, dry_run=getattr(args, "dry_run", False)), store


# ---------------------------------------------------------------- commands
def cmd_trends(args) -> int:
    pipeline, store = _pipeline(args)
    signals = pipeline.discover(only=args.source or None)
    if args.json:
        print(json.dumps(
            [
                {
                    "source": s.source, "term": s.term, "volume": s.volume,
                    "event_at": s.event_at.isoformat() if s.event_at else None,
                    "url": s.url,
                }
                for s in signals
            ],
            indent=2,
        ))
        return 0

    by_source: dict[str, int] = {}
    for s in signals:
        by_source[s.source] = by_source.get(s.source, 0) + 1
    print(f"\n{len(signals)} signals from {len(by_source)} sources")
    for name, count in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {name:18} {count:4d}")

    print(f"\nTop {min(args.limit, len(signals))} by volume:")
    for s in sorted(signals, key=lambda x: -x.volume)[: args.limit]:
        when = s.event_at.strftime("%Y-%m-%d %H:%M") if s.event_at else "undated"
        print(f"  [{s.source:14}] {when}  vol={s.volume:>10,.0f}  {s.term[:70]}")

    if pipeline.gate.known_generations:
        print("\nProduct generations learned this run (used to block stale topics):")
        for family, number in sorted(pipeline.gate.known_generations.items()):
            print(f"  {family:24} -> {number:g}")
    store.close()
    return 0


def cmd_plan(args) -> int:
    pipeline, store = _pipeline(args)
    now = utcnow()
    signals = pipeline.discover()
    limit = args.limit or pipeline.planner.videos_needed(now)
    selected, rejected = pipeline.select(signals, limit, now=now)
    slots = pipeline.planner.plan(len(selected), now=now)

    print(f"\n{len(signals)} signals -> {len(selected) + len(rejected)} topics")
    print(f"Queue depth: {pipeline.planner.queue_depth(now)} scheduled videos")
    print(f"Quota today: {pipeline.quota.usage()} "
          f"(room for {pipeline.quota.max_publishes_today(playlist=False)} more uploads)")

    print(f"\nSELECTED ({len(selected)}), ranked by expected revenue:")
    if not selected:
        print("  (nothing passed the filters - see rejections below)")
    total = 0.0
    for st, slot in zip(selected, slots):
        total += st.expected_revenue_usd
        print(f"\n  {st.topic.term}")
        print(f"    niche={st.topic.niche}  rpm=${st.rpm:.0f}  score={st.score:.3f}")
        print(f"    freshness={st.freshness:.2f}  competition={st.saturation:.2f}  "
              f"sources={','.join(st.topic.sources)}")
        print(f"    projected {st.projected_views:,} views  "
              f"-> ${st.expected_revenue_usd:.2f} est. ad revenue")
        print(f"    would publish {slot.isoformat()}")
    if selected:
        print(f"\n  Run total: ${total:.2f} estimated")

    if rejected and not args.quiet_rejects:
        print(f"\nREJECTED ({len(rejected)}):")
        by_rule: dict[str, int] = {}
        for _, v in rejected:
            by_rule[v.rule] = by_rule.get(v.rule, 0) + 1
        for rule, count in sorted(by_rule.items(), key=lambda kv: -kv[1]):
            print(f"  {rule:16} {count:4d}")
        print("\n  Examples:")
        for topic, verdict in rejected[: args.show_rejects]:
            print(f"    [{verdict.rule:14}] {topic.term[:58]}")
            print(f"       {verdict.reason}")
    store.close()
    return 0


def cmd_run(args) -> int:
    pipeline, store = _pipeline(args)
    report = pipeline.run(limit=args.limit)
    print("\n" + report.summary())
    for note in report.notes:
        print(f"  note: {note}")
    for item in report.published:
        print(f"  published: {item['title']}")
        print(f"     {item['url']}  publishes {item['scheduled_for']}")
    for title in report.queued_for_approval:
        print(f"  awaiting approval: {title}")
    for failure in report.failures:
        print(f"  FAILED {failure.get('topic', failure.get('stage'))}: {failure['error']}")
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    store.close()
    # Non-zero only when the run produced nothing but was supposed to, so CI
    # surfaces a real outage without alerting on a legitimately quiet day.
    return 1 if report.failures and not report.published else 0


def cmd_status(args) -> int:
    config = load_config(args.config_dir)
    store = Store(config.db_path)
    now = utcnow()
    upcoming = store.scheduled_after(now)

    print(f"\nChannel: {config.channel.name}")
    print(f"Niches:  {', '.join(config.niche_names)}")
    print(f"Quota today: {store.quota_today()}")
    print(f"\nScheduled ({len(upcoming)}):")
    for row in store.published_since(now - timedelta(days=30)):
        if row["scheduled_for"] and row["scheduled_for"] > now.isoformat():
            flag = " [dry-run]" if row["dry_run"] else ""
            print(f"  {row['scheduled_for']}  {row['title'][:60]}{flag}")

    pending = store.pending_approvals()
    if pending:
        print(f"\nAwaiting approval ({len(pending)}):")
        for row in pending:
            print(f"  {row['fingerprint'][:10]}  {row['title'][:60]}")

    print("\nRecent runs:")
    for row in store.recent_runs(8):
        print(f"  {row['finished_at'][:19]}  {row['stage']:8} {row['status']:14} {row['detail'][:60]}")
    store.close()
    return 0


def cmd_approve(args) -> int:
    config = load_config(args.config_dir)
    store = Store(config.db_path)
    pending = store.pending_approvals()
    if not pending:
        print("Nothing awaiting approval.")
        store.close()
        return 0
    if args.list or not args.fingerprint:
        for row in pending:
            payload = json.loads(row["payload"] or "{}")
            print(f"\n{row['fingerprint']}")
            print(f"  title:   {row['title']}")
            print(f"  niche:   {payload.get('niche')}")
            print(f"  video:   {payload.get('video_path')}")
            print(f"  publish: {payload.get('publish_at')}")
        print("\nApprove with: ootube approve --fingerprint <id> [--reject]")
        store.close()
        return 0
    store.decide_approval(args.fingerprint, "rejected" if args.reject else "approved")
    print(f"{'Rejected' if args.reject else 'Approved'} {args.fingerprint}")
    store.close()
    return 0


def cmd_doctor(args) -> int:
    """Check configuration and credentials without spending quota."""
    config = load_config(args.config_dir)
    problems, warnings = [], []

    print(f"\nConfig dir: {args.config_dir or 'config/'}")
    print(f"Channel:    {config.channel.name}")
    print(f"Niches:     {len(config.channel.niches)}")
    if not config.channel.niches:
        problems.append("no niches configured - nothing will ever be selected")

    print("\nCredentials:")
    checks = [
        ("ANTHROPIC_API_KEY", True, "script generation"),
        ("YOUTUBE_CLIENT_ID", True, "upload"),
        ("YOUTUBE_CLIENT_SECRET", True, "upload"),
        ("YOUTUBE_REFRESH_TOKEN", True, "unattended upload"),
        ("YOUTUBE_API_KEY", False, "YouTube trending chart source"),
        ("PEXELS_API_KEY", False, "b-roll footage"),
        ("ELEVENLABS_API_KEY", False, "ElevenLabs voice"),
    ]
    for name, required, purpose in checks:
        present = bool(os.environ.get(name))
        mark = "ok " if present else ("MISSING" if required else "-  ")
        print(f"  [{mark:7}] {name:24} {purpose}")
        if required and not present:
            problems.append(f"{name} is not set ({purpose} will fail)")
        elif not required and not present:
            warnings.append(f"{name} not set - {purpose} disabled")

    print("\nBinaries:")
    for binary in (config.media.ffmpeg_bin, config.media.ffprobe_bin):
        found = shutil.which(binary)
        print(f"  [{'ok ' if found else 'MISSING':7}] {binary:24} {found or ''}")
        if not found:
            problems.append(f"{binary} not found on PATH - rendering will fail")

    if config.media.tts_provider == "edge" and not shutil.which("edge-tts"):
        problems.append("tts_provider is 'edge' but edge-tts is not installed")

    print("\nState:")
    store = Store(config.db_path)
    print(f"  database:   {config.db_path}")
    print(f"  scheduled:  {len(store.scheduled_after(utcnow()))}")
    print(f"  quota used: {store.quota_today()}")
    store.close()

    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print(f"  - {w}")
    if problems:
        print("\nProblems:")
        for p in problems:
            print(f"  - {p}")
        print(f"\n{len(problems)} problem(s) found.")
        return 1
    print("\nAll required checks passed.")
    return 0


def cmd_auth(args) -> int:
    """Run the one-time OAuth consent flow and print a reusable refresh token."""
    from .publish.youtube import YouTubeClient

    client = YouTubeClient(client_secrets=args.client_secrets, token_file=args.token_file)
    creds = client._credentials()
    print(f"\nToken written to {args.token_file}")
    if getattr(creds, "refresh_token", None):
        print("\nFor unattended CI, store these as repository secrets:")
        print(f"  YOUTUBE_CLIENT_ID={getattr(creds, 'client_id', '')}")
        print("  YOUTUBE_CLIENT_SECRET=<from your client_secret.json>")
        print(f"  YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
    return 0


# ------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ootube", description="Autonomous, trend-targeted YouTube channel operator"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--config-dir", default=None, help="directory holding channel.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("trends", help="fetch and show current trend signals")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--source", action="append", help="restrict to a source (repeatable)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_trends)

    p = sub.add_parser("plan", help="show what a run would produce, without producing it")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--show-rejects", type=int, default=8)
    p.add_argument("--quiet-rejects", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("run", help="discover, produce and schedule videos")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true", help="produce videos but do not upload")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="show queue, quota and recent runs")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("approve", help="review videos held for human approval")
    p.add_argument("--fingerprint")
    p.add_argument("--reject", action="store_true")
    p.add_argument("--list", action="store_true")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("doctor", help="check configuration and credentials")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("auth", help="one-time YouTube OAuth setup")
    p.add_argument("--client-secrets", default="client_secret.json")
    p.add_argument("--token-file", default="token.json")
    p.set_defaults(func=cmd_auth)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
