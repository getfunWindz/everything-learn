# -*- coding: utf-8 -*-
"""R1–R13 报告结构自检：知识点结构/编号/自测密度/乱码/占位符/术语校验"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import report_check

GOOD = """# 标题

## 学习地图（知识点全览）

1. 概念 A
2. 概念 B

## 知识详解

### 段 1｜开场（[0:00–5:00]）

#### 知识点 1：概念 A（[0:00–2:00]）

- **定义（官方）**：A 的定义。
- **通俗**：A 的通俗解释。
- **名词注释**：
  - **A**：术语 A 的注释。

#### 知识点 2：概念 B（[2:00–4:00]）

- **定义（官方）**：B 的定义。
- **通俗**：B 的通俗解释。
- **名词注释**：
  - **B**：术语 B 的注释。

## 金句与重要观点

1. “A 是一句话。”（[0:10]）
2. “B 也是一句话。”（[1:10]）
3. “C 还是一句话。”（[2:10]）
4. “D 仍是。”（[3:10]）
5. “E 最后一句。”（[4:10]）

## 自测

1. A 和 B 的区别？（知识点 1/2）

## 复检说明

档 2，覆盖 100%。
"""


def test_good_report_passes():
    r = report_check.check_report(GOOD)
    assert r["ok"] is True, [c for c in r["checks"] if c["level"] == "fail"]
    assert {c["name"] for c in r["checks"]} >= {"sections", "point_structure", "map", "selftest"}


def test_alias_section_names_accepted():
    """R5/R6 旧称（知识点全览 / 学习路径与自测）也应被识别，
    避免按 SKILL 字面写标题却被门禁误杀"""
    alias = (GOOD.replace("## 学习地图（知识点全览）", "## 知识点全览")
                 .replace("## 自测", "## 学习路径与自测"))
    r = report_check.check_report(alias)
    assert r["ok"] is True, [c for c in r["checks"] if c["level"] == "fail"]


def test_missing_point_structure_fails():
    bad = GOOD.replace("- **通俗**：B 的通俗解释。\n", "")
    r = report_check.check_report(bad)
    assert r["ok"] is False
    fail = [c for c in r["checks"] if c["level"] == "fail" and c["name"] == "point_structure"][0]
    assert "2" in fail["detail"]


def test_numbering_gap_fails():
    bad = GOOD.replace("#### 知识点 2：概念 B", "#### 知识点 3：概念 B")
    r = report_check.check_report(bad)
    assert r["ok"] is False
    assert any(c["name"] == "numbering" and c["level"] == "fail" for c in r["checks"])


def test_map_mismatch_fails():
    bad = GOOD.replace("2. 概念 B", "3. 概念 B")
    r = report_check.check_report(bad)
    assert r["ok"] is False
    assert any(c["name"] == "map" and c["level"] == "fail" for c in r["checks"])


def test_selftest_too_few_fails():
    # 7 个知识点至少需要 ceil(7/5)=2 题
    body = GOOD.replace("## 自测\n\n1. A 和 B 的区别？（知识点 1/2）\n", "## 自测\n\n（无）\n")
    r = report_check.check_report(body)
    assert r["ok"] is False
    assert any(c["name"] == "selftest" and c["level"] == "fail" for c in r["checks"])


def test_garble_and_placeholder_fail():
    r1 = report_check.check_report(GOOD.replace("A 的定义", "A 的定\ufffd义"))
    assert any(c["name"] == "garble" and c["level"] == "fail" for c in r1["checks"])
    r2 = report_check.check_report(GOOD.replace("A 的通俗解释", "A 的通俗解释 TBD"))
    assert any(c["name"] == "placeholder" and c["level"] == "fail" for c in r2["checks"])


def test_glossary_check_integration():
    """传入 content 时调用 glossary 校验：字幕出现但未提及的术语 → fail"""
    content = "这里讨论 A，也讨论 ZZZ。"
    r = report_check.check_report(GOOD, content_text=content,
                                 glossary={"A": "术语 A", "ZZZ": "未覆盖术语"})
    assert r["ok"] is False
    g = [c for c in r["checks"] if c["name"] == "glossary"][0]
    assert "ZZZ" in g["detail"]


def test_cli_exit_code(tmp_path, capsys):
    p = tmp_path / "r.md"
    p.write_text(GOOD, encoding="utf-8")
    assert report_check.main([str(p)]) == 0
    p.write_text(GOOD.replace("## 自测", "## 自测（缺失）").replace("## 自测（缺失）\n\n1. A 和 B 的区别？（知识点 1/2）", ""), encoding="utf-8")
    assert report_check.main([str(p)]) == 1


def test_citations_trace_numbers():
    content = "波长 13.5 nm，营收 816.15 亿美元，毛利率 74.9%。"
    good = GOOD.replace("A 的定义。", "A 的定义：波长 13.5 nm，营收 816.15 亿美元。")
    r = report_check.check_report(good, content_text=content)
    assert not any(c["name"] == "citations" and c["level"] == "fail" for c in r["checks"])

    bad = GOOD.replace("A 的定义。", "A 的定义：毛利率 99.9%。")
    r2 = report_check.check_report(bad, content_text=content)
    assert any(c["name"] == "citations" and c["level"] == "warn" for c in r2["checks"])
    r3 = report_check.check_report(bad, content_text=content, strict_citations=True)
    assert any(c["name"] == "citations" and c["level"] == "fail" for c in r3["checks"])


def test_quotes_trace():
    content = "视频里说：“Raw data are not automatically features.”"
    good = GOOD.replace("A 的定义。", "A 的定义：“Raw data are not automatically features.”")
    r = report_check.check_report(good, content_text=content)
    assert not any(c["name"] == "quotes" and c["level"] == "fail" for c in r["checks"])
    bad = GOOD.replace("A 的定义。", "A 的定义：“这是视频里没有的一句话。”")
    r2 = report_check.check_report(bad, content_text=content)
    assert any(c["name"] == "quotes" and c["level"] == "warn" for c in r2["checks"])
    r3 = report_check.check_report(bad, content_text=content, strict_citations=True)
    assert any(c["name"] == "quotes" and c["level"] == "fail" for c in r3["checks"])


def test_timestamps_range():
    content = "[0.0-3.0] a\n[3.0-60.0] b\n"
    good = GOOD.replace("#### 知识点 1：概念 A（[0:00–2:00]）",
                        "#### 知识点 1：概念 A（[0:30]）")
    r = report_check.check_report(good, content_text=content)
    assert not any(c["name"] == "timestamps" and c["level"] == "fail" for c in r["checks"])
    bad = GOOD.replace("#### 知识点 1：概念 A（[0:00–2:00]）",
                       "#### 知识点 1：概念 A（[9:00]）")
    r2 = report_check.check_report(bad, content_text=content, strict_citations=True)
    assert any(c["name"] == "timestamps" and c["level"] == "fail" for c in r2["checks"])


def test_version_matches_pyproject():
    import version
    import os
    root = os.path.join(os.path.dirname(__file__), "..")
    text = open(os.path.join(root, "pyproject.toml"), encoding="utf-8").read()
    assert f'version = "{version.VERSION}"' in text


def test_quotes_allow_ellipsis_but_check_segments():
    content = "第一句话在这里。中间还有别的。第二句话在这里。"
    good = GOOD.replace("A 的定义。", "A 的定义：“第一句话在这里……第二句话在这里。”")
    r = report_check.check_report(good, content_text=content)
    assert not any(c["name"] == "quotes" and c["level"] == "fail" for c in r["checks"])
    bad = GOOD.replace("A 的定义。", "A 的定义：“第一句话在这里……不存在的一句话。”")
    r2 = report_check.check_report(bad, content_text=content, strict_citations=True)
    assert any(c["name"] == "quotes" and c["level"] == "fail" for c in r2["checks"])
