import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
from core.artifacts import build_info, render_content_txt, write_item_artifacts
from core.models import ItemRef, ItemMeta, TimelineContent, DocumentContent


def test_render_timeline_lines():
    out = render_content_txt(TimelineContent(segments=[{"start": 0.0, "end": 1.5, "text": "你好"}]))
    assert out == "[0.0-1.5] 你好\n"


def test_render_document_markdown():
    out = render_content_txt(DocumentContent(blocks=[{"heading": "引言", "text": "正文"}]))
    assert out.startswith("## 引言") and "正文" in out


def test_write_item_artifacts_timeline(tmp_path):
    ref = ItemRef(platform="bilibili", item_id="BV1xx", sub_id="1", url="u", title="P1")
    meta = ItemMeta(title="标题", author="UP", duration=100, page="1", page_part="P1基础")
    info = build_info(meta, ref, kind="timeline", source="字幕")
    write_item_artifacts(str(tmp_path), info,
                         TimelineContent(segments=[{"start": 0.0, "end": 1.5, "text": "你好"}]))
    saved = json.load(open(os.path.join(str(tmp_path), "content_info.json"), encoding="utf-8"))
    assert saved["platform"] == "bilibili" and saved["kind"] == "timeline"
    assert saved["source"] == "字幕" and saved["item_id"] == "BV1xx"
    sub = (tmp_path / "content.txt").read_text(encoding="utf-8")
    assert sub.startswith("[0.0-1.5] 你好")
    tpl = (tmp_path / "report_template.md").read_text(encoding="utf-8")
    assert "## 知识详解" in tpl and "标题" in tpl


def test_write_item_artifacts_document_not_implemented(tmp_path):
    """document 骨架尚未实现（M3）→ 明确报错而非产出坏骨架"""
    ref = ItemRef(platform="zhihu", item_id="q1", url="u")
    meta = ItemMeta(title="问题", author="答主")
    info = build_info(meta, ref, kind="document", source="document")
    with pytest.raises(NotImplementedError):
        write_item_artifacts(str(tmp_path), info, DocumentContent(blocks=[{"text": "x"}]))
