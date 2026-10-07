import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from core.models import ItemRef, ItemMeta, Segment, Block, TimelineContent, DocumentContent, ContentResult


def test_item_ref_defaults():
    r = ItemRef(platform="bilibili", item_id="BV1xx", sub_id="1", duration=100)
    assert r.platform == "bilibili" and r.sub_id == "1" and r.duration == 100


def test_timeline_content_serializes_lines():
    c = TimelineContent(segments=[Segment(0.0, 1.5, "你好")])
    assert c.kind == "timeline" and c.segments[0].text == "你好"


def test_document_content_blocks():
    c = DocumentContent(blocks=[Block(heading="引言", text="正文")])
    assert c.kind == "document" and c.blocks[0].heading == "引言"


def test_content_result_status():
    r = ContentResult(kind="timeline", status="suspect", segments=[], reason="零命中")
    assert r.status == "suspect" and r.reason == "零命中"


def test_item_meta_fields():
    m = ItemMeta(title="标题", author="UP", duration=100, stats={"view": 1})
    assert m.title == "标题" and m.stats["view"] == 1
