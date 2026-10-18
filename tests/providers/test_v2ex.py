# -*- coding: utf-8 -*-
"""契约测试（自动生成）：v2ex 适配器

补全建议（按 docs/ADAPTER_TEMPLATE.md）：
1. tests/fixtures/v2ex/ 取样（列表/详情/字幕/正文）
2. 纯函数解析单测（不联网）
3. 真实链接冒烟记录（风控平台如实记录失败）
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import pytest
from core import registry
from tests.contract.harness import run_contract


def _adapter():
    registry.load_providers()
    for a in registry.all():
        if a.name == "v2ex":
            return a
    pytest.skip("适配器 v2ex 未注册（先实现 providers/v2ex.py）")


def test_contract_v2ex():
    assert run_contract(_adapter()) == []


# ---------------- 解析/内容（fixture，离线） ----------------

import json
import os
from providers.v2ex import V2EXAdapter, parse_topic, parse_replies

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "v2ex")


def _fx_text(name):
    return open(os.path.join(FIX, name), encoding="utf-8").read()


def _fx_json(name):
    return json.load(open(os.path.join(FIX, name), encoding="utf-8"))


def test_match_and_resolve():
    a = V2EXAdapter(session=object())
    assert a.match("https://www.v2ex.com/t/1249681")
    assert a.match("https://v2ex.com/t/1249681#reply3")
    assert not a.match("https://www.v2ex.com/")
    ref = a.resolve("https://www.v2ex.com/t/1249681")
    assert ref.item_id == "v2ex-1249681" and ref.extra["topic_id"] == "1249681"


def test_parse_topic_fixture():
    t = parse_topic(_fx_json("topic.json"))
    assert t["id"] == 1249681
    assert t["title"] and t["author"]
    assert t["replies"] >= 1
    assert "<" in t["html"]


def test_parse_replies_fixture():
    reps = parse_replies(_fx_text("topic.html"), limit=5)
    assert len(reps) == 5
    assert all(r["text"] for r in reps)
    assert any(r["author"] for r in reps)


class _Resp:
    def __init__(self, text="", payload=None, status=200):
        self.text, self._payload, self.status_code = text, payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _SeqSession:
    def __init__(self, resps):
        self._resps = list(resps)
        self.headers = {}

    def get(self, *a, **k):
        return self._resps.pop(0)


def test_fetch_content_includes_op_and_replies():
    a = V2EXAdapter(session=_SeqSession([
        _Resp(payload=_fx_json("topic.json")),
        _Resp(text=_fx_text("topic.html")),
    ]))
    ref = a.resolve("https://www.v2ex.com/t/1249681")
    res = a.fetch_content(ref)
    assert res.kind == "document" and res.status in ("ok", "suspect")
    assert any("回复 1" in (b.get("heading") or "") for b in res.blocks)
    assert len(res.blocks) > 1
