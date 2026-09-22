"""Text-to-speech.

Several providers are supported because voice is the most replaceable part of
the stack and the one most likely to change for cost or quality reasons. The
``silent`` provider generates correctly-timed silence so the rest of the
pipeline can be exercised end to end without an API key or network access.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from ..config import MediaConfig

log = logging.getLogger(__name__)


class TTSError(RuntimeError):
    pass


def _ensure_parent(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def estimate_duration(text: str, words_per_minute: int) -> float:
    words = len((text or "").split())
    if words_per_minute <= 0:
        return 0.0
    return words / words_per_minute * 60.0


# --------------------------------------------------------------------------
def _synthesize_silent(text: str, out_path: Path, cfg: MediaConfig) -> float:
    """Write silence matching the script's spoken length.

    Used for dry runs and CI: it exercises timing, muxing and duration logic
    without contacting a paid API.
    """
    duration = max(1.0, estimate_duration(text, cfg.words_per_minute))
    sample_rate = 24000
    frames = int(duration * sample_rate)
    with wave.open(str(out_path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack("<h", 0) * frames)
    return duration


def _synthesize_edge(text: str, out_path: Path, cfg: MediaConfig) -> float:
    """Microsoft Edge neural voices via the ``edge-tts`` CLI (free, no key)."""
    exe = shutil.which("edge-tts")
    if not exe:
        raise TTSError("edge-tts not found. Install with: pip install edge-tts")
    result = subprocess.run(
        [exe, "--voice", cfg.tts_voice, "--text", text, "--write-media", str(out_path)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise TTSError(f"edge-tts failed: {result.stderr[:400]}")
    return probe_duration(out_path, cfg) or estimate_duration(text, cfg.words_per_minute)


def _synthesize_elevenlabs(text: str, out_path: Path, cfg: MediaConfig) -> float:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise TTSError("ELEVENLABS_API_KEY is not set")
    import requests

    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", cfg.tts_voice)
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2_5"),
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=300,
    )
    if resp.status_code != 200:
        raise TTSError(f"ElevenLabs returned {resp.status_code}: {resp.text[:300]}")
    out_path.write_bytes(resp.content)
    return probe_duration(out_path, cfg) or estimate_duration(text, cfg.words_per_minute)


def _synthesize_piper(text: str, out_path: Path, cfg: MediaConfig) -> float:
    """Piper: fully local neural TTS, no per-character cost."""
    exe = shutil.which("piper")
    if not exe:
        raise TTSError("piper not found on PATH")
    model = os.environ.get("PIPER_MODEL")
    if not model:
        raise TTSError("PIPER_MODEL must point to a .onnx voice model")
    result = subprocess.run(
        [exe, "--model", model, "--output_file", str(out_path)],
        input=text,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise TTSError(f"piper failed: {result.stderr[:400]}")
    return probe_duration(out_path, cfg) or estimate_duration(text, cfg.words_per_minute)


PROVIDERS = {
    "silent": _synthesize_silent,
    "edge": _synthesize_edge,
    "elevenlabs": _synthesize_elevenlabs,
    "piper": _synthesize_piper,
}


def probe_duration(path: str | Path, cfg: MediaConfig) -> float | None:
    """Read a media file's real duration with ffprobe, if available."""
    exe = shutil.which(cfg.ffprobe_bin)
    if not exe:
        return None
    result = subprocess.run(
        [
            exe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def synthesize(text: str, out_path: str | Path, cfg: MediaConfig) -> float:
    """Render ``text`` to audio at ``out_path``. Returns duration in seconds."""
    if not (text or "").strip():
        raise TTSError("nothing to synthesize")
    provider = (cfg.tts_provider or "silent").lower()
    fn = PROVIDERS.get(provider)
    if fn is None:
        raise TTSError(
            f"unknown tts_provider '{provider}'. Options: {', '.join(sorted(PROVIDERS))}"
        )
    path = _ensure_parent(out_path)
    duration = fn(text, path, cfg)
    if not path.exists() or path.stat().st_size == 0:
        raise TTSError(f"{provider} produced no audio at {path}")
    log.info("tts(%s) -> %s (%.1fs)", provider, path, duration)
    return duration

def probe_has_audio(path: str | Path, cfg: MediaConfig) -> bool:
    """True if the file carries at least one audio stream.

    Stock footage often ships with ambience. The timeline leaves it off - it
    competes with narration - but the project file should still describe the
    source accurately so the editor can pull source audio up if they want it.
    """
    exe = shutil.which(cfg.ffprobe_bin)
    if not exe:
        return False
    result = subprocess.run(
        [exe, "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


# --------------------------------------------------------------------------
@dataclass
class SpokenClip:
    """One synthesized narration block and where it lands on the timeline.

    Per-section synthesis is what makes an editable timeline possible: a single
    narration blob gives one immovable clip, while per-section files give the
    editor blocks they can reorder, trim or drop, and give the b-roll real
    boundaries to cut against.
    """

    index: int
    heading: str
    text: str
    path: Path
    duration: float
    start: float = 0.0

    @property
    def end(self) -> float:
        return self.start + self.duration


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")


def split_sentences(text: str) -> list[str]:
    """Split narration into sentences for caption and marker timing."""
    parts = [p.strip() for p in _SENTENCE_RE.split((text or "").strip()) if p.strip()]
    return parts or ([text.strip()] if (text or "").strip() else [])


def sentence_timings(
    clip: "SpokenClip", words_per_minute: int
) -> list[tuple[float, float, str]]:
    """Distribute a clip's real duration across its sentences by word count.

    Approximate by construction. Most TTS providers do not return word
    boundaries, so sentence starts are interpolated within a section whose
    total duration *is* measured. Good enough to seed captions and cut markers
    that the editor nudges; not frame-accurate, and the edit notes say so.
    """
    sentences = split_sentences(clip.text)
    if not sentences or clip.duration <= 0:
        return []
    weights = [max(1, len(s.split())) for s in sentences]
    total = sum(weights)
    out: list[tuple[float, float, str]] = []
    cursor = clip.start
    for sentence, weight in zip(sentences, weights):
        span = clip.duration * weight / total
        out.append((cursor, cursor + span, sentence))
        cursor += span
    return out


def synthesize_sections(
    blocks: list[tuple[str, str]],
    out_dir: str | Path,
    cfg: MediaConfig,
    *,
    gap_s: float = 0.35,
) -> list[SpokenClip]:
    """Synthesize each ``(heading, text)`` block to its own audio file.

    ``gap_s`` inserts breathing room between blocks on the timeline. It is
    deliberately small - the editor tightens or widens it, and a gap that is
    already there is easier to close than one that has to be created.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    clips: list[SpokenClip] = []
    cursor = 0.0
    for index, (heading, text) in enumerate(blocks):
        if not (text or "").strip():
            continue
        path = out_dir / f"vo_{index:02d}.wav"
        duration = synthesize(text, path, cfg)
        clip = SpokenClip(
            index=index,
            heading=heading or f"Section {index}",
            text=text.strip(),
            path=path,
            duration=duration,
            start=cursor,
        )
        clips.append(clip)
        cursor += duration + gap_s
    return clips
