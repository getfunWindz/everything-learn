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


def _cfg():
    return {"recheck": {"tier_policy": "auto", "tier2_score": 0.55,
                        "tier3": {"allow": False, "max_density": 1.5, "max_gap_sec": 15},
                        "gap_sec": 20, "region_window_sec": 60, "region_top_n": 5}}


def test_decide_tier_short_dense_is_tier2():
    from core import recheck
    sig = {"dur_min": 10, "kp_density": 4.0, "kp_spread": 0.5, "src_risk": 1.0,
           "vis_ref": 2.0, "gap_ratio": 0.05, "max_gap": 8.0}
    d = recheck.decide_tier(sig, _cfg(), provenance="auto", has_video=True)
    assert d["tier"] == 2


def test_decide_tier_long_sparse_is_tier1():
    from core import recheck
    sig = {"dur_min": 180, "kp_density": 0.5, "kp_spread": 0.2, "src_risk": 0.3,
           "vis_ref": 0.0, "gap_ratio": 0.03, "max_gap": 6.0}
    d = recheck.decide_tier(sig, _cfg(), provenance="human", has_video=True)
    assert d["tier"] == 1


def test_decide_tier3_whitelist_requires_all_conditions():
    from core import recheck
    base = {"dur_min": 30, "kp_density": 1.0, "kp_spread": 0.3, "src_risk": 0.3,
            "vis_ref": 0.0, "gap_ratio": 0.02, "max_gap": 5.0}
    cfg = _cfg()
    assert recheck.decide_tier(base, cfg, provenance="human", has_video=True)["tier"] != 3
    cfg["recheck"]["tier3"]["allow"] = True
    assert recheck.decide_tier(base, cfg, provenance="human", has_video=True)["tier"] == 3
    assert recheck.decide_tier(base, cfg, provenance="auto", has_video=True)["tier"] != 3
    hot = dict(base, kp_density=2.0)
    assert recheck.decide_tier(hot, cfg, provenance="human", has_video=True)["tier"] != 3
    vis = dict(base, vis_ref=3.0)
    assert recheck.decide_tier(vis, cfg, provenance="human", has_video=True)["tier"] != 3
    gap = dict(base, max_gap=30.0)
    assert recheck.decide_tier(gap, cfg, provenance="human", has_video=True)["tier"] != 3


def test_decide_tier_policy_overrides():
    from core import recheck
    sig = {"dur_min": 10, "kp_density": 4.0, "kp_spread": 0.5, "src_risk": 1.0,
           "vis_ref": 2.0, "gap_ratio": 0.05, "max_gap": 8.0}
    assert recheck.decide_tier(sig, _cfg(), provenance="auto", has_video=True, policy="off")["tier"] == 3
    assert recheck.decide_tier(sig, _cfg(), provenance="auto", has_video=True, policy="tier1")["tier"] == 1
    assert recheck.decide_tier(sig, _cfg(), provenance="auto", has_video=True, policy="tier2")["tier"] == 2


def test_decide_tier_audio_only_is_na():
    from core import recheck
    sig = {"dur_min": 60, "kp_density": 2.0, "kp_spread": 0.5, "src_risk": 0.8,
           "vis_ref": 0.0, "gap_ratio": 0.0, "max_gap": 0.0}
    assert recheck.decide_tier(sig, _cfg(), provenance="unknown", has_video=False)["tier"] == "n/a"


def test_kp_spread_uniform_vs_concentrated():
    from core import recheck
    hot_text = "定义 公式 步骤 定理 结论 定义 公式 步骤"
    uniform = [{"start": i * 30, "end": i * 30 + 25, "text": hot_text} for i in range(20)]
    concentrated = [{"start": 0, "end": 25, "text": hot_text}] + \
                   [{"start": 30 + i * 30, "end": 55 + i * 30, "text": "嗯 然后 这个 就是"} for i in range(19)]
    s_u = recheck.kp_spread(uniform, 600)
    s_c = recheck.kp_spread(concentrated, 600)
    assert s_u > s_c


def test_gap_stats():
    from core import recheck
    lines = [{"start": 0, "end": 50, "text": "a"}]
    ratio, longest = recheck.gap_stats(lines, 100)
    assert longest == 50.0
    assert abs(ratio - 0.5) < 1e-6
