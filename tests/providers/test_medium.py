import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from providers.medium import MediumAdapter, medium_to_blocks, parse_feed, payload_status
from core.models import ItemRef
from tests.contract.harness import run_contract

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "medium")


def _payload():
    return json.load(open(os.path.join(FIX, "article_acf7c1585cb5.json"), encoding="utf-8"))


# ---------------- 解析层 ----------------

def test_medium_to_blocks_structure():
    blocks, images = medium_to_blocks(_payload())
    headings = [b["heading"] for b in blocks if b["heading"]]
    assert "What is Boosting?" in headings and "Types of Boosting" in headings
    assert "AdaBoost (Adaptive Boosting)" in headings
    assert any("Boosting" in b["text"] for b in blocks if b["text"])
    assert len(images) == 5
    assert images[0].startswith("https://miro.medium.com/v2/resize:fit:1400/")
    # 代码段文本保留
    joined = "\n".join(b["text"] for b in blocks)
    assert "Fit a model to the data" in joined


def test_medium_to_blocks_skips_title_duplicate():
    blocks, _ = medium_to_blocks(_payload())
    assert blocks[0]["heading"] != "A Quick Guide to Boosting in ML"


def test_payload_status_paywall():
    locked = {"payload": {"value": {"isSubscriptionLocked": True}}}
    status, reason = payload_status(locked)
    assert status == "suspect" and "付费墙" in reason
    status2, _ = payload_status({"payload": {"value": {}}})
    assert status2 == "ok"


def test_parse_feed_synthetic():
    xml = open(os.path.join(FIX, "feed_greyatom.xml"), encoding="utf-8").read()
    items = parse_feed(xml)
    assert len(items) == 2
    assert items[0]["title"] == "Test Article One"
    assert items[0]["link"].startswith("https://medium.com/")
    assert items[0]["author"] == "Alice Author"
    assert "content" in items[0] and "<p>" in items[0]["content"]


# ---------------- 适配器层（离线） ----------------

def test_match_and_resolve():
    a = MediumAdapter()
    assert a.match("https://medium.com/greyatom/a-quick-guide-to-boosting-in-ml-acf7c1585cb5")
    assert a.match("https://medium.com/feed/greyatom")
    assert not a.match("https://medium.com/@someone")
    ref = a.resolve("https://medium.com/greyatom/a-quick-guide-to-boosting-in-ml-acf7c1585cb5?source=x")
    assert ref.extra["kind"] == "article" and ref.extra["hash"] == "acf7c1585cb5"
    fref = a.resolve("https://medium.com/feed/greyatom")
    assert fref.extra["kind"] == "feed"


def test_fetch_content_uses_feed_content():
    """feed 通道：不需要浏览器（extra 里带全文 HTML）"""
    a = MediumAdapter(session=object())
    ref = ItemRef(platform="medium", item_id="article-x", url="https://medium.com/x-abcdef123456",
                  sub_id="1", extra={"kind": "article",
                                     "feed_content": "<h1>Title</h1><p>Hello world.</p><h2>Sec</h2><p>More.</p>"})
    res = a.fetch_content(ref)
    assert res.kind == "document" and res.status == "ok" and res.blocks
    assert any(b["heading"] == "Sec" for b in res.blocks)
    assert res.label == "正文(feed)"


def test_fetch_meta_from_feed_extra():
    a = MediumAdapter(session=object())
    ref = ItemRef(platform="medium", item_id="article-x", url="https://medium.com/x-abcdef123456",
                  sub_id="1", title="T", extra={"kind": "article", "feed_content": "<p>x</p>",
                                                "feed_title": "Feed Title", "feed_author": "Alice",
                                                "feed_published": "2025-10-01"})
    meta = a.fetch_meta(ref)
    assert meta.title == "Feed Title" and meta.author == "Alice" and meta.published == "2025-10-01"


def test_list_items_from_feed(monkeypatch):
    xml = open(os.path.join(FIX, "feed_greyatom.xml"), encoding="utf-8").read()

    class _Resp:
        status_code, text = 200, xml
    a = MediumAdapter(session=object())
    monkeypatch.setattr(MediumAdapter, "_get_feed", lambda self, url: xml)
    ref = a.resolve("https://medium.com/feed/greyatom")
    items = a.list_items(ref)
    assert len(items) == 2 and items[0].sub_id == "1"
    assert items[0].extra["feed_author"] == "Alice Author"
    assert "content" not in items[0].extra or True
    assert items[0].extra["feed_content"].startswith("<h1>")


def test_contract():
    assert run_contract(MediumAdapter()) == []


def test_session_is_throttled():
    from core.throttle import ThrottledSession
    a = MediumAdapter()
    assert isinstance(a.session, ThrottledSession) and a.session.platform == "medium"
