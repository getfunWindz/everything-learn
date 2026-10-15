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


def test_kp_spread_guard_invalid_segments():
    from core import recheck
    dense = [{"start": 0, "end": 50, "text": "定义 公式 步骤 定理 结论 定义 公式 步骤"}]
    assert recheck.kp_spread(dense, 600, segments=1) == 0.0
    assert recheck.kp_spread(dense, 600, segments=0) == 0.0
    assert recheck.kp_spread([], 0) == 0.0


def test_gap_stats():
    from core import recheck
    lines = [{"start": 0, "end": 50, "text": "a"}]
    ratio, longest = recheck.gap_stats(lines, 100)
    assert longest == 50.0
    assert abs(ratio - 0.5) < 1e-6


def test_vis_ref_density_no_overlap_double_count():
    from core import recheck
    lines = [{"start": 0, "end": 5, "text": "如图所示。这张表格里数据很多。看这里，演示一下。"}]
    assert recheck.vis_ref_density(lines, 600) == 4.0


def test_vis_ref_density_units_per_10min():
    from core import recheck
    lines = [{"start": 0, "end": 5, "text": "如图"}]
    assert recheck.vis_ref_density(lines, 600) == 1.0
    assert recheck.vis_ref_density(lines, 300) == 2.0


def test_gather_signals_contract_and_duration_fallback():
    from core import recheck
    lines = [{"start": 0, "end": 50, "text": "定义 公式 步骤"},
             {"start": 60, "end": 115, "text": "注意 结论"}]
    sig = recheck.gather_signals(lines, 0, provenance="human")   # duration=0 → 回退 115s
    assert abs(sig["dur_min"] - 115 / 60) < 1e-9
    for k in ("kp_density", "kp_spread", "src_risk", "vis_ref", "gap_ratio", "max_gap"):
        assert k in sig
    assert sig["src_risk"] == 0.3
    assert recheck.gather_signals(lines, 0, provenance="none")["src_risk"] == 1.0
    lines2 = [{"start": 0, "end": 5, "text": "如图"}]
    sig2 = recheck.gather_signals(lines2, 600, provenance="whisper")
    assert sig2["vis_ref"] == 1.0            # 1 个 / 10min
    assert sig2["src_risk"] == 1.0
    assert sig2["gap_ratio"] > 0.9           # 595s 空档 / 600s


def test_decide_tier_score_threshold_boundary():
    from core import recheck
    sig = {"dur_min": 30, "kp_density": 1.0, "kp_spread": 0.3, "src_risk": 0.8,
           "vis_ref": 0.0, "gap_ratio": 0.02, "max_gap": 5.0}
    d = recheck.decide_tier(sig, _cfg(), provenance="unknown", has_video=True)
    assert abs(d["score"] - 0.6217) < 0.001     # 1/6 + 0.18 + 0.4 − 0.125
    assert d["tier"] == 2
    cfg2 = _cfg(); cfg2["recheck"]["tier2_score"] = 0.7
    assert recheck.decide_tier(sig, cfg2, provenance="unknown", has_video=True)["tier"] == 1


def test_decide_tier_score_includes_vis_ref_term():
    from core import recheck
    sig = {"dur_min": 30, "kp_density": 1.0, "kp_spread": 0.3, "src_risk": 0.8,
           "vis_ref": 2.5, "gap_ratio": 0.02, "max_gap": 5.0}
    d = recheck.decide_tier(sig, _cfg(), provenance="unknown", has_video=True)
    # 0.6217 + 0.4*(2.5/5) = 0.8217
    assert abs(d["score"] - 0.8217) < 0.001
    assert d["tier"] == 2


def test_select_regions_prefers_dense_and_gaps():
    from core import recheck
    dense = [{"start": 0, "end": 55, "text": "定义 公式 步骤 定理 结论 定义 公式 步骤"}]
    rest = [{"start": 60 + i * 30, "end": 85 + i * 30, "text": "嗯 然后 这个"} for i in range(18)]
    lines = dense + rest
    regions = recheck.select_regions(lines, 600, _cfg())
    assert regions, "应选出候选区域"
    assert any("知识点" in r["reason"] for r in regions)
    assert any("空档" in r["reason"] for r in regions)
    assert regions == sorted(regions, key=lambda r: -r["score"])


def test_select_regions_disputed_boost():
    from core import recheck
    lines = [{"start": i * 30, "end": i * 30 + 25, "text": "普通内容"} for i in range(20)]
    regions = recheck.select_regions(lines, 600, _cfg(), disputed=[(120, 150)])
    assert any("分歧" in r["reason"] for r in regions)


def test_estimate_budget_and_needs_gate():
    from core import recheck
    pricing = {"currency": "USD", "input_per_1m": 0.3, "output_per_1m": 1.2,
               "tokens_per_frame": 600}
    est = recheck.estimate_budget(frames=9000, windows=100, pricing=pricing)
    assert est["currency"] == "USD"
    assert est["low"] < est["amount"] < est["high"]
    assert est["input_tokens"] == 9000 * 600 + 100 * 600
    cfg = {"recheck": {"auto_approve_amount": 0.3, "confirm_over_sec": 3600}}
    assert recheck.needs_gate(est, cfg, duration_sec=600) is True     # 金额超阈值
    cheap = dict(est, amount=0.01, low=0.007, high=0.013)
    assert recheck.needs_gate(cheap, cfg, duration_sec=600) is False
    assert recheck.needs_gate(cheap, cfg, duration_sec=4000) is True  # 超长
