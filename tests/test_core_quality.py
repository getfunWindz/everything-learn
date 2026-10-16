"""转写质量评估测试（低配检测 → 强制推荐，2026-10-17）"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from core import quality


def _lines(chars=1200, dur=600):
    return [{"start": 0.0, "end": float(dur), "text": "字" * chars}]


def test_assess_weak_on_low_logprob():
    q = quality.assess({"avg_logprob": -1.2, "language_probability": 0.9, "segments": 10},
                       model_used="small", duration_sec=600, lines=_lines())
    assert q["level"] == "weak"
    assert any("置信度" in r or "logprob" in r.lower() for r in q["reasons"])


def test_assess_weak_on_low_language_probability():
    q = quality.assess({"avg_logprob": -0.3, "language_probability": 0.3, "segments": 10},
                       model_used="small", duration_sec=600, lines=_lines())
    assert q["level"] == "weak"


def test_assess_weak_on_low_density():
    q = quality.assess({"avg_logprob": -0.3, "language_probability": 0.9, "segments": 5},
                       model_used="small", duration_sec=600, lines=_lines(chars=50))
    assert q["level"] == "weak"
    assert any("密度" in r for r in q["reasons"])


def test_assess_ok():
    q = quality.assess({"avg_logprob": -0.4, "language_probability": 0.95, "segments": 50},
                       model_used="small", duration_sec=600, lines=_lines())
    assert q["level"] == "ok" and q["reasons"] == []


def test_assess_none_without_stats():
    assert quality.assess(None, model_used="small", duration_sec=10, lines=[]) is None


def test_needs_upgrade_only_low_tier_and_weak():
    weak = {"level": "weak", "reasons": ["x"]}
    ok = {"level": "ok", "reasons": []}
    assert quality.needs_upgrade(weak, "small") is True
    assert quality.needs_upgrade(weak, "tiny") is True
    assert quality.needs_upgrade(ok, "small") is False
    assert quality.needs_upgrade(weak, "medium") is False      # 已达推荐档
    assert quality.needs_upgrade(weak, "") is False            # 档位未知 → 不误报


def test_tier_ordering():
    assert quality.tier_index("tiny") < quality.tier_index("small")
    assert quality.tier_index("small") < quality.tier_index("medium")
    assert quality.tier_index("medium") < quality.tier_index("large-v3")
