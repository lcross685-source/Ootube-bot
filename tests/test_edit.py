"""Tests for the edit package - timeline, project files, captions, notes.

The project file is the deliverable, so these tests check the details that
make an NLE import succeed or fail: integer frames, file dedup, audio
sourcetracks, and encoded path URLs.
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ootube.edit.edl import build_edl
from ootube.edit.fcp7 import build_xml, write_fcp7_xml
from ootube.edit.package import build_package
from ootube.edit.srt import build_srt, chunk_cue
from ootube.edit.timeline import MediaFile, build_timeline, to_frames, to_timecode
from ootube.media.tts import synthesize_sections

from .conftest import NOW


@pytest.fixture
def spoken(config, tmp_path):
    blocks = [
        ("hook", "The Fed cut rates today. Markets moved within minutes."),
        ("impact", " ".join(["word"] * 120)),
        ("read", "Two more cuts are coming. The dot plot says so."),
    ]
    return synthesize_sections(blocks, tmp_path / "audio", config.media)


@pytest.fixture
def broll_clips(tmp_path):
    """Two real encoded clips: one short enough to repeat, one long."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path / "broll"
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for name, duration in (("short.mp4", 3), ("long.mp4", 45)):
        path = out / name
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"testsrc=size=320x180:rate=30:duration={duration}",
             "-c:v", "libx264", "-preset", "ultrafast", str(path)],
            check=True, timeout=120,
        )
        made.append(path)
    return made


class TestTimecode:
    @pytest.mark.parametrize("seconds,expected", [
        (0, "00:00:00:00"), (1.5, "00:00:01:15"),
        (61.0, "00:01:01:00"), (3661.0, "01:01:01:00"),
    ])
    def test_frames_to_timecode(self, seconds, expected):
        assert to_timecode(to_frames(seconds, 30), 30) == expected


class TestMediaFile:
    def test_pathurl_is_absolute_file_url(self, tmp_path):
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x")
        media = MediaFile(id="m", name="clip.mp4", path=f, duration_frames=30)
        assert media.pathurl.startswith("file://localhost/")

    def test_pathurl_encodes_special_characters(self, tmp_path):
        """An unencoded space truncates the path in some importers."""
        d = tmp_path / "my clips"
        d.mkdir()
        f = d / "a&b.mp4"
        f.write_bytes(b"x")
        media = MediaFile(id="m", name="a&b.mp4", path=f, duration_frames=30)
        assert "%20" in media.pathurl
        assert "%26" in media.pathurl
        assert " " not in media.pathurl


class TestTimeline:
    def test_narration_becomes_one_clip_per_section(self, spoken):
        tl = build_timeline("t", spoken, [None] * 3)
        assert len(tl.audio_tracks[0]) == len(spoken)

    def test_reserves_an_empty_music_track(self, spoken):
        tl = build_timeline("t", spoken, [None] * 3)
        assert len(tl.audio_tracks) == 2
        assert tl.audio_tracks[1] == []

    def test_missing_broll_leaves_a_gap_and_a_marker(self, spoken):
        tl = build_timeline("t", spoken, [None] * 3, sentence_markers=False)
        assert tl.video_tracks[0] == []
        assert any(m.name == "NO B-ROLL" for m in tl.markers)

    def test_short_clip_repeats_to_cover_the_section(self, spoken, broll_clips):
        short = broll_clips[0]
        tl = build_timeline(
            "t", spoken, [short, None, None],
            broll_durations={str(short): 3.0}, sentence_markers=False,
        )
        assert len(tl.video_tracks[0]) > 1
        assert any(m.name == "B-ROLL REPEATS" for m in tl.markers)

    def test_repeated_clips_are_contiguous(self, spoken, broll_clips):
        short = broll_clips[0]
        tl = build_timeline(
            "t", spoken, [short, None, None],
            broll_durations={str(short): 3.0}, sentence_markers=False,
        )
        clips = tl.video_tracks[0]
        for a, b in zip(clips, clips[1:]):
            assert b.start == a.end, "gap or overlap between repeats"

    def test_long_clip_is_placed_once(self, spoken, broll_clips):
        long = broll_clips[1]
        tl = build_timeline(
            "t", spoken, [long, None, None],
            broll_durations={str(long): 45.0}, sentence_markers=False,
        )
        assert len(tl.video_tracks[0]) == 1

    def test_markers_are_sorted(self, spoken):
        tl = build_timeline("t", spoken, [None] * 3)
        frames = [m.frame for m in tl.markers]
        assert frames == sorted(frames)

    def test_sentence_markers_can_be_disabled(self, spoken):
        with_m = build_timeline("t", spoken, [None] * 3, sentence_markers=True)
        without = build_timeline("t", spoken, [None] * 3, sentence_markers=False)
        assert len(with_m.markers) > len(without.markers)


