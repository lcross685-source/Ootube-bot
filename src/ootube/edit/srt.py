"""SubRip caption writer.

Premiere imports ``.srt`` directly as a caption track, which saves the editor
transcribing narration they already have the text of. Timings are interpolated
within each measured section, so they are close but not frame-accurate - the
editor nudges them, which is still far less work than typing the captions.

Captions also matter for reach: a large share of feed viewing is muted.
"""

from __future__ import annotations

from pathlib import Path

from ..media.tts import SpokenClip, sentence_timings


def _stamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    return f"{total // 3600:02d}:{(total // 60) % 60:02d}:{total % 60:02d},{ms:03d}"


def chunk_cue(
    start: float,
    end: float,
    text: str,
    *,
    max_chars: int = 84,
    max_seconds: float = 6.0,
) -> list[tuple[float, float, str]]:
    """Split one span into readable caption cues.

    A caption holding a whole 60-second section is unusable: viewers need
    roughly two lines at a time, held for a few seconds. Long sentences are
    split on word boundaries and the span divided in proportion to the words
    in each piece, so the text still tracks the narration.
    """
    words = (text or "").split()
    if not words:
        return []

    span = max(0.0, end - start)
    # A cue may be too long by characters, by duration, or both.
    pieces_needed = max(
        1,
        -(-len(" ".join(words)) // max_chars),
        int(span // max_seconds) + (1 if span % max_seconds else 0),
    )
    if pieces_needed <= 1:
        return [(start, end, " ".join(words))]

    per_piece = -(-len(words) // pieces_needed)
    pieces = [
        " ".join(words[i : i + per_piece]) for i in range(0, len(words), per_piece)
    ]
    pieces = [p for p in pieces if p]

    out: list[tuple[float, float, str]] = []
    total_words = sum(len(p.split()) for p in pieces) or 1
    cursor = start
    for piece in pieces:
        share = span * len(piece.split()) / total_words
        out.append((cursor, cursor + share, piece))
        cursor += share
    return out


def build_srt(
    spoken: list[SpokenClip],
    words_per_minute: int,
    *,
    max_chars: int = 84,
    max_seconds: float = 6.0,
) -> str:
    blocks: list[str] = []
    index = 1
    for clip in spoken:
        for start, end, text in sentence_timings(clip, words_per_minute):
            for c_start, c_end, c_text in chunk_cue(
                start, end, text, max_chars=max_chars, max_seconds=max_seconds
            ):
                blocks.append(
                    f"{index}\n{_stamp(c_start)} --> {_stamp(c_end)}\n{c_text}\n"
                )
                index += 1
    return "\n".join(blocks)


def write_srt(
    spoken: list[SpokenClip], out_path: str | Path, words_per_minute: int = 155
) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_srt(spoken, words_per_minute), encoding="utf-8")
    return out
