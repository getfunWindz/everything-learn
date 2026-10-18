# -*- coding: utf-8 -*-
"""内建知识库：索引/搜索/统计/去重"""
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import kb


def _make_item(root, author="作者", title="EUV 光刻", body="光刻机的原理与多层膜镜子"):
    d = os.path.join(str(root), author, title)
    os.makedirs(d, exist_ok=True)
    json.dump({"title": title, "author": author, "platform": "bilibili",
               "url": "https://example.com/1", "kind": "timeline"},
              open(os.path.join(d, "content_info.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    open(os.path.join(d, "content.txt"), "w", encoding="utf-8").write(body)
    open(os.path.join(d, title + "学习报告.md"), "w", encoding="utf-8").write("# " + title + "\n" + body)
    return d


def test_index_search_stats(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    d = _make_item(tmp_path)
    assert kb.index_dir(d, db_path=db)
    rows = kb.search("光刻", db_path=db)
    assert rows and rows[0]["title"] == "EUV 光刻"
    assert kb.stats(db_path=db)["docs"] == 1
    # 重复索引不产生重复行
    assert kb.index_dir(d, db_path=db)
    assert kb.stats(db_path=db)["docs"] == 1


def test_index_root(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    _make_item(tmp_path, "A", "标题一", "光刻")
    _make_item(tmp_path, "B", "标题二", "mRNA 疫苗")
    assert kb.index_root(str(tmp_path), db_path=db) == 2
    assert kb.stats(db_path=db)["docs"] == 2


def test_search_empty_or_missing(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    assert kb.search("", db_path=db) == []
    assert kb.search("不存在", db_path=db) == []


def test_pipeline_kb_index_respects_flag(tmp_path, monkeypatch):
    import core.config as cfg
    import core.pipeline as pipeline
    import kb as kb_mod
    called = []
    monkeypatch.setattr(kb_mod, "index_dir", lambda d: called.append(d))
    monkeypatch.setattr(cfg, "load_config", lambda: {"kb": {"enabled": False}})
    pipeline._kb_index(str(tmp_path))
    assert called == []
    monkeypatch.setattr(cfg, "load_config", lambda: {"kb": {"enabled": True}})
    pipeline._kb_index(str(tmp_path))
    assert called == [str(tmp_path)]
