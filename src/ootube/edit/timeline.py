"""The edit timeline.

This is the real deliverable. The pipeline's job is to assemble the first 80%
of an edit - narration laid out, b-roll roughly matched, cut points and
citations marked - and hand it to a human who tightens it into something worth
watching.

Everything is measured in frames, because that is what NLE interchange formats
speak and because rounding seconds to frames in one place avoids drift
accumulating across a few hundred clips.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from ..media.tts import SpokenClip, sentence_timings


def to_frames(seconds: float, fps: int) -> int:
    return max(0, int(round(seconds * fps)))


def to_timecode(frames: int, fps: int) -> str:
    """Non-drop-frame timecode, HH:MM:SS:FF."""
    frames = max(0, int(frames))
    f = frames % fps
    total_seconds = frames // fps
    s = total_seconds % 60
    m = (total_seconds // 60) % 60
    h = total_seconds // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


@dataclass
class MediaFile:
    """A source file referenced by the timeline."""

    id: str
    name: str
    path: Path
    duration_frames: int
    has_video: bool = True
    has_audio: bool = False
    width: int = 1920
    height: int = 1080

    @property
    def pathurl(self) -> str:
        """Absolute ``file://`` URL, as NLE interchange formats require.

        Three details that break imports if skipped: the path must be
        absolute, a Windows path (``C:\\clips``) needs a leading slash after
        the host or it concatenates into ``localhostC:``, and characters like
        spaces and ampersands must be percent-encoded or the importer
        truncates the path at the first one.
        """
        resolved = str(Path(self.path).resolve()).replace("\\", "/")
        if not resolved.startswith("/"):
            resolved = "/" + resolved          # Windows drive letters
        return "file://localhost" + quote(resolved)


@dataclass
class Clip:
    """One clip instance on a track."""

    name: str
    media: MediaFile
    start: int          # timeline in-point, frames
    end: int            # timeline out-point, frames
    source_in: int = 0
    source_out: int = 0
    is_audio: bool = False

    def __post_init__(self) -> None:
        if self.source_out <= self.source_in:
            self.source_out = self.source_in + (self.end - self.start)

    @property
    def duration(self) -> int:
        return max(0, self.end - self.start)


@dataclass
class Marker:
    """A sequence marker.

    Markers carry the editorial instructions that would otherwise live in a
    separate document nobody reads while cutting: where each section begins,
    which claim needs an on-screen citation, and where b-roll is missing.
    """

    frame: int
    name: str
    comment: str = ""


@dataclass
class Timeline:
    name: str
    fps: int = 30
    width: int = 1920
    height: int = 1080
    video_tracks: list[list[Clip]] = field(default_factory=list)
    audio_tracks: list[list[Clip]] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)

    @property
    def duration(self) -> int:
        ends = [c.end for track in self.video_tracks + self.audio_tracks for c in track]
        return max(ends) if ends else 0

    @property
    def media_files(self) -> list[MediaFile]:
        """Unique sources in first-reference order.

        Interchange formats define a file fully at its first appearance and
        reference it by id afterwards, so this order matters.
        """
        seen: dict[str, MediaFile] = {}
        for track in self.video_tracks + self.audio_tracks:
            for clip in track:
                seen.setdefault(clip.media.id, clip.media)
        return list(seen.values())


def build_timeline(
    name: str,
    spoken: list[SpokenClip],
    broll: list[Path | None],
    *,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    words_per_minute: int = 155,
    broll_durations: dict[str, float] | None = None,
    broll_has_audio: dict[str, bool] | None = None,
    claim_markers: list[tuple[int, str]] | None = None,
    sentence_markers: bool = True,
) -> Timeline:
    """Assemble narration, b-roll and markers into an editable timeline.

    Layout:

    * **V1** - b-roll, cut to section boundaries. A section with no usable
      clip is left as a visible gap rather than padded, because an empty span
      in the sequence is the clearest possible instruction to the editor.
    * **A1** - narration, one clip per section so blocks can be moved, trimmed
      or dropped independently.
    * **A2** - left empty for music. Reserved rather than populated: choosing
      the bed is a taste decision.
    """
    timeline = Timeline(name=name, fps=fps, width=width, height=height)
    video: list[Clip] = []
    audio: list[Clip] = []
    markers: list[Marker] = []
    broll_durations = broll_durations or {}
    broll_has_audio = broll_has_audio or {}

    for i, clip in enumerate(spoken):
        start_f = to_frames(clip.start, fps)
        end_f = to_frames(clip.end, fps)

        # --- narration on A1 ---
        vo_file = MediaFile(
            id=f"vo-{i}",
            name=clip.path.name,
            path=clip.path,
            duration_frames=max(1, end_f - start_f),
            has_video=False,
            has_audio=True,
        )
        audio.append(
            Clip(name=f"VO {i:02d} {clip.heading}", media=vo_file,
                 start=start_f, end=end_f, is_audio=True)
        )

        markers.append(
            Marker(
                frame=start_f,
                name=clip.heading.title(),
                comment=(clip.text[:160] + "...") if len(clip.text) > 160 else clip.text,
            )
        )

        # --- b-roll on V1 ---
        source = broll[i] if i < len(broll) else None
        if source and Path(source).exists():
            src_seconds = broll_durations.get(str(source), 0.0)
            src_frames = to_frames(src_seconds, fps) if src_seconds > 0 else 0
            bfile = MediaFile(
                id=f"broll-{i}",
                name=Path(source).name,
                path=Path(source),
                duration_frames=max(1, src_frames or (end_f - start_f)),
                has_video=True,
                # Described truthfully; only the video is placed on V1.
                has_audio=broll_has_audio.get(str(source), False),
                width=width,
                height=height,
            )
            needed = end_f - start_f
            if src_frames <= 0 or src_frames >= needed:
                video.append(
                    Clip(name=bfile.name, media=bfile, start=start_f, end=end_f,
                         source_in=0, source_out=needed)
                )
            else:
                # Stock clips are short. Repeat rather than stretch: a repeated
                # clip is obvious to the editor and trivially swapped, where a
                # slowed one silently looks wrong.
                cursor, repeat = start_f, 0
                while cursor < end_f:
                    chunk = min(src_frames, end_f - cursor)
                    video.append(
                        Clip(name=f"{bfile.name} ({repeat + 1})", media=bfile,
                             start=cursor, end=cursor + chunk,
                             source_in=0, source_out=chunk)
                    )
                    cursor += chunk
                    repeat += 1
                if repeat > 1:
                    markers.append(
                        Marker(frame=start_f, name="B-ROLL REPEATS",
                               comment=f"{bfile.name} loops {repeat}x here - "
                                       "consider swapping in more coverage")
                    )
        else:
            markers.append(
                Marker(frame=start_f, name="NO B-ROLL",
                       comment="V1 is empty for this section - drop footage here")
            )

        # --- sentence cut points ---
        if sentence_markers:
            for s_start, _, sentence in sentence_timings(clip, words_per_minute):
                f = to_frames(s_start, fps)
                if f == start_f:
                    continue  # already covered by the section marker
                markers.append(
                    Marker(frame=f, name="cut point",
                           comment=sentence[:120] + ("..." if len(sentence) > 120 else ""))
                )

    for frame, text in (claim_markers or []):
        markers.append(Marker(frame=frame, name="CITE", comment=text))

    timeline.video_tracks = [video] if video else [[]]
    timeline.audio_tracks = [audio, []]  # A2 reserved for music
    timeline.markers = sorted(markers, key=lambda m: m.frame)
    return timeline
