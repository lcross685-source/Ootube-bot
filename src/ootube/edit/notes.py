"""The editor's brief.

Everything the person cutting the video needs that does not fit in the
timeline: what the claimed facts are and where they came from, what the
pipeline already knows is weak about this rough cut, and the metadata that
will be used at upload.

Written as Markdown so it reads fine in any editor and in the GitHub UI.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..models import Script, Topic
from ..media.tts import SpokenClip
from .timeline import Timeline, to_timecode


def build_edit_notes(
    script: Script,
    topic: Topic,
    timeline: Timeline,
    spoken: list[SpokenClip],
    *,
    missing_broll: list[str] | None = None,
    verification_warnings: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    fps = timeline.fps
    total = timeline.duration / fps if fps else 0.0
    lines: list[str] = []

    lines.append(f"# {script.title}")
    lines.append("")
    lines.append(
        f"Rough cut: **{total / 60:.1f} min** · {len(spoken)} sections · "
        f"{script.word_count()} words · {fps}fps {timeline.width}x{timeline.height}"
    )
    lines.append("")
    lines.append("> This is a first pass, not a finished video. Everything below is")
    lines.append("> a starting point to cut against.")
    lines.append("")

    # --- what to fix first ----------------------------------------------
    lines.append("## Do these first")
    lines.append("")
    todo: list[str] = []
    if missing_broll:
        todo.append(
            f"**{len(missing_broll)} section(s) have no b-roll** - V1 is empty there. "
            f"Marked `NO B-ROLL`. Queries that found nothing: "
            + ", ".join(f"`{q}`" for q in missing_broll[:6])
        )
    todo.append(
        "**Cut the narration down.** Written to hit a target length, which means "
        "it is long. Sentence-level `cut point` markers show where you can lift a "
        "line without leaving a gap mid-sentence."
    )
    todo.append(
        "**Add the citations.** Every `CITE` marker is a factual claim that should "
        "carry an on-screen source. Sources are listed below."
    )
    todo.append(
        "**Tighten the gaps.** There is a 0.35s pad between sections. Close it where "
        "the pacing should be tight, widen it where a beat helps."
    )
    todo.append("**A2 is empty** and reserved for a music bed.")
    for item in todo:
        lines.append(f"- {item}")
    lines.append("")

    if verification_warnings:
        lines.append("## Flags from verification")
        lines.append("")
        lines.append("Not blocking, but worth a look before publishing:")
        lines.append("")
        for warning in verification_warnings:
            lines.append(f"- {warning}")
        lines.append("")

    # --- the original angle ----------------------------------------------
    if script.original_analysis:
        lines.append("## The angle")
        lines.append("")
        lines.append(f"> {script.original_analysis}")
        lines.append("")
        lines.append(
            "This is the part that makes the video worth publishing rather than "
            "a summary. If the cut loses it, the video is not worth publishing."
        )
        lines.append("")

    # --- shot list --------------------------------------------------------
    lines.append("## Shot list")
    lines.append("")
    lines.append("| TC | Section | Length | B-roll | Narration |")
    lines.append("|---|---|---|---|---|")
    broll_by_start = {}
    for track in timeline.video_tracks:
        for clip in track:
            broll_by_start.setdefault(clip.start, clip.media.name)
    for clip in spoken:
        start_f = int(round(clip.start * fps))
        tc = to_timecode(start_f, fps)
        broll = broll_by_start.get(start_f, "*(none)*")
        preview = clip.text[:70].replace("|", "\\|")
        lines.append(
            f"| `{tc}` | {clip.heading} | {clip.duration:.0f}s | {broll} | {preview}... |"
        )
    lines.append("")

    # --- sources ----------------------------------------------------------
    if script.claims:
        lines.append("## Claims and sources")
        lines.append("")
        lines.append(
            "Each of these is asserted in the narration. Verify anything you are "
            "unsure of before publishing - your name is on the video."
        )
        lines.append("")
        for claim in script.claims:
            date = f" ({claim.as_of:%Y-%m-%d})" if claim.as_of else " *(undated)*"
            lines.append(f"- {claim.text}")
            lines.append(f"  - {claim.source_url or '*no source*'}{date}")
        lines.append("")

    # --- full script ------------------------------------------------------
    lines.append("## Script")
    lines.append("")
    lines.append(f"**Hook** — {script.hook}")
    lines.append("")
    for clip in spoken:
        lines.append(f"### {clip.heading} — `{to_timecode(int(clip.start * fps), fps)}`")
        lines.append("")
        lines.append(clip.text)
        lines.append("")

    # --- upload metadata --------------------------------------------------
    lines.append("## Upload metadata")
    lines.append("")
    lines.append(
        "Prepared in `metadata.json`. Edit that file to change any of it, then:"
    )
    lines.append("")
    lines.append("```bash")
    lines.append(f"ootube publish {topic.key} --video <your-export.mp4>")
    lines.append("```")
    lines.append("")
    lines.append(f"- **Title:** {script.title}")
    lines.append(f"- **Tags:** {', '.join(script.tags[:12])}")
    lines.append(f"- **Niche:** {topic.niche}")
    lines.append(
        "- **Chapters and description** are generated at publish time from the "
        "final runtime, so they match whatever you cut."
    )
    lines.append("")
    return "\n".join(lines)


def write_edit_notes(
    script: Script,
    topic: Topic,
    timeline: Timeline,
    spoken: list[SpokenClip],
    out_path: str | Path,
    **kwargs,
) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        build_edit_notes(script, topic, timeline, spoken, **kwargs), encoding="utf-8"
    )
    return out
