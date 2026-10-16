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


# ---------------- M13 评分校准：教学信号 / 中文数字单位 / 档1 保底采样 ----------------

def test_teaching_markers_boost_score_and_breakdown_count():
    from core import recheck
    teach = "性质 大小 方向 变化 比如 单位 计算"
    chat = "嗯 然后 这个 就是 那个 随便 说说"
    sc_t, det_t = recheck.score_window_text(teach)
    sc_c, _ = recheck.score_window_text(chat)
    assert sc_t > sc_c
    assert det_t["teach"] == 7                 # 按出现次数计权（不去重）
    assert recheck.score_window_text("性质性质性质")[1]["teach"] == 3


def test_teaching_marker_weight_is_1_5():
    """教学词权重精确钉死 1.5：同为教学词文本，权重 1.0 时 score≈3.0417，粗断言会险过"""
    from core import recheck
    score, detail = recheck.score_window_text("性质 性质 性质")   # 3 次命中
    assert detail["teach"] == 3
    assert detail["markers"] == 0        # 防教学口径混入 discourse markers
    assert score >= 4.5                  # 3 × 1.5（另有 ttr 小量加成）
    # 精确口径：仅教学词计入，score = 3×1.5 + 0.5×ttr_n(ttr=1/3) = 4.541666…
    assert abs(score - (4.5 + 0.5 * ((1 / 3 - 0.3) / 0.4))) < 1e-9


def test_cn_numunit_counted_in_numunit_bucket():
    from core import recheck
    _, det = recheck.score_window_text("一百米 三秒 五千克 两公斤")
    assert det["numunit"] == 4
    assert recheck.score_window_text("今天天气不错")[1]["numunit"] == 0


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


def test_decide_tier_default_threshold_is_045_when_missing_in_cfg():
    """缺省 tier2_score=0.45：score 落在 0.45~0.55 之间必须判档2（缺省误为 0.55 则档1）"""
    from core import recheck
    # score = 0.6/6 + 0.6*0.2 + 0.5*0.8 − 0.5*(30/120) = 0.495 ∈ (0.45, 0.55)
    sig = {"dur_min": 30, "kp_density": 0.6, "kp_spread": 0.2, "src_risk": 0.8,
           "vis_ref": 0.0, "gap_ratio": 0.02, "max_gap": 5.0}
    d = recheck.decide_tier(sig, {"recheck": {}}, provenance="unknown", has_video=True)
    assert abs(d["score"] - 0.495) < 0.001
    assert 0.45 < d["score"] < 0.55     # 真正夹在两阈值之间，才可区分缺省 0.45 / 0.55
    assert d["tier"] == 2                # 缺省 0.45 → 档2；若缺省误为 0.55 → 档1


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
    rest = [{"start": 60 + i * 30, "end": 85 + i * 30, "text": "嗯 然后 这个"} for i in range(6)] + \
           [{"start": 265 + i * 30, "end": 290 + i * 30, "text": "嗯 然后 这个"} for i in range(11)]
    lines = dense + rest
    regions = recheck.select_regions(lines, 600, _cfg())
    assert regions, "应选出候选区域"
    assert any("知识点" in r["reason"] for r in regions)
    assert any("空档" in r["reason"] for r in regions)
    assert regions == sorted(regions, key=lambda r: -r["score"])


def test_plan_frames_cap():
    from core import recheck
    regions = [{"start": 0, "end": 10000, "reason": "x", "score": 1.0}]
    p = recheck.plan_frames(regions, fps=3, max_frames=20000)
    assert p["wanted"] == 30000 and p["frames"] == 20000 and p["capped"] is True


def test_plan_frames_float_precision_no_lost_frame():
    from core import recheck
    # 0.58*50 = 28.999999999999996：旧实现 int() 少算 1 帧
    p = recheck.plan_frames([{"start": 0.0, "end": 0.58, "reason": "x", "score": 1.0}], fps=50)
    assert p["wanted"] == 29 and p["frames"] == 29


def test_select_regions_disputed_boost():
    from core import recheck
    lines = [{"start": i * 30, "end": i * 30 + 25, "text": "普通内容"} for i in range(20)]
    regions = recheck.select_regions(lines, 600, _cfg(), disputed=[(120, 150)])
    assert any("分歧" in r["reason"] for r in regions)


