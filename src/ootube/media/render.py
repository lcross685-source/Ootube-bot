"""Video assembly with ffmpeg.

Structure: one visual segment per script section, timed to that section's
narration, concatenated and muxed with the voiceover. Sections whose b-roll
lookup failed get a generated gradient card with the section's on-screen text,
so a missing clip degrades the look rather than failing the run.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import MediaConfig
from ..models import Script, VideoAsset
from .tts import probe_duration, synthesize

log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    pass


def ffmpeg_available(cfg: MediaConfig) -> bool:
    return shutil.which(cfg.ffmpeg_bin) is not None


def _run(args: list[str], timeout: int = 1800) -> None:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        tail = (result.stderr or "")[-800:]
        raise RenderError(f"ffmpeg failed ({' '.join(args[:4])}...): {tail}")


@dataclass
class Segment:
    duration: float
    clip: Path | None
    caption: str


class Renderer:
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg

    # ------------------------------------------------------------------
    def _escape(self, text: str) -> str:
        """Escape text for ffmpeg's drawtext filter."""
        return (
            (text or "")
            .replace("\\", r"\\\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
            .replace("%", r"\%")
            .replace("[", r"\[")
            .replace("]", r"\]")
            .replace(",", r"\,")
        )

    def _card(self, segment: Segment, index: int, workdir: Path) -> Path:
        """Generate a gradient card for a section with no b-roll."""
        out = workdir / f"seg_{index:02d}.mp4"
        cfg = self.cfg
        # Hue drifts with the section index so consecutive cards differ.
        hue = (index * 37) % 360
        filters = [
            f"gradients=s={cfg.width}x{cfg.height}:d={segment.duration:.2f}"
            f":c0=0x0f172a:c1=0x1e3a8a:speed=0.02"
        ]
        vf = f"hue=h={hue}"
        if segment.caption:
            vf += (
                f",drawtext=text='{self._escape(segment.caption[:60])}'"
                f":fontcolor=white:fontsize={max(28, cfg.height // 22)}"
                f":x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.35:boxborderw=24"
            )
        _run([
            cfg.ffmpeg_bin, "-y", "-f", "lavfi",
            "-i", filters[0],
            "-vf", f"{vf},format=yuv420p",
            "-r", str(cfg.fps), "-t", f"{segment.duration:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            str(out),
        ])
        return out

    def _clip_segment(self, segment: Segment, index: int, workdir: Path) -> Path:
        """Trim/loop a b-roll clip to exactly the section's duration."""
        out = workdir / f"seg_{index:02d}.mp4"
        cfg = self.cfg
        vf = (
            f"scale={cfg.width}:{cfg.height}:force_original_aspect_ratio=increase,"
            f"crop={cfg.width}:{cfg.height},format=yuv420p"
        )
        if segment.caption:
            vf += (
                f",drawtext=text='{self._escape(segment.caption[:60])}'"
                f":fontcolor=white:fontsize={max(28, cfg.height // 24)}"
                f":x=(w-text_w)/2:y=h-text_h-80"
                f":box=1:boxcolor=black@0.45:boxborderw=20"
            )
        _run([
            cfg.ffmpeg_bin, "-y",
            # Loop the source so a 6-second stock clip can cover a 40-second
            # section instead of freezing on its last frame.
            "-stream_loop", "-1", "-i", str(segment.clip),
            "-t", f"{segment.duration:.2f}",
            "-vf", vf, "-r", str(cfg.fps),
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            str(out),
        ])
        return out

    # ------------------------------------------------------------------
    def plan_segments(self, script: Script, total_duration: float) -> list[Segment]:
        """Split the runtime across sections in proportion to their word counts."""
        section_words = [max(1, len(s.voiceover.split())) for s in script.sections]
        hook_words = max(1, len(script.hook.split()))
        weights = [hook_words] + section_words
        total_words = sum(weights)

        captions = [script.title] + [s.on_screen_text for s in script.sections]
        segments: list[Segment] = []
        for weight, caption in zip(weights, captions):
            segments.append(
                Segment(
                    duration=max(2.0, total_duration * weight / total_words),
                    clip=None,
                    caption=caption or "",
                )
            )
        return segments

    # ------------------------------------------------------------------
    def render(
        self,
        script: Script,
        workdir: str | Path,
        *,
        broll: list[Path | None] | None = None,
    ) -> VideoAsset:
        cfg = self.cfg
        work = Path(workdir)
        work.mkdir(parents=True, exist_ok=True)

        if not ffmpeg_available(cfg):
            raise RenderError(
                f"{cfg.ffmpeg_bin} not found on PATH. Install ffmpeg "
                "(apt-get install ffmpeg / brew install ffmpeg)."
            )

        # 1. Narration first - its real length drives every visual timing.
        audio_path = work / "voiceover.wav"
        duration = synthesize(script.voiceover_text, audio_path, cfg)
        duration = probe_duration(audio_path, cfg) or duration
        if duration < cfg.min_duration_s:
            log.warning(
                "narration is %.0fs, below the %.0fs minimum; short videos "
                "under-perform on watch time",
                duration, cfg.min_duration_s,
            )

        # 2. One visual segment per narration block.
        segments = self.plan_segments(script, duration)
        if broll:
            for seg, clip in zip(segments[1:], broll):
                seg.clip = clip

        segment_files: list[Path] = []
        for i, seg in enumerate(segments):
            if seg.clip and Path(seg.clip).exists():
                segment_files.append(self._clip_segment(seg, i, work))
            else:
                segment_files.append(self._card(seg, i, work))

        # 3. Concatenate, then mux the narration over the whole thing.
        concat_file = work / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{p.resolve()}'" for p in segment_files) + "\n"
        )
        silent_video = work / "silent.mp4"
        _run([
            cfg.ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file), "-c", "copy", str(silent_video),
        ])

        final = work / "video.mp4"
        _run([
            cfg.ffmpeg_bin, "-y",
            "-i", str(silent_video), "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            str(final),
        ])

        return VideoAsset(
            topic_key=script.topic_key,
            video_path=str(final),
            audio_path=str(audio_path),
            duration_s=probe_duration(final, cfg) or duration,
            width=cfg.width,
            height=cfg.height,
        )
