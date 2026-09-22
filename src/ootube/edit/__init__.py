"""Edit-package generation: turn a script and assets into an editable timeline."""

from .timeline import Clip, Marker, MediaFile, Timeline, build_timeline
from .fcp7 import write_fcp7_xml
from .edl import write_edl
from .srt import write_srt
from .notes import write_edit_notes

__all__ = [
    "Clip", "Marker", "MediaFile", "Timeline", "build_timeline",
    "write_fcp7_xml", "write_edl", "write_srt", "write_edit_notes",
]
