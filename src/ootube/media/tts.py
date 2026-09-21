"""Text-to-speech.

Several providers are supported because voice is the most replaceable part of
the stack and the one most likely to change for cost or quality reasons. The
``silent`` provider generates correctly-timed silence so the rest of the
pipeline can be exercised end to end without an API key or network access.
"""

from __future__ import annotations

import logging
import os
import shutil
import struct
import subprocess
import wave
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
