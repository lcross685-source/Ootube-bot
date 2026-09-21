"""Audio and video production."""

from .tts import synthesize, TTSError
from .render import Renderer, RenderError, ffmpeg_available
from .thumbnail import make_thumbnail
from .broll import fetch_broll

__all__ = [
    "synthesize", "TTSError",
    "Renderer", "RenderError", "ffmpeg_available",
    "make_thumbnail", "fetch_broll",
]
