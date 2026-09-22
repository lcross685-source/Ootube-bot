"""Assemble an editor-ready package.

The output of a run is a folder you open in Premiere, not a finished video:

    out/<topic-key>/
      project.xml        <- import this (File > Import)
      project.edl        <- fallback if the XML misbehaves
      captions.srt       <- drag onto the timeline as captions
      EDIT_NOTES.md      <- shot list, sources, what to fix first
      metadata.json      <- title/tags/description used at publish time
      thumbnail.jpg      <- starting point, not final
      audio/vo_*.wav     <- narration, one file per section
      broll/*.mp4        <- downloaded footage
      preview.mp4        <- optional rough render, to watch before editing

Assets are kept as separate files rather than baked together precisely so the
edit stays editable.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..config import Config
from ..media.tts import (
    SpokenClip, probe_duration, probe_has_audio, synthesize_sections,
)
from ..models import EditPackage, Script, Topic, utcnow
from .edl import write_edl
from .fcp7 import write_fcp7_xml
from .notes import write_edit_notes
from .srt import write_srt
from .timeline import build_timeline, to_frames

log = logging.getLogger(__name__)


def _blocks(script: Script) -> list[tuple[str, str]]:
    """Narration blocks: the hook, then one per section."""
    blocks = [("hook", script.hook)] if script.hook.strip() else []
    blocks.extend((s.heading or f"Section {i + 1}", s.voiceover)
                  for i, s in enumerate(script.sections))
    return blocks


def _claim_markers(
    script: Script, spoken: list[SpokenClip], fps: int
) -> list[tuple[int, str]]:
    """Place a CITE marker where each claim's wording appears in narration.

    Falls back to distributing unmatched claims across the timeline: a marker
    in roughly the right place still beats no marker, and the edit notes carry
    the full list either way.
    """
    markers: list[tuple[int, str]] = []
    unmatched: list[str] = []

    for claim in script.claims:
        label = f"{claim.text[:90]} — {claim.source_url or 'NO SOURCE'}"
        key_words = {w.lower().strip(".,;:") for w in claim.text.split() if len(w) > 5}
        best, best_score = None, 0
        for clip in spoken:
            words = {w.lower().strip(".,;:") for w in clip.text.split()}
            score = len(key_words & words)
            if score > best_score:
                best, best_score = clip, score
        if best is not None and best_score >= 2:
            markers.append((to_frames(best.start, fps), label))
        else:
            unmatched.append(label)

    if unmatched and spoken:
        span = max(1, len(spoken))
        for i, label in enumerate(unmatched):
            clip = spoken[min(span - 1, i % span)]
            markers.append((to_frames(clip.start, fps), label))
    return markers


def build_package(
    script: Script,
    topic: Topic,
    config: Config,
    out_dir: str | Path,
    *,
    broll: list[Path | None] | None = None,
    thumbnail: Path | None = None,
    verification_warnings: list[str] | None = None,
    now: datetime | None = None,
) -> EditPackage:
    """Produce the edit package. Returns paths and timing metadata."""
    now = now or utcnow()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    media = config.media

    # 1. Narration, one file per section - the spine of the timeline.
    spoken = synthesize_sections(
        _blocks(script), out / "audio", media, gap_s=media.section_gap_s
    )
    if not spoken:
        raise ValueError("script produced no narration blocks")

    # 2. Measure the footage so clips can be cut, not stretched.
    broll = list(broll or [])
    durations: dict[str, float] = {}
    has_audio: dict[str, bool] = {}
    missing: list[str] = []
    for i, clip_path in enumerate(broll):
        if clip_path and Path(clip_path).exists():
            durations[str(clip_path)] = probe_duration(clip_path, media) or 0.0
            has_audio[str(clip_path)] = probe_has_audio(clip_path, media)
        else:
            queries = [s.b_roll_query for s in script.sections]
            missing.append(queries[i] if i < len(queries) else f"section {i + 1}")

    # The hook has no b-roll query of its own, so V1 starts one slot later.
    aligned_broll: list[Path | None] = [None] + broll if script.hook.strip() else broll

    # 3. Lay out the timeline.
    timeline = build_timeline(
        f"{script.title[:60]} — rough cut",
        spoken,
        aligned_broll,
        fps=media.fps,
        width=media.width,
        height=media.height,
        words_per_minute=media.words_per_minute,
        broll_durations=durations,
        broll_has_audio=has_audio,
        claim_markers=_claim_markers(script, spoken, media.fps),
        sentence_markers=media.sentence_markers,
    )

    # 4. Write the project files.
    project_xml = write_fcp7_xml(timeline, out / "project.xml")
    project_edl = write_edl(timeline, out / "project.edl", title=script.title)
    captions = write_srt(spoken, out / "captions.srt", media.words_per_minute)

    # 5. Metadata for the publish step, editable by hand before upload.
    metadata = {
        "topic_key": topic.key,
        "topic_term": topic.term,
        "niche": topic.niche,
        "title": script.title,
        "tags": script.tags,
        "summary": script.description,
        "original_analysis": script.original_analysis,
        "thumbnail_text": script.thumbnail_text,
        "claims": [
            {
                "text": c.text,
                "source_url": c.source_url,
                "as_of": c.as_of.isoformat() if c.as_of else None,
            }
            for c in script.claims
        ],
        "sections": [
            {"heading": s.heading, "voiceover": s.voiceover} for s in script.sections
        ],
        "hook": script.hook,
        "drafted_at": now.isoformat(),
        "model": script.model,
        "rough_cut_seconds": round(timeline.duration / media.fps, 2),
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    notes = write_edit_notes(
        script, topic, timeline, spoken, out / "EDIT_NOTES.md",
        missing_broll=missing,
        verification_warnings=verification_warnings,
        now=now,
    )

    package = EditPackage(
        topic_key=topic.key,
        directory=str(out),
        project_xml=str(project_xml),
        project_edl=str(project_edl),
        captions=str(captions),
        notes=str(notes),
        metadata_path=str(out / "metadata.json"),
        thumbnail_path=str(thumbnail) if thumbnail else "",
        audio_paths=[str(c.path) for c in spoken],
        broll_paths=[str(p) for p in broll if p],
        duration_s=timeline.duration / media.fps if media.fps else 0.0,
        section_count=len(spoken),
        missing_broll=missing,
    )
    log.info(
        "edit package ready: %s (%.1f min, %d sections, %d markers, %d missing b-roll)",
        out, package.duration_s / 60, package.section_count,
        len(timeline.markers), len(missing),
    )
    return package
