# -*- coding: utf-8 -*-
"""登录态自动恢复：错误识别 / ensure 去重 / pipeline 重试"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import core.auth as auth


def test_looks_like_auth_error():
    assert auth.looks_like_auth_error(RuntimeError("需要登录 cookie"))
    assert auth.looks_like_auth_error(RuntimeError("知乎未登录：请重新导出"))
    assert auth.looks_like_auth_error(RuntimeError("微博风控拦截（432/空响应）"))
    assert auth.looks_like_auth_error(RuntimeError("HTTP 401"))
    assert not auth.looks_like_auth_error(RuntimeError("知乎风控限流（40362）"))
    assert not auth.looks_like_auth_error(RuntimeError("timeout"))


def test_ensure_is_throttled(monkeypatch):
    auth.reset()
    calls = []
    monkeypatch.setattr(auth, "_do_export",
                        lambda p, interactive: calls.append((p, interactive)) or True)
    assert auth.ensure("zhihu", interactive=False)
    assert auth.ensure("zhihu", interactive=False)
    assert calls == [("zhihu", False)]


def test_ensure_caches_failure(monkeypatch):
    auth.reset()
    calls = []
    monkeypatch.setattr(auth, "_do_export",
                        lambda p, interactive: calls.append(p) or False)
    assert auth.ensure("weibo", interactive=False) is False
    assert auth.ensure("weibo", interactive=False) is False
    assert calls == ["weibo"]
    auth.reset()


def test_pipeline_with_auth_retry(monkeypatch):
    import core.pipeline as pipeline
    monkeypatch.setattr(auth, "ensure", lambda platform, interactive=None: True)
    n = {"c": 0}

    def fn():
        n["c"] += 1
        if n["c"] == 1:
            raise RuntimeError("需要登录 cookie")
        return "ok"

    class Adapter:
        reloaded = 0
        def reload_auth(self):
            Adapter.reloaded += 1

    assert pipeline._with_auth_retry("zhihu", fn, Adapter()) == "ok"
    assert n["c"] == 2 and Adapter.reloaded == 1


def test_pipeline_with_auth_retry_gives_up(monkeypatch):
    import core.pipeline as pipeline
    import pytest
    monkeypatch.setattr(auth, "ensure", lambda platform, interactive=None: False)

    def fn():
        raise RuntimeError("需要登录 cookie")

    with pytest.raises(RuntimeError):
        pipeline._with_auth_retry("zhihu", fn)
