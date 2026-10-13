import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import json
import pytest
from core.adapter import SearchNeeded
from providers.x_twitter import XTwitterAdapter, tweet_token
from tests.contract.harness import run_contract


def test_tweet_token_known_values():
    assert tweet_token("20") == "6dq1a2xwd93"
    assert tweet_token("2108563438901350773") == "548wn4l3l3v1jvk2r19k9"
    assert tweet_token("2109957980011552839") == "544mmistwz2qs6skyb9"


def test_match_forms():
    a = XTwitterAdapter(session=object())
    assert a.match("https://x.com/NASA/status/2108563438901350773")
    assert a.match("https://twitter.com/NASA/status/2108563438901350773?s=20&t=abc")
    assert a.match("https://x.com/i/status/2108563438901350773")
    assert a.match("https://twitter.com/i/web/status/2108563438901350773")
    assert a.match("https://mobile.twitter.com/NASA/status/2108563438901350773")
    assert a.match("https://t.co/AbCdEf123")
    assert not a.match("https://x.com/NASA")
    assert not a.match("https://example.com/NASA/status/2108563438901350773")


def test_resolve_status_forms():
    a = XTwitterAdapter(session=object())
    for url in ("https://x.com/NASA/status/2108563438901350773",
                "https://twitter.com/i/web/status/2108563438901350773?s=20"):
        ref = a.resolve(url)
        assert ref.platform == "x_twitter" and ref.item_id == "tw-2108563438901350773"
        assert ref.extra["kind"] == "tweet"


def test_resolve_name_needs_search():
    a = XTwitterAdapter(session=object())
    with pytest.raises(SearchNeeded):
        a.resolve("一条推文标题")


def test_resolve_tco_expands(monkeypatch):
    class R:
        url = "https://x.com/SpaceX/status/2109957980011552839"

        def close(self):
            pass

    class S:
        headers = {}
        def get(self, url, **kw):
            return R()
    a = XTwitterAdapter(session=S())
    ref = a.resolve("https://t.co/abc123")
    assert ref.item_id == "tw-2109957980011552839"


def test_list_items_and_contract():
    a = XTwitterAdapter(session=object())
    ref = a.resolve("https://x.com/NASA/status/2108563438901350773")
    assert a.list_items(ref) == [ref]
    assert run_contract(a) == []
