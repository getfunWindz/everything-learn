# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import canary


def test_samples_unique_and_cover_platforms():
    names = [s["name"] for s in canary.SAMPLES]
    assert len(names) == len(set(names)) == 12
    assert {"bilibili", "zhihu", "weibo", "wechat_mp", "ted", "medium",
            "podcast", "v2ex", "youtube", "douyin", "x_twitter", "xiaohongshu"} <= set(names)


def test_summarize_required_vs_optional():
    results = [
        {"name": "v2ex", "status": "ok"},
        {"name": "zhihu", "status": "error"},
        {"name": "youtube", "status": "error"},
    ]
    s = canary.summarize(results)
    assert s["ok"] is False and s["failed"] == ["zhihu"]
    assert s["optional_failed"] == ["youtube"]


def test_summarize_all_required_ok_with_optional_fail():
    results = [{"name": "v2ex", "status": "ok"}, {"name": "douyin", "status": "error"}]
    s = canary.summarize(results)
    assert s["ok"] is True and s["optional_failed"] == ["douyin"]
