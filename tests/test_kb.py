# -*- coding: utf-8 -*-
"""内建知识库：索引/去重/清理/深度报告重索引/FTS+过滤器"""
import json
import os
import sys

import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import kb


def _make_item(root, author="作者", title="EUV 光刻", body="光刻机的原理与多层膜镜子",
               platform="test", item_id=None, sub_id="1", kind="document",
               published="2026-10-17"):
    d = os.path.join(str(root), author, title)
    os.makedirs(d, exist_ok=True)
    info = {"title": title, "author": author, "platform": platform,
            "item_id": item_id or title, "sub_id": sub_id,
            "url": "https://example.com/" + title, "kind": kind,
            "published": published}
    json.dump(info, open(os.path.join(d, "content_info.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    open(os.path.join(d, "content.txt"), "w", encoding="utf-8").write(body)
    open(os.path.join(d, title + "学习报告.md"), "w", encoding="utf-8").write("# " + title + "\n" + body)
    return d


def _set_body(d, body):
    """同时更新 content 与报告，模拟深度报告覆盖；避免旧报告内容残留。"""
    for name in os.listdir(d):
        if name == "content.txt" or name.endswith("学习报告.md"):
            open(os.path.join(d, name), "w", encoding="utf-8").write(body)
            if name.endswith("学习报告.md"):
                open(os.path.join(d, name), "w", encoding="utf-8").write("# 报告\n" + body)


def test_index_search_stats(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    d = _make_item(tmp_path)
    assert kb.index_dir(d, db_path=db)
    rows = kb.search("光刻", db_path=db)
    assert rows and rows[0]["title"] == "EUV 光刻"
    assert "snippet" in rows[0]
    assert kb.stats(db_path=db)["docs"] == 1
    # 重复索引不产生重复行
    assert kb.index_dir(d, db_path=db)
    assert kb.stats(db_path=db)["docs"] == 1


def test_index_root(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    _make_item(tmp_path, "A", "标题一", "光刻")
    _make_item(tmp_path, "B", "标题二", "mRNA 疫苗")
    r = kb.index_root(str(tmp_path), db_path=db)
    assert r["scanned"] == 2 and r["docs"] == 2
    assert r["pruned"] == 0
    assert kb.stats(db_path=db)["docs"] == 2


def test_search_empty_or_missing(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    assert kb.search("", db_path=db) == []
    assert kb.search("不存在", db_path=db) == []


def test_dedup_same_item_keeps_larger(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    short = _make_item(tmp_path, "A", "短版本", "短内容 光刻", platform="bilibili", item_id="BV1")
    long = _make_item(tmp_path, "B", "长版本", "长内容 光刻 多层膜 镜子 光源", platform="bilibili", item_id="BV1")
    r = kb.index_root(str(tmp_path), db_path=db)
    assert r["scanned"] == 2
    assert kb.stats(db_path=db)["docs"] == 1
    rows = kb.search("多层膜", db_path=db)
    assert rows and rows[0]["path"] == os.path.abspath(long)
    assert os.path.abspath(short) not in [x["path"] for x in rows]


def test_skip_batch_collection(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    base = _make_item(tmp_path, "作者", "合集 测试", "合集汇总 光刻", kind="batch", item_id="C1")
    _make_item(os.path.join(base), "作者", "P01", "P01 光刻 内容", kind="document", item_id="C1", sub_id="1")
    r = kb.index_root(str(tmp_path), db_path=db)
    assert r["docs"] == 1
    assert kb.search("合集汇总", db_path=db) == []
    assert kb.search("P01", db_path=db)
    r2 = kb.index_root(str(tmp_path), db_path=db, include_collections=True)
    assert r2["docs"] == 2
    assert kb.search("合集汇总", db_path=db)


def test_prune_deleted(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    keep = _make_item(tmp_path, "A", "保留", "保留 光刻")
    gone = _make_item(tmp_path, "B", "删除", "删除 mRNA")
    kb.index_root(str(tmp_path), db_path=db)
    import shutil
    shutil.rmtree(gone)
    r = kb.index_root(str(tmp_path), db_path=db)
    assert r["pruned"] == 1
    assert kb.stats(db_path=db)["docs"] == 1
    assert kb.search("mRNA", db_path=db) == []
    assert kb.search("保留", db_path=db)


def test_deep_report_reindex(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    d = _make_item(tmp_path, title="深度报告", body="基线内容")
    kb.index_dir(d, db_path=db)
    assert kb.search("KB_MARKER_2026", db_path=db) == []
    _set_body(d, "深度内容 KB_MARKER_2026 光刻")
    assert kb.search("KB_MARKER_2026", db_path=db) == []
    kb.index_dir(d, db_path=db)
    rows = kb.search("KB_MARKER_2026", db_path=db)
    assert rows and rows[0]["title"] == "深度报告"


def test_search_filters_and_snippet(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    _make_item(tmp_path, "甲", "B站光刻", "B站 光刻机 内容", platform="bilibili",
               kind="timeline", published="2026-10-17")
    _make_item(tmp_path, "乙", "知乎显卡", "知乎 显卡 内容", platform="zhihu",
               kind="document", published="2026-10-01")
    kb.index_root(str(tmp_path), db_path=db)
    assert [r["title"] for r in kb.search("光刻", db_path=db, platform="bilibili")] == ["B站光刻"]
    assert [r["title"] for r in kb.search("光刻", db_path=db, platform="zhihu")] == []
    assert [r["title"] for r in kb.search("内容", db_path=db, author="乙")] == ["知乎显卡"]
    assert [r["title"] for r in kb.search("内容", db_path=db, kind="timeline")] == ["B站光刻"]
    assert [r["title"] for r in kb.search("内容", db_path=db, since="2026-10-10")] == ["B站光刻"]
    row = kb.search("光刻", db_path=db, platform="bilibili")[0]
    assert "光刻" in row["snippet"]
    # 3 字符走 FTS5，同时应用 platform 过滤
    assert [r["title"] for r in kb.search("光刻机", db_path=db, platform="bilibili")] == ["B站光刻"]


def test_like_wildcards_escaped(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    _make_item(tmp_path, title="百分比", body="100% 光刻")
    _make_item(tmp_path, title="普通", body="普通 内容")
    kb.index_root(str(tmp_path), db_path=db)
    # % 只匹配字面量，不得变成“匹配全部”
    rows = kb.search("%", db_path=db)
    assert [r["title"] for r in rows] == ["百分比"]
    rows = kb.search("100%", db_path=db)
    assert rows and rows[0]["title"] == "百分比"


def test_short_chinese_query_fallback(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    d = _make_item(tmp_path, title="光刻机", body="EUV 光刻机 多层膜")
    kb.index_dir(d, db_path=db)
    assert kb.search("光刻", db_path=db)
    assert kb.search("光刻机", db_path=db)


def test_delete_doc_and_rebuild(tmp_path):
    db = str(tmp_path / "kb.sqlite3")
    d = _make_item(tmp_path, title="可删除", body="删除测试 光刻")
    kb.index_dir(d, db_path=db)
    assert kb.stats(db_path=db)["docs"] == 1
    assert kb.delete_doc(d, db_path=db) == 1
    assert kb.stats(db_path=db)["docs"] == 0
    assert kb.search("光刻", db_path=db) == []
    kb.index_dir(d, db_path=db)
    assert kb.rebuild(str(tmp_path), db_path=db)["docs"] == 1
    assert kb.stats(db_path=db)["docs"] == 1


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


def test_cli_kb_build_search_filters(tmp_path, monkeypatch, capsys):
    import el
    from core import config
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(el, "setup_logging", lambda *a, **k: "")
    db = str(tmp_path / "kb.sqlite3")
    _make_item(tmp_path, "甲", "B站光刻", "B站 光刻机 内容", platform="bilibili",
               kind="timeline", published="2026-10-17")
    _make_item(tmp_path, "乙", "知乎显卡", "知乎 显卡 内容", platform="zhihu",
               kind="document", published="2026-10-01")
    el.main(["kb", "build", str(tmp_path), "--db", db])
    out = capsys.readouterr().out
    assert "已扫描 2 个目录" in out and "知识库 2 条" in out
    el.main(["kb", "search", "光刻", "--db", db, "--platform", "bilibili",
             "--since", "2026-10-10"])
    out = capsys.readouterr().out
    assert "B站光刻" in out and "光刻" in out
    with pytest.raises(SystemExit) as e:
        el.main(["kb", "search", "不存在", "--db", db])
    assert e.value.code == 1


def test_schema_v1_rebuilt(tmp_path):
    import sqlite3
    db = str(tmp_path / "old.sqlite3")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE docs(id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT, "
              "author TEXT, platform TEXT, url TEXT, kind TEXT, created_at TEXT, "
              "chars INTEGER, content TEXT)")
    c.execute("INSERT INTO docs(path,title,content) VALUES('old','old','old')")
    c.execute("PRAGMA user_version=1")
    c.commit(); c.close()
    st = kb.stats(db_path=db)
    assert st["docs"] == 0 and st["fts"] is True
    c = sqlite3.connect(db)
    assert c.execute("PRAGMA user_version").fetchone()[0] == kb.SCHEMA_VERSION
    c.close()


def test_search_without_fts_falls_back(tmp_path, monkeypatch):
    db = str(tmp_path / "kb.sqlite3")
    d = _make_item(tmp_path, title="回退", body="EUV 光刻机 多层膜")
    kb.index_dir(d, db_path=db)
    monkeypatch.setattr(kb, "_fts_exists", lambda conn: False)
    assert kb.search("光刻机", db_path=db)


def test_prune_stale_direct(tmp_path):
    import shutil
    db = str(tmp_path / "kb.sqlite3")
    d = _make_item(tmp_path, title="直接清理", body="光刻")
    kb.index_dir(d, db_path=db)
    shutil.rmtree(d)
    assert kb.prune_stale(str(tmp_path), db_path=db) == 1
    assert kb.stats(db_path=db)["docs"] == 0
