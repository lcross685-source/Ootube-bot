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
from datetime import datetime, timedelta, timezone

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

    for item in report.drafted:
        print(f"\n  {item['title']}")
        print(f"    {item['minutes']} min rough cut, {item['sections']} sections"
              f"  (est. ${item['expected_revenue_usd']} if published)")
        print(f"    open:  {item['project']}")
        print(f"    notes: {item['directory']}/EDIT_NOTES.md")
        if item["missing_broll"] != "0":
            print(f"    !! {item['missing_broll']} section(s) need footage")

    if report.drafted:
        print("\n  Next: import project.xml into Premiere (File > Import), cut it down,")
        print("  export, then:  ootube publish <topic-key> --video <your-export.mp4>")

    for failure in report.failures:
        print(f"  FAILED {failure.get('topic', failure.get('stage'))}: {failure['error']}")
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    store.close()
    return 1 if report.failures and not report.drafted else 0


def cmd_drafts(args) -> int:
    """List edit packages waiting to be cut."""
    config = load_config(args.config_dir)
    store = Store(config.db_path)
    drafts = store.pending_drafts()
    if not drafts:
        print("\nNo drafts waiting. Run `ootube run` to make some.")
        store.close()
        return 0

    print(f"\n{len(drafts)} draft(s) waiting to be edited:\n")
    for row in drafts:
        age = ""
        drafted = row["drafted_at"]
        try:
            delta = utcnow() - datetime.fromisoformat(drafted)
            hours = delta.total_seconds() / 3600
            age = f"{hours:.0f}h old" if hours < 48 else f"{hours / 24:.0f}d old"
            # Topic freshness decays fast; an old draft may no longer be current.
            if hours > 72:
                age += "  <- may be stale, check before publishing"
        except (TypeError, ValueError):
            pass
        print(f"  {row['topic_key']}")
        print(f"    {row['title'][:70]}")
        print(f"    {row['duration_s'] / 60:.1f} min · {row['niche']} · {age}")
        print(f"    {row['directory']}/project.xml")
        if row["missing_broll"]:
            print(f"    !! {row['missing_broll']} section(s) need footage")
        print()
    print("Publish one with:  ootube publish <topic-key> --video <export.mp4>")
    store.close()
    return 0


def cmd_publish(args) -> int:
    """Upload a finished, human-edited export."""
    pipeline, store = _pipeline(args)
    publish_at = None
    if args.at:
        try:
            publish_at = datetime.fromisoformat(args.at)
            if publish_at.tzinfo is None:
                publish_at = publish_at.replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Could not parse --at {args.at!r}; expected ISO-8601.")
            store.close()
            return 2
    try:
        result = pipeline.publish_edited(
            args.topic_key, args.video, publish_at=publish_at
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        print(f"\nPublish failed: {exc}")
        store.close()
        return 1
    print(f"\nUploaded: {result.url}")
    print(f"Goes public: {result.scheduled_for.isoformat()}")
    if result.dry_run:
        print("(dry run - nothing was actually uploaded)")
    store.close()
    return 0


def cmd_discard(args) -> int:
    config = load_config(args.config_dir)
    store = Store(config.db_path)
    store.discard_draft(args.topic_key)
    print(f"Discarded draft {args.topic_key}")
    store.close()
    return 0


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

    drafts = store.pending_drafts()
    if drafts:
        print(f"\nWaiting to be edited ({len(drafts)}):")
        for row in drafts:
            print(f"  {row['topic_key'][:34]:34} {row['duration_s'] / 60:4.1f}min  {row['title'][:40]}")

    print("\nRecent runs:")
    for row in store.recent_runs(8):
        print(f"  {row['finished_at'][:19]}  {row['stage']:8} {row['status']:14} {row['detail'][:60]}")
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

    p = sub.add_parser("run", help="draft edit packages for the best current topics")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("drafts", help="list edit packages waiting to be cut")
    p.set_defaults(func=cmd_drafts)

    p = sub.add_parser("publish", help="upload a finished, human-edited export")
    p.add_argument("topic_key", help="from `ootube drafts`")
    p.add_argument("--video", required=True, help="your exported .mp4")
    p.add_argument("--at", help="ISO-8601 publish time (default: next free slot)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("discard", help="drop a draft you are not going to use")
    p.add_argument("topic_key")
    p.set_defaults(func=cmd_discard)

    p = sub.add_parser("status", help="show drafts, queue, quota and recent runs")
    p.set_defaults(func=cmd_status)

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
