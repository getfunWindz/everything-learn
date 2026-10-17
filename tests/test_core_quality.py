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


def test_is_suspected_hallucination_looped_outro():
    """实测：无语音视频被 Whisper 写成「Thanks for watching...」两遍"""
    from core import quality
    lines = [{"start": 0, "end": 2,
              "text": "Thanks for watching! If you enjoyed this video, please like and subscribe!"}] * 2
    assert quality.is_suspected_hallucination(lines, {"no_speech_ratio": 0.9})


def test_is_suspected_hallucination_template_only():
    from core import quality
    lines = [{"start": 0, "end": 2, "text": "Thanks for watching! Please like and subscribe!"}]
    assert quality.is_suspected_hallucination(lines, {})


def test_is_suspected_hallucination_normal_content():
    from core import quality
    lines = [{"start": 0, "end": 2, "text": "今天讲 DeepSeek V4.1 的成本结构"},
             {"start": 2, "end": 4, "text": "单任务大约 0.2 美元"}]
    assert not quality.is_suspected_hallucination(lines, {"no_speech_ratio": 0.1})


import pytest


@pytest.mark.parametrize("platform,text_len,images,video,hint,expect", [
    # 完整内容：5 平台均不应误报
    ("zhihu", 5000, 0, 0, "", ""),
    ("wechat", 1200, 2, 0, "", ""),
    ("medium", 3000, 1, 0, "", ""),
    ("weibo", 800, 1, 0, "", ""),
    ("xiaohongshu", 600, 3, 0, "", ""),
    # 图片帖 / 极短：提示（微博 29 字实测）
    ("weibo", 29, 2, 0, "", "过短"),
    ("xiaohongshu", 45, 4, 0, "", "过短"),
    # 截断（Medium 会员文实测）
    ("medium", 900, 0, 0, "会员文章或正文被截断", "会员"),
    ("wechat", 1500, 0, 0, "", "截断"),
    # 以图片为主
    ("weibo", 200, 3, 0, "", "图片为主"),
    ("xiaohongshu", 250, 5, 0, "", "图片为主"),
    # 有口播的视频帖不报“正文过短”
    ("x_twitter", 10, 0, 2, "", ""),
])
def test_document_quality_matrix(platform, text_len, images, video, hint, expect):
    from core import quality
    text = "正" * text_len
    if expect == "截断":
        text = text[:-1] + "…"
    got = quality.document_quality_hint(text_len, images=images, video_lines=video,
                                        adapter_hint=hint, text=text)
    if expect:
        assert expect in got
    else:
        assert got == ""
