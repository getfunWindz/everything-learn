# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test_kp_density_high_vs_low():
    from core import recheck
    dense = [{"start": 0, "end": 55, "text": "定义 首先 公式 ∫ 3次 步骤 定理 结论 定义 公式 步骤"},
             {"start": 60, "end": 115, "text": "定义 因此 注意 公式 参数 1GB 倍"}]
    low = [{"start": 0, "end": 110, "text": "今天我们来聊聊这个事情的背景和来龙去脉啊"}]
    d1, det1 = recheck.kp_density(dense, 120)
    d2, _ = recheck.kp_density(low, 120)
    assert d1 > d2
    assert d2 == 0.0
    assert det1["blocks"] >= 2


def test_kp_density_glossary_terms_boost():
    from core import recheck
    lines = [{"start": 0, "end": 50, "text": "注意力机制 和 扩散模型 是重点"}]
    d_plain, _ = recheck.kp_density(lines, 60)
    d_gloss, _ = recheck.kp_density(lines, 60, glossary_terms=["注意力机制", "扩散模型"])
    assert d_gloss > d_plain


def test_kp_density_empty_lines_zero_duration():
    from core import recheck
    d, det = recheck.kp_density([], 0)
    assert d == 0.0
    assert det["windows"] == []
    assert recheck._windows([], 0) == []


def test_kp_density_duration_fallback_matches_lines_end():
    from core import recheck
    lines = [{"start": 0, "end": 120, "text": "定义 公式 步骤 定理"}]
    d0, det0 = recheck.kp_density(lines, 0)
    dn, _ = recheck.kp_density(lines, None)
    dref, _ = recheck.kp_density(lines, 120)
    assert d0 == dn == dref  # duration=0/None 回退到末条字幕 end
    assert d0 < 100.0  # 旧实现分母 clamp 1e-9 → ~6e9
    w0 = det0["windows"][0]
    assert w0["score"] == round(w0["score_raw"], 2)


def test_windows_last_window_truncated():
    from core import recheck
    w = recheck._windows([], 125, 60)
    assert len(w) == 3
    assert (w[-1][0], w[-1][1]) == (120.0, 125.0)


def test_windows_nonpositive_win_sec_no_hang():
    from core import recheck
    lines = [{"start": 0, "end": 30, "text": "定义"}]
    w = recheck._windows(lines, 30, 0)
    assert len(w) == 30  # win_sec<=0 夹到 1.0，不悬挂
    assert all(e - s <= 1.0 for s, e, _ in w)


def test_ttr_chinese_bigram():
    from core import recheck
    _, det = recheck.score_window_text("一二三四一二三四")
    assert det["ttr"] == 0.571  # 2-gram 4/7；字符级会得 4/8=0.5


def test_ttr_english_words_lowercased():
    from core import recheck
    _, det = recheck.score_window_text("Hello hello world")
    assert det["ttr"] == 0.667  # 词级 2/3；字符级 8/15≈0.533


def test_numunit_spec_regex():
    from core import recheck
    _, det = recheck.score_window_text("50% 3次 2倍 5ms 1GB 2万 3亿 4MB 6秒")
    assert det["numunit"] == 7  # MB/秒 不在 spec 正则内