def test_select_regions_gap_threshold_respects_gap_sec():
    from core import recheck
    # 仅 5s 停顿：不足以成为空档候选（gap_sec=20）
    lines = [{"start": i * 30, "end": i * 30 + 25, "text": "普通内容"} for i in range(20)]
    regions = recheck.select_regions(lines, 600, _cfg())
    assert not any("空档" in r["reason"] for r in regions)
    # 30s 真空档 (25,55)：必须出现
    lines2 = [{"start": 0, "end": 25, "text": "普通内容"},
              {"start": 55, "end": 80, "text": "普通内容"}]
    regions2 = recheck.select_regions(lines2, 600, _cfg())
    assert any("空档" in r["reason"] and r["start"] <= 30 <= r["end"] for r in regions2)


def test_select_regions_top_n_and_invalid():
    from core import recheck
    cfg = _cfg()
    cfg["recheck"]["region_top_n"] = 2
    lines = [{"start": i * 30, "end": i * 30 + 25, "text": "定义 公式 步骤 定理"} for i in range(20)]
    assert len(recheck.select_regions(lines, 600, cfg)) <= 2
    cfg2 = _cfg(); cfg2["recheck"]["region_top_n"] = 0
    assert recheck.select_regions(lines, 600, cfg2) == []


def test_select_regions_fallback_sampling_when_no_signal():
    """档1 保底采样：无密集/空档/分歧信号 → 至少给 1 个候选（最窗）"""
    from core import recheck
    lines = [{"start": i * 30, "end": i * 30 + 25, "text": "嗯 然后 这个 就是"} for i in range(20)]
    regions = recheck.select_regions(lines, 600, _cfg())
    assert len(regions) == 1
    assert regions[0]["reason"] == "保底采样（无显著信号）"
    assert regions[0]["score"] == 0.1
    assert (regions[0]["start"], regions[0]["end"]) == (0.0, 60.0)
    # duration=0 → 无窗口，不产生保底区域
    assert recheck.select_regions([], 0, _cfg()) == []


def test_select_regions_no_fallback_when_candidates_exist():
    from core import recheck
    dense = [{"start": 0, "end": 55, "text": "定义 公式 步骤 定理 结论 定义 公式 步骤"}]
    rest = [{"start": 60 + i * 30, "end": 85 + i * 30, "text": "嗯 然后 这个"} for i in range(10)]
    regions = recheck.select_regions(dense + rest, 600, _cfg())
    assert regions
    assert all(r["reason"] != "保底采样（无显著信号）" for r in regions)


def test_physics_lecture_density_regression_after_calibration():
    """物理课形态文本：旧口径（无教学词/中文数字单位计权）0 块 → 新口径密度显著抬升"""
    from core import recheck
    text = ("我们来看这个性质，性质说的是大小，大小有方向，方向就是概念，概念里面有规律。"
            "比如这个变化，变化要看关系，关系就是比较。比如说单位，单位等于物理量，"
            "物理量有数值，数值要计算啊。这里还有实验，实验有现象，现象有条件，"
            "条件的作用是说明过程。方法就是推导，推导能证明意义，意义有区别和联系，"
            "联系就是应用。再比如说方向，方向的变化就是规律，规律要比较，比较要计算。")
    lines = [{"start": 0, "end": 55, "text": text}]
    density, det = recheck.kp_density(lines, 60)
    assert len(text) >= 150
    bd = det["windows"][0]["detail"]
    assert bd["markers"] == 0 and bd["numunit"] == 0   # 旧口径无任何命中（仅 ttr 项 ≤0.5）
    assert bd["latin"] == 0 and bd["formula"] == 0 and bd["enum"] == 0
    assert bd["teach"] >= 30
    assert det["blocks"] >= 2 and density > 0.5


