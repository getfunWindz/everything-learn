# -*- coding: utf-8 -*-
"""A0: 名词注释机制测试——术语库读写/自动沉淀/注释与覆盖校验"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from glossary import load_glossary, save_glossary, merge_terms, check_coverage, check_annotation

SEED = {
    "KV Cache": "键值缓存：推理时缓存注意力 K/V 矩阵、避免重复计算的技术",
    "MHA": "多头注意力：多组独立注意力并行后拼接融合",
    "softmax": "将得分归一化为概率分布的激活函数",
}


def _tmp_glossary_path():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    return path


def test_load_glossary_empty_when_missing():
    assert load_glossary("/nonexistent/glossary.json") == {}


def test_save_load_roundtrip():
    p = _tmp_glossary_path()
    try:
        save_glossary(SEED, p)
        assert load_glossary(p) == SEED
    finally:
        os.remove(p)


def test_merge_terms_appends_new_keeps_existing():
    new = {"KV Cache": "被覆盖的解释不应生效", "GQA": "分组查询注意力：头分组共享 KV"}
    merged = merge_terms(SEED, new)
    assert merged["KV Cache"] == SEED["KV Cache"], "已有术语解释不得被覆盖"
    assert merged["GQA"] == "分组查询注意力：头分组共享 KV"


def test_check_coverage_finds_missing_terms():
    report = "本报告讲解了 KV Cache 的原理与 softmax 的应用。"
    subtitle = "KV Cache 是核心概念，MHA 也很重要，softmax 用于归一化。"
    missing = check_coverage(report, subtitle, SEED)
    assert "MHA" in missing, "字幕出现但报告完全未提及的术语应被报告"


def test_check_annotation_finds_unannotated_terms():
    # 报告正文提及了 MHA 但知识点末尾没有它的注释 → 注释缺失
    report = "MHA 是重要的注意力机制。\n\n**名词注释**：\n- KV Cache：键值缓存\n- softmax：归一化函数"
    subtitle = "MHA 与 KV Cache 都是推理优化的关键。"
    unannotated = check_annotation(report, subtitle, SEED)
    assert "MHA" in unannotated, "正文提及但注释小节缺失的术语应被报告"
    assert "KV Cache" not in unannotated, "已注释术语不应误报"


def test_check_annotation_clean_report():
    report = "KV Cache 是核心。\n\n**名词注释**：\n- KV Cache：键值缓存"
    subtitle = "KV Cache 是核心。"
    assert check_annotation(report, subtitle, SEED) == []


def test_clean_lines_replaces_and_merges_counts():
    """批量筛验：逐条别名替换 + counts 汇总 + uncertain 合并（不改原对象）"""
    import glossary
    g = dict(SEED)
    g["_aliases"] = {"KV Catch": {"correct": "KV Cache", "type": "stt_error", "note": ""},
                     "MHA": {"correct": "多头注意力", "type": "naming", "note": ""},
                     "存疑词": {"correct": None, "type": "uncertain", "note": "待确认"}}
    lines = [{"start": 0.0, "end": 1.0, "text": "KV Catch 很快"},
             {"start": 1.0, "end": 2.0, "text": "KV Catch 与 MHA"},
             {"start": 2.0, "end": 3.0, "text": "存疑词与存疑词"}]
    new_lines, counts, uncertain = glossary.clean_lines(lines, g)
    assert new_lines[0]["text"] == "KV Cache 很快"
    assert new_lines[1]["text"] == "KV Cache 与 多头注意力"
    assert counts == {"KV Catch": 2, "MHA": 1}
    assert uncertain == [{"raw": "存疑词", "count": 2, "note": "待确认"}]
    assert new_lines[0]["start"] == 0.0 and lines[0]["text"] == "KV Catch 很快"


def test_absorb_corrections_only_when_term_matched(monkeypatch):
    """自动反哺：仅当 原词/新词 命中术语库（术语名或别名目标）时登记"""
    import glossary
    saved = {}
    monkeypatch.setattr(glossary, "save_glossary", lambda g, path=None: saved.update(g))
    pairs = [("KV Catch", "KV Cache"), ("随便", "随便二"), ("MHA", "多头注意力"),
             ("", "空"), ("same", "Same")]
    added = glossary.absorb_corrections(pairs, dict(SEED))
    assert added == {"KV Catch": "KV Cache", "MHA": "多头注意力"}
    aliases = saved.get("_aliases") or {}
    assert aliases["KV Catch"]["correct"] == "KV Cache"
    assert "随便" not in aliases


def test_absorb_corrections_keeps_existing_alias(monkeypatch):
    """已有别名不覆盖（重复反哺幂等）"""
    import glossary
    monkeypatch.setattr(glossary, "save_glossary", lambda g, path=None: None)
    g = dict(SEED)
    g["_aliases"] = {"KV Catch": {"correct": "KV Cache", "type": "stt_error"}}
    assert glossary.absorb_corrections([("KV Catch", "KV Cache")], g) == {}


def test_term_match_latin_boundaries():
    """英文术语按词边界匹配：pi 不得命中 pipeline，KDA 不得命中 backdata"""
    from glossary import _term_in_subtitle
    assert not _term_in_subtitle("pi", "the machine-learning pipeline is long")
    assert not _term_in_subtitle("KDA", "backdata is not a term")
    assert _term_in_subtitle("pi", "策略记为 pi(a|s)")
    assert _term_in_subtitle("embedding", "word embeddings are dense")
    assert _term_in_subtitle("softmax", "SOFTMAX 输出概率")
    assert _term_in_subtitle("超参数", "我们选择超参数")           # 中文仍按子串


def test_check_coverage_ignores_latin_substrings():
    """覆盖校验同一规则：正文只有 pipeline 时，不应把 pi 视为已提及"""
    seed = {"pi": "策略符号", "KDA": "线性注意力"}
    report = "这里只讨论 pipeline 与 backdata。"
    subtitle = "策略记为 pi，KDA 是线性注意力。"
    assert check_coverage(report, subtitle, seed) == ["pi", "KDA"]


def test_annotation_sections_accept_bullet_marker():
    """名词注释允许列表写法（- **名词注释**：），不应因前缀漏检"""
    report = "- 正文提到 pi。\n- **名词注释**：\n  - **pi**：策略符号 π(a|s)。\n"
    subtitle = "策略记为 pi。"
    assert check_annotation(report, subtitle, {"pi": "策略符号"}) == []