class TestFcp7Xml:
    def test_root_is_xmeml_v5(self, spoken):
        root = build_xml(build_timeline("t", spoken, [None] * 3))
        assert root.tag == "xmeml"
        assert root.get("version") == "5"

    def test_all_times_are_integer_frames(self, spoken):
        """Seconds anywhere in these fields make the import silently drift."""
        root = build_xml(build_timeline("t", spoken, [None] * 3))
        for tag in ("start", "end", "in", "out", "duration", "frame"):
            for el in root.iter(tag):
                assert el.text.lstrip("-").isdigit(), f"<{tag}> is not an integer"

    def test_files_are_defined_once_then_referenced(self, spoken, broll_clips):
        """Repeating a file body creates duplicate bin items on import."""
        short = broll_clips[0]
        tl = build_timeline(
            "t", spoken, [short, None, None],
            broll_durations={str(short): 3.0}, sentence_markers=False,
        )
        root = build_xml(tl)
        files = root.findall(".//file")
        full = [f for f in files if f.find("pathurl") is not None]
        refs = [f for f in files if f.find("pathurl") is None]
        assert len(refs) > 0, "repeated clip should reference, not redefine"
        ids = {f.get("id") for f in full}
        assert len(ids) == len(full), "a file was defined twice"
        assert all(r.get("id") in ids for r in refs)

    def test_audio_clips_have_sourcetrack(self, spoken):
        """Without sourcetrack the clips import silent."""
        root = build_xml(build_timeline("t", spoken, [None] * 3))
        clips = root.findall(".//audio//clipitem")
        assert clips
        for clip in clips:
            st = clip.find("sourcetrack")
            assert st is not None
            assert st.find("mediatype").text == "audio"

    def test_markers_are_point_markers(self, spoken):
        root = build_xml(build_timeline("t", spoken, [None] * 3))
        markers = root.findall("sequence/marker")
        assert markers
        assert all(m.find("out").text == "-1" for m in markers)

    def test_written_file_is_parseable_and_declares_doctype(self, spoken, tmp_path):
        out = write_fcp7_xml(build_timeline("t", spoken, [None] * 3), tmp_path / "p.xml")
        text = out.read_text()
        assert "<!DOCTYPE xmeml>" in text
        ET.parse(out)   # raises if malformed

    def test_sequence_duration_covers_all_clips(self, spoken):
        tl = build_timeline("t", spoken, [None] * 3)
        root = build_xml(tl)
        declared = int(root.find("sequence/duration").text)
        ends = [c.end for track in tl.audio_tracks + tl.video_tracks for c in track]
        assert declared == max(ends)


class TestEdl:
    def test_has_required_header(self, spoken):
        edl = build_edl(build_timeline("t", spoken, [None] * 3))
        assert edl.startswith("TITLE:")
        assert "FCM: NON-DROP FRAME" in edl

    def test_events_are_numbered_sequentially(self, spoken):
        edl = build_edl(build_timeline("t", spoken, [None] * 3))
        numbers = [
            int(line.split()[0])
            for line in edl.splitlines()
            if line[:3].isdigit()
        ]
        assert numbers == list(range(1, len(numbers) + 1))


class TestCaptions:
    def test_long_span_is_chunked(self):
        """A caption holding a whole section is unreadable."""
        cues = chunk_cue(0, 60, " ".join(["word"] * 200))
        assert len(cues) > 1
        assert max(b - a for a, b, _ in cues) <= 6.5
        assert max(len(t) for _, _, t in cues) <= 90

    def test_short_span_is_left_alone(self):
        cues = chunk_cue(0, 3, "Short line.")
        assert cues == [(0, 3, "Short line.")]

    def test_empty_text_yields_nothing(self):
        assert chunk_cue(0, 3, "   ") == []

    def test_srt_is_sequentially_numbered(self, spoken, config):
        srt = build_srt(spoken, config.media.words_per_minute)
        indices = [
            int(b.splitlines()[0])
            for b in srt.split("\n\n") if b.strip() and b.splitlines()[0].isdigit()
        ]
        assert indices == list(range(1, len(indices) + 1))

    def test_srt_timestamps_are_well_formed(self, spoken, config):
        import re
        srt = build_srt(spoken, config.media.words_per_minute)
        stamps = re.findall(r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})", srt)
        assert stamps
        assert len(stamps) == srt.count("-->")


class TestPackage:
    def test_writes_every_expected_artifact(self, config, tmp_path, stub_writer):
        from ootube.models import Topic
        topic = Topic(term="Fed cuts rates", niche="personal-finance")
        script = stub_writer.write(topic, now=NOW)
        pkg = build_package(script, topic, config, tmp_path / "pkg", broll=[None] * 3, now=NOW)

        for path in (pkg.project_xml, pkg.project_edl, pkg.captions,
                     pkg.notes, pkg.metadata_path):
            assert Path(path).exists(), f"missing {path}"
            assert Path(path).stat().st_size > 0
        assert pkg.audio_paths and all(Path(p).exists() for p in pkg.audio_paths)
        assert pkg.section_count == len(script.sections) + 1  # +1 for the hook

    def test_metadata_round_trips_for_publish(self, config, tmp_path, stub_writer):
        from ootube.models import Topic
        topic = Topic(term="Fed cuts rates", niche="personal-finance")
        script = stub_writer.write(topic, now=NOW)
        pkg = build_package(script, topic, config, tmp_path / "pkg", broll=[None] * 3, now=NOW)

        meta = json.loads(Path(pkg.metadata_path).read_text())
        assert meta["title"] == script.title
        assert meta["claims"] and all(c["source_url"] for c in meta["claims"])
        assert meta["niche"] == "personal-finance"

    def test_reports_sections_missing_footage(self, config, tmp_path, stub_writer):
        from ootube.models import Topic
        topic = Topic(term="Fed cuts rates", niche="ai-tools")
        script = stub_writer.write(topic, now=NOW)
        pkg = build_package(script, topic, config, tmp_path / "pkg", broll=[None] * 3, now=NOW)
        assert pkg.needs_footage
        assert len(pkg.missing_broll) == len(script.sections)

    def test_notes_name_the_publish_command(self, config, tmp_path, stub_writer):
        from ootube.models import Topic
        topic = Topic(term="Fed cuts rates", niche="ai-tools")
        script = stub_writer.write(topic, now=NOW)
        pkg = build_package(script, topic, config, tmp_path / "pkg", broll=[None] * 3, now=NOW)
        notes = Path(pkg.notes).read_text()
        assert f"ootube publish {topic.key}" in notes
        assert "NO B-ROLL" in notes or "no b-roll" in notes.lower()