def test_estimate_budget_and_needs_gate():
    from core import recheck
    pricing = {"currency": "USD", "input_per_1m": 0.3, "output_per_1m": 1.2,
               "tokens_per_frame": 600}
    est = recheck.estimate_budget(frames=9000, windows=100, pricing=pricing)
    assert est["currency"] == "USD"
    assert est["low"] < est["amount"] < est["high"]
    assert est["input_tokens"] == 9000 * 600 + 100 * 600
    assert est["output_tokens"] == 100 * 800
    assert abs(est["high"] / est["amount"] - 1.3) < 1e-6
    assert abs(est["low"] / est["amount"] - 0.7) < 1e-6
    cfg = {"recheck": {"auto_approve_amount": 0.3, "confirm_over_sec": 3600}}
    assert recheck.needs_gate(est, cfg, duration_sec=600) is True     # 金额超阈值
    cheap = dict(est, amount=0.01, low=0.007, high=0.013)
    assert recheck.needs_gate(cheap, cfg, duration_sec=600) is False
    assert recheck.needs_gate(cheap, cfg, duration_sec=4000) is True  # 超长


def test_needs_gate_boundaries():
    from core import recheck
    cfg = {"recheck": {"auto_approve_amount": 0.3, "confirm_over_sec": 3600}}
    assert recheck.needs_gate({"amount": 0.3}, cfg, 600) is False        # 恰好等于阈值：不触发
    assert recheck.needs_gate({"amount": 0.3000001}, cfg, 600) is True
    assert recheck.needs_gate({"amount": 0.01}, cfg, 3600) is True       # 恰好 3600s：触发
    assert recheck.needs_gate({"amount": 0.01}, cfg, 3599) is False


def test_same_source_ratio_and_verdict():
    from core import recheck
    subs = [{"start": 0, "end": 10, "text": "今天讲注意力机制"},
            {"start": 100, "end": 110, "text": "接下来是扩散模型"}]
    clips_ok = [(0, 10, "今天讲注意力机制"), (100, 110, "接下来是扩散模型")]
    r = recheck.same_source_check(clips_ok, subs, {"recheck": {"same_source_threshold": 0.45}})
    assert r["passed"] is True and r["avg"] > 0.8
    assert r["checked"] is True and r["compared"] == 2 and r["empty"] == 0
    clips_bad = [(0, 10, "欢迎收看影视剧第九集"), (100, 110, "插播一段广告")]
    r2 = recheck.same_source_check(clips_bad, subs, {"recheck": {"same_source_threshold": 0.45}})
    assert r2["passed"] is False


def test_same_source_single_comparable_point_not_checked():
    """最少可比点保护：仅 1 个可比点 → checked=False（fail-safe）"""
    from core import recheck
    subs = [{"start": 0, "end": 10, "text": "今天讲注意力机制"}]
    r = recheck.same_source_check([(0, 10, "今天讲注意力机制")], subs, {"recheck": {}})
    assert r["checked"] is False and r["passed"] is False
    assert r["compared"] == 1 and r["reason_code"] == "insufficient"


def test_same_source_empty_text_points_skipped_as_empty_not_zero():
    """空文本点不计入比值、只计 empty（稀疏字幕不误杀）"""
    from core import recheck
    subs = [{"start": 0, "end": 10, "text": "今天讲注意力机制"},
            {"start": 100, "end": 110, "text": "接下来是扩散模型"}]
    clips = [(0, 10, ""), (100, 110, "接下来是扩散模型"), (300, 310, "")]
    r = recheck.same_source_check(clips, subs, {"recheck": {}})
    assert r["compared"] == 1 and r["empty"] == 2
    assert r["checked"] is False and r["reason_code"] == "insufficient"


def test_same_source_no_clips_is_not_checked():
    from core import recheck
    r = recheck.same_source_check([], [{"start": 0, "end": 1, "text": "x"}], {"recheck": {}})
    assert r["checked"] is False and r["passed"] is False
    assert r["compared"] == 0 and r["reason_code"] == "insufficient"


def test_norm_for_match_pure_punct_whitespace_empty():
    """纯标点/空白归一化为空串（falsy）→ 不作为可比点/分歧证据"""
    from core import recheck
    assert recheck._norm_for_match("，。！") == ""
    assert recheck._norm_for_match("   ") == ""
    assert recheck._norm_for_match("\t\n") == ""
    assert recheck._norm_for_match("") == ""
    assert recheck._norm_for_match(None) == ""
    assert recheck._norm_for_match(" Hello, 世界! ") == "hello 世界"


