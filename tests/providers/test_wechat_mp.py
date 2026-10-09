import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import pytest
from core.adapter import SearchNeeded
from providers.wechat_mp import WeChatMpAdapter
from tests.contract.harness import run_contract


def test_match_short_and_long():
    a = WeChatMpAdapter(session=object())
    assert a.match("https://mp.weixin.qq.com/s/AbCdEf1234567890_-abc")
    assert a.match("https://mp.weixin.qq.com/s?__biz=MzI3MjY1ODI2Ng==&mid=2247484162&idx=1&sn=abc123")
    assert a.match("http://mp.weixin.qq.com/s/AbCdEf1234567890_-abc")
    assert not a.match("https://mp.weixin.qq.com/")
    assert not a.match("https://mp.weixin.qq.com/mp/appmsgalbum?__biz=x")
    assert not a.match("https://example.com/s/AbCdEf1234567890_-abc")


def test_resolve_short_link():
    a = WeChatMpAdapter(session=object())
    ref = a.resolve("https://mp.weixin.qq.com/s/AbCdEf1234567890_-abc")
    assert ref.platform == "wechat_mp"
    assert ref.item_id == "wx-AbCdEf1234567890_-abc"
    assert ref.url == "https://mp.weixin.qq.com/s/AbCdEf1234567890_-abc"
    assert ref.extra["kind"] == "article"


def test_resolve_long_link():
    a = WeChatMpAdapter(session=object())
    ref = a.resolve(
        "https://mp.weixin.qq.com/s?__biz=MzI3MjY1ODI2Ng==&mid=2247484162&idx=1&sn=abc123")
    assert ref.item_id == "wx-MzI3MjY1ODI2Ng_2247484162_1"
    assert ref.extra["mid"] == "2247484162"


def test_resolve_name_needs_search():
    a = WeChatMpAdapter(session=object())
    with pytest.raises(SearchNeeded):
        a.resolve("一篇公众号文章")


def test_list_items_and_contract():
    a = WeChatMpAdapter(session=object())
    ref = a.resolve("https://mp.weixin.qq.com/s/AbCdEf1234567890_-abc")
    assert a.list_items(ref) == [ref]
    assert run_contract(a) == []


FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "wechat_mp")


def _fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, text):
        self._text = text
        self.headers = {}

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        return _Resp(self._text)


def test_fetch_content_from_fixture():
    a = WeChatMpAdapter(session=_FakeSession(_fx("article.html")))
    ref = a.resolve("https://mp.weixin.qq.com/s/-cThzr5N2w3IEYYf-duCDA")
    res = a.fetch_content(ref)
    assert res.kind == "document" and res.status == "ok"
    assert any("Cannot assign" in b["text"] for b in res.blocks)
    assert [b["heading"] for b in res.blocks if b["heading"]] == ["一、问题现象", "二、排查过程"]
    assert res.images == [
        "https://mmbiz.qpic.cn/mmbiz_png/9aKOAbMEQ5qcotFGPMS0qazbMibmnluxObCrSQCw3yVmnEOjcvRrSq95NbiaesvqfWKccRx4KOicUIpYtxP5MLWcw/640"]


def test_fetch_meta_from_fixture():
    a = WeChatMpAdapter(session=_FakeSession(_fx("article.html")))
    ref = a.resolve("https://mp.weixin.qq.com/s/-cThzr5N2w3IEYYf-duCDA")
    meta = a.fetch_meta(ref)
    assert meta.title == "这个 TCP 问题你得懂：Cannot assign requested address"
    assert meta.author == "AlwaysBeta"
    assert meta.published == "2021-09-06"


def test_error_page_readable():
    a = WeChatMpAdapter(session=_FakeSession(_fx("error_param.html")))
    ref = a.resolve("https://mp.weixin.qq.com/s/aaaaaaaaaaaaaaaaaaaaaa")
    res = a.fetch_content(ref)
    assert res.status == "empty" and "参数错误" in res.reason
