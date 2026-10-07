import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
from core.browserfetch import strip_json_prefix, parse_fetch_result, BrowserFetchError


def test_strip_json_prefix():
    raw = ')]}while(1);</x>{"success":true,"payload":{"value":{}}}'
    assert strip_json_prefix(raw).startswith("{")


def test_strip_plain_json():
    assert strip_json_prefix('{"a":1}') == '{"a":1}'


def test_parse_fetch_result_ok():
    data = parse_fetch_result({"status": 200, "text": '{"a":1}'})
    assert data == {"a": 1}


def test_parse_fetch_result_prefixed():
    data = parse_fetch_result({"status": 200, "text": ')]}while(1);</x>{"a":2}'})
    assert data == {"a": 2}


def test_parse_fetch_result_blocked():
    with pytest.raises(BrowserFetchError):
        parse_fetch_result({"status": 403, "text": "<html>Just a moment...</html>"})


def test_parse_fetch_result_attention_required():
    with pytest.raises(BrowserFetchError):
        parse_fetch_result({"status": 200, "text": "<html>Attention Required! | Cloudflare</html>"})


def test_parse_fetch_result_non_json():
    with pytest.raises(BrowserFetchError):
        parse_fetch_result({"status": 200, "text": "not json at all"})