def test_same_source_punct_only_points_counted_empty():
    """空文本对齐：纯标点抽查点归入 empty，不计入 compared（旧行为误判 ratio=1.0）"""
    from core import recheck
    clips = [(0, 5, "，。！"), (30, 40, "今天讲注意力机制")]
    subs = [{"start": 0, "end": 5, "text": "。。。！"},
            {"start": 30, "end": 40, "text": "今天讲注意力机制"}]
    r = recheck.same_source_check(clips, subs, {"recheck": {}})
    assert r["compared"] == 1 and r["empty"] == 1
    assert r["ratios"] == [1.0]
    assert r["checked"] is False and r["reason_code"] == "insufficient"


def test_pick_spot_times_within_bounds():
    from core import recheck
    pts = recheck.pick_spot_times(600)
    assert len(pts) == 3
    for a, b in pts:
        assert 0 <= a < b <= 600


# ---------------- T14.5：双 ASR 交叉比对 ----------------

def test_merge_ranges_adjacent_and_overlapping():
    """相邻（相接）与重叠区间都合并，乱序输入先排序"""
    from core import recheck
    assert recheck._merge_ranges([[0, 5], [5, 10], [20, 25], [22, 30], [12, 13]]) == \
        [[0, 10], [12, 13], [20, 30]]


def test_merge_ranges_empty_and_none():
    from core import recheck
    assert recheck._merge_ranges([]) == []
    assert recheck._merge_ranges(None) == []
    assert recheck._merge_ranges([[9, 12], [0, 3]]) == [[0, 3], [9, 12]]


def test_cross_check_identical_texts_full_agreement():
    """双侧文本逐窗一致 → agree_pct=1.0，无分歧区间"""
    from core import recheck
    primary = [{"start": 0, "end": 5, "text": "今天讲注意力机制"},
               {"start": 20, "end": 25, "text": "接下来是扩散模型"}]
    cc = recheck.cross_check(primary, [dict(l) for l in primary], window_sec=15)
    assert cc == {"agree_pct": 1.0, "compared": 2, "disputed": []}


def test_cross_check_divergent_texts_zero_and_disputed_merged():
    """逐窗完全分歧 → agree_pct=0.0；相邻分歧窗合并为整段"""
    from core import recheck
    primary = [{"start": 0, "end": 5, "text": "今天讲注意力机制"},
               {"start": 20, "end": 25, "text": "接下来是扩散模型"}]
    secondary = [{"start": 0, "end": 5, "text": "欢迎收看影视剧"},
                 {"start": 20, "end": 25, "text": "插播一段广告"}]
    cc = recheck.cross_check(primary, secondary, window_sec=15)
    assert cc["agree_pct"] == 0.0 and cc["compared"] == 2
    assert cc["disputed"] == [[0.0, 25.0]]  # [0,15)+[15,25) 相邻 → 合并


def test_cross_check_mixed_agreement_pct_and_ranges():
    """有比较窗与空白窗交错：空窗不计入 compared，只按可比窗算比例"""
    from core import recheck
    primary = [{"start": 0, "end": 5, "text": "今天讲注意力机制"},
               {"start": 30, "end": 35, "text": "接下来是扩散模型"}]
    secondary = [{"start": 0, "end": 5, "text": "今天讲注意力机制"},
                 {"start": 30, "end": 35, "text": "插播一段广告"}]
    cc = recheck.cross_check(primary, secondary, window_sec=15)
    # 窗 [0,15) 一致；[15,30) 两侧皆空 → 不比较；[30,35) 分歧
    assert cc["agree_pct"] == 0.5 and cc["compared"] == 2
    assert cc["disputed"] == [[30.0, 35.0]]


def test_cross_check_no_comparable_windows_returns_none():
    """无两侧均非空的比较窗 → agree_pct=None（不是 0，避免误报全分歧）"""
    from core import recheck
    primary = [{"start": 0, "end": 5, "text": "今天讲注意力机制"}]
    cc = recheck.cross_check(primary, [], window_sec=15)
    assert cc == {"agree_pct": None, "compared": 0, "disputed": []}
    assert recheck.cross_check([], primary)["agree_pct"] is None


