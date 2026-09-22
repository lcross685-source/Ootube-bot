"""Final Cut Pro 7 XML (xmeml v5) writer.

This is the format Premiere Pro actually imports. Adobe's importer reads
``.xml`` (the FCP7 schema) and does *not* read ``.fcpxml`` (modern Final Cut),
so despite the name this is the correct target for a Premiere workflow. Resolve
reads it too, which makes it the one file that opens in both.

The schema is fussy in specific ways, each handled below:

* every time is an integer frame count, never seconds;
* a ``<file>`` is described in full at its first appearance and referenced by
  id alone afterwards - repeating the body makes Premiere create duplicate
  bin items;
* audio clips need a ``<sourcetrack>`` or they import silent;
* gaps are implicit, expressed by leaving a span unoccupied.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from .timeline import Clip, MediaFile, Timeline

XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'


def _sub(parent: ET.Element, tag: str, text: str | int | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def _rate(parent: ET.Element, fps: int) -> None:
    rate = _sub(parent, "rate")
    _sub(rate, "timebase", fps)
    # NTSC true would imply a 29.97/59.94 pulldown; integer timebases are
    # cleaner for synthetic media and avoid drift against the narration.
    _sub(rate, "ntsc", "FALSE")


def _sample_characteristics(parent: ET.Element, tl: Timeline) -> None:
    sc = _sub(parent, "samplecharacteristics")
    _rate(sc, tl.fps)
    _sub(sc, "width", tl.width)
    _sub(sc, "height", tl.height)
    _sub(sc, "anamorphic", "FALSE")
    _sub(sc, "pixelaspectratio", "square")
    _sub(sc, "fielddominance", "none")


def _file_element(
    parent: ET.Element, media: MediaFile, tl: Timeline, emitted: set[str]
) -> ET.Element:
    """Emit a ``<file>``, in full the first time and by reference after."""
    el = _sub(parent, "file")
    el.set("id", media.id)
    if media.id in emitted:
        return el  # reference only - body would duplicate the bin item

    emitted.add(media.id)
    _sub(el, "name", media.name)
    _sub(el, "pathurl", media.pathurl)
    _rate(el, tl.fps)
    _sub(el, "duration", media.duration_frames)

    media_el = _sub(el, "media")
    if media.has_video:
        video = _sub(media_el, "video")
        _sub(video, "duration", media.duration_frames)
        _sample_characteristics(video, tl)
    if media.has_audio:
        audio = _sub(media_el, "audio")
        _sub(audio, "duration", media.duration_frames)
        asc = _sub(audio, "samplecharacteristics")
        _sub(asc, "depth", 16)
        _sub(asc, "samplerate", 48000)
        _sub(audio, "channelcount", 1)
    return el


def _clip_item(
    parent: ET.Element,
    clip: Clip,
    tl: Timeline,
    emitted: set[str],
    index: int,
) -> None:
    item = _sub(parent, "clipitem")
    item.set("id", f"clipitem-{index}")
    _sub(item, "name", clip.name)
    _sub(item, "enabled", "TRUE")
    _sub(item, "duration", clip.media.duration_frames)
    _rate(item, tl.fps)
    _sub(item, "start", clip.start)
    _sub(item, "end", clip.end)
    _sub(item, "in", clip.source_in)
    _sub(item, "out", clip.source_out)
    _file_element(item, clip.media, tl, emitted)

    if clip.is_audio:
        # Without this, Premiere imports the clip with no audio mapping.
        st = _sub(item, "sourcetrack")
        _sub(st, "mediatype", "audio")
        _sub(st, "trackindex", 1)
    else:
        st = _sub(item, "sourcetrack")
        _sub(st, "mediatype", "video")
        _sub(st, "trackindex", 1)


def build_xml(tl: Timeline) -> ET.Element:
    root = ET.Element("xmeml", {"version": "5"})
    sequence = _sub(root, "sequence")
    sequence.set("id", "sequence-1")
    _sub(sequence, "name", tl.name)
    _sub(sequence, "duration", tl.duration)
    _rate(sequence, tl.fps)

    tc = _sub(sequence, "timecode")
    _rate(tc, tl.fps)
    _sub(tc, "string", "00:00:00:00")
    _sub(tc, "frame", 0)
    _sub(tc, "displayformat", "NDF")

    media = _sub(sequence, "media")
    emitted: set[str] = set()
    counter = 0

    video = _sub(media, "video")
    fmt = _sub(video, "format")
    _sample_characteristics(fmt, tl)
    for track_clips in tl.video_tracks:
        track = _sub(video, "track")
        for clip in track_clips:
            _clip_item(track, clip, tl, emitted, counter)
            counter += 1
        _sub(track, "enabled", "TRUE")
        _sub(track, "locked", "FALSE")

    audio = _sub(media, "audio")
    for track_clips in tl.audio_tracks:
        track = _sub(audio, "track")
        for clip in track_clips:
            _clip_item(track, clip, tl, emitted, counter)
            counter += 1
        _sub(track, "enabled", "TRUE")
        _sub(track, "locked", "FALSE")

    # Markers live on the sequence, after the media block.
    for marker in tl.markers:
        m = _sub(sequence, "marker")
        _sub(m, "name", marker.name)
        _sub(m, "comment", marker.comment)
        _sub(m, "in", marker.frame)
        _sub(m, "out", -1)   # -1 means a point marker rather than a span

    return root


def write_fcp7_xml(tl: Timeline, out_path: str | Path) -> Path:
    """Write the sequence as an ``.xml`` Premiere and Resolve can import."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    root = build_xml(tl)
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    out.write_text(XML_DECL + body, encoding="utf-8")
    return out
