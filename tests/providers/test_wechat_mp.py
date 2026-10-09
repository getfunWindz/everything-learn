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