def test_cross_check_duration_from_primary_only():
    """总时长取主侧：副侧越界/超长不扩窗"""
    from core import recheck
    primary = [{"start": 0, "end": 4, "text": "abcd"}]
    secondary = [{"start": 5, "end": 100, "text": "wxyz"}]  # 超出主侧时长 → 不参与
    assert recheck.cross_check(primary, secondary, window_sec=15) == \
        {"agree_pct": None, "compared": 0, "disputed": []}


def test_cross_check_nonpositive_window_no_hang():
    """win_sec<=0 夹到 1.0：不悬挂；逐窗全部比较/分歧合并"""
    from core import recheck
    primary = [{"start": i, "end": i + 1, "text": "abcd"} for i in range(4)]
    secondary = [{"start": i, "end": i + 1, "text": "wxyz"} for i in range(4)]
    cc = recheck.cross_check(primary, secondary, window_sec=0)
    assert cc["compared"] == 4  # 仅主侧时长 4s 内的 4 个 1s 窗
    assert cc["agree_pct"] == 0.0
    assert cc["disputed"] == [[0.0, 4.0]]


def test_cross_check_default_window_sec_15():
    """无参默认窗 15s：行 start 0/12/20 → 两窗（10s 会切三窗、30s 并一窗）"""
    from core import recheck
    a, b = "x" * 13 + "y" * 7, "x" * 13 + "z" * 7
    primary = [{"start": 0, "end": 5, "text": a},
               {"start": 12, "end": 17, "text": "中段"},
               {"start": 20, "end": 25, "text": "尾段"}]
    secondary = [{"start": 0, "end": 5, "text": b},
                 {"start": 12, "end": 17, "text": "中段"},
                 {"start": 20, "end": 25, "text": "尾段"}]
    cc = recheck.cross_check(primary, secondary)  # 无参 → 窗 15 / 阈值 0.65
    assert cc == {"agree_pct": 1.0, "compared": 2, "disputed": []}


def test_cross_check_default_threshold_065_boundary_ge():
    """无参默认阈值 0.65：相似度恰 0.65 → agree（>= 边界）；恰低一档 → disputed"""
    from core import recheck
    a, b = "x" * 13 + "y" * 7, "x" * 13 + "z" * 7
    assert recheck._similarity(a, b) == 0.65  # 构造核验：26/40
    cc = recheck.cross_check([{"start": 0, "end": 5, "text": a}],
                             [{"start": 0, "end": 5, "text": b}])
    assert cc == {"agree_pct": 1.0, "compared": 1, "disputed": []}
    # 阈值 0.66 > 0.65：同一对文本转分歧（边界另一侧）
    cc2 = recheck.cross_check([{"start": 0, "end": 5, "text": a}],
                              [{"start": 0, "end": 5, "text": b}],
                              window_sec=15, threshold=0.66)
    assert cc2["agree_pct"] == 0.0 and cc2["disputed"] == [[0.0, 5.0]]
    # 恰低一档（258/398≈0.6482）：默认 0.65 下应判分歧
    c, d = "x" * 129 + "y" * 70, "x" * 129 + "z" * 70
    assert recheck._similarity(c, d) < 0.65
    cc3 = recheck.cross_check([{"start": 0, "end": 5, "text": c}],
                              [{"start": 0, "end": 5, "text": d}])
    assert cc3["agree_pct"] == 0.0 and cc3["disputed"] == [[0.0, 5.0]]


def test_cross_check_line_start_at_window_boundary_half_open():
    """窗为半开区间 [t, t+win)：start 恰 == 窗尾归属下一窗，不落入前一窗"""
    from core import recheck
    primary = [{"start": 0, "end": 5, "text": "aaaa"},
               {"start": 15, "end": 20, "text": "bbbb"}]
    secondary = [{"start": 15, "end": 20, "text": "bbbb"}]  # 恰落窗尾 15
    cc = recheck.cross_check(primary, secondary, window_sec=15)
    # 窗 [0,15) 副侧为空 → 跳过；窗 [15,20] 两侧均为 "bbbb" → agree
    assert cc == {"agree_pct": 1.0, "compared": 1, "disputed": []}
