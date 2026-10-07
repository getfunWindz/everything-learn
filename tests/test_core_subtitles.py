"""core/subtitles：vtt/srt 共享解析测试"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from core import subtitles as st


def test_parse_vtt_shared_behaviour():
    segs = st.parse_vtt("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello vtt\n")
    assert segs == [{"start": 1.0, "end": 3.0, "text": "Hello vtt"}]


def test_parse_srt():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "podcast", "sample.srt")
    segs = st.parse_srt(open(path, encoding="utf-8").read())
    assert len(segs) == 2
    assert segs[0]["start"] == 1.0 and segs[0]["text"] == "Hello srt"
    assert segs[1]["start"] == 3.5 and segs[1]["text"] == "Second line"


def test_youtube_reuses_shared_parser():
    import providers.youtube as y
    assert y.parse_vtt is st.parse_vtt
