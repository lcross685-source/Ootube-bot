"""CMX3600 EDL writer.

A deliberate fallback. EDLs carry only cuts and timecode - no markers, no
multiple video tracks, no effects - but essentially every editing application
ever written can read one. If the XML import misbehaves on a particular
Premiere version, the EDL still reconstructs the cut order.
"""

from __future__ import annotations

from pathlib import Path

from .timeline import Timeline, to_timecode


def build_edl(tl: Timeline, title: str = "") -> str:
    lines = [f"TITLE: {title or tl.name}", "FCM: NON-DROP FRAME", ""]

    events: list[tuple[int, str, bool]] = []
    for track in tl.video_tracks:
        events.extend((c.start, c.name, False) for c in track)
    for track in tl.audio_tracks:
        events.extend((c.start, c.name, True) for c in track)

    clips = [c for track in tl.video_tracks + tl.audio_tracks for c in track]
    clips.sort(key=lambda c: (c.start, c.is_audio))

    for number, clip in enumerate(clips, start=1):
        channel = "A" if clip.is_audio else "V"
        src_in = to_timecode(clip.source_in, tl.fps)
        src_out = to_timecode(clip.source_out, tl.fps)
        rec_in = to_timecode(clip.start, tl.fps)
        rec_out = to_timecode(clip.end, tl.fps)
        lines.append(
            f"{number:03d}  AX       {channel}     C        "
            f"{src_in} {src_out} {rec_in} {rec_out}"
        )
        lines.append(f"* FROM CLIP NAME: {clip.media.name}")
        lines.append("")

    return "\n".join(lines)


def write_edl(tl: Timeline, out_path: str | Path, title: str = "") -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_edl(tl, title), encoding="utf-8")
    return out
