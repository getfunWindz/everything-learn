# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test_kp_density_high_vs_low():
    from core import recheck
    dense = [{"start": 0, "end": 55, "text": "定义 首先 公式 ∫ 3次 步骤 定理 结论 定义 公式 步骤"},
             {"start": 60, "end": 115, "text": "定义 因此 注意 公式 参数 1GB 倍"}]
    low = [{"start": 0, "end": 110, "text": "今天我们来聊聊这个事情的背景和来龙去脉啊"}]
    d1, det1 = recheck.kp_density(dense, 120)
    d2, det2 = recheck.kp_density(low, 120)
    assert d1 > d2
    assert d2 == 0.0
    assert det1["blocks"] >= 2


def test_kp_density_glossary_terms_boost():
    from core import recheck
    lines = [{"start": 0, "end": 50, "text": "注意力机制 和 扩散模型 是重点"}]
    d_plain, _ = recheck.kp_density(lines, 60)
    d_gloss, _ = recheck.kp_density(lines, 60, glossary_terms=["注意力机制", "扩散模型"])
    assert d_gloss >= d_plain
