import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import json
import pytest
from core.adapter import SearchNeeded
from providers.xiaohongshu import XiaohongshuAdapter
from tests.contract.harness import run_contract


def test_match_forms():
    a = XiaohongshuAdapter(session=object())
    assert a.match("https://www.xiaohongshu.com/explore/6acde40b000000001b02c980?xsec_token=ABC&xsec_source=pc_share")
    assert a.match("https://www.xiaohongshu.com/discovery/item/6acde40b000000001b02c980?xsec_token=ABC")
    assert a.match("https://xhslink.cn/o/5HWLkuHJZ7x")
    assert a.match("https://xhslink.com/a/AbCdEf123")
    assert not a.match("https://www.xiaohongshu.com/explore")
    assert not a.match("https://example.com/explore/6acde40b000000001b02c980")


def test_resolve_direct_keeps_token():
    a = XiaohongshuAdapter(session=object())
    ref = a.resolve("https://www.xiaohongshu.com/explore/6acde40b000000001b02c980"
                    "?xsec_token=ABC123=&xsec_source=pc_share")
    assert ref.platform == "xiaohongshu"
    assert ref.item_id == "xhs-6acde40b000000001b02c980"
    assert ref.extra["kind"] == "note"
    assert ref.extra["token"] == "ABC123="
    assert "xsec_token=ABC123=" in ref.url


def test_resolve_short_link(monkeypatch):
    class R:
        url = ("https://www.xiaohongshu.com/discovery/item/6acde40b000000001b02c980"
               "?xsec_token=CBTYel6EB4CeoNIadZD8vJaSTjjgiC9ytiUWXkw6Y-r24=&xsec_source=app_share")

        def close(self):
            pass

    class S:
        headers = {}
        def get(self, url, **kw):
            return R()
    a = XiaohongshuAdapter(session=S())
    ref = a.resolve("https://xhslink.cn/o/5HWLkuHJZ7x")
    assert ref.item_id == "xhs-6acde40b000000001b02c980"
    assert ref.extra["token"].startswith("CBTYel6EB4")


def test_resolve_name_needs_search():
    a = XiaohongshuAdapter(session=object())
    with pytest.raises(SearchNeeded):
        a.resolve("一篇小红书笔记")


def test_list_items_and_contract():
    a = XiaohongshuAdapter(session=object())
    ref = a.resolve("https://www.xiaohongshu.com/explore/6acde40b000000001b02c980?xsec_token=ABC")
    assert a.list_items(ref) == [ref]
    assert run_contract(a) == []
