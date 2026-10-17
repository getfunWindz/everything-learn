"""转写质量评估（低配检测 → 强制推荐，2026-10-17）

- 输入：transcriber 采集的置信度 stats（avg_logprob / no_speech_ratio / language_probability /
  segments）+ 使用档位 + 时长/文本
- 输出：{"level": "ok|weak", "reasons": [...]}；stats 缺失 → None（不阻塞）
- needs_upgrade：低档（< medium）且质量弱 → 强制推荐补依赖 + 升 medium（实机验证最佳）
- 阈值初值见 THRESHOLDS，由低配实测（docs/plans/2026-10-17-lowdep-test-record.md）标定
"""
TIER_ORDER = ["tiny", "base", "small", "medium", "large-v3"]

THRESHOLDS = {
    "avg_logprob": -0.8,          # 段级平均对数似然过低 → 转写不可靠
    "language_probability": 0.6,  # 语言判定概率过低 → 可能识别错语言
    "chars_per_min": 40,          # 转写密度异常低（中文正常语音约 150-250 字/分）
}


def tier_index(model: str) -> int:
    """档位序号（tiny<...<large-v3）；未知 → -1"""
    try:
        return TIER_ORDER.index((model or "").strip().lower())
    except ValueError:
        return -1


def assess(stats, model_used: str = "", duration_sec: float = 0, lines=None):
    """质量评估；stats 缺失/为空 → None"""
    if not stats:
        return None
    reasons = []
    lp = stats.get("avg_logprob")
    if isinstance(lp, (int, float)) and lp < THRESHOLDS["avg_logprob"]:
        reasons.append(f"转写置信度偏低（avg_logprob={lp:.2f} < {THRESHOLDS['avg_logprob']}）")
    lprob = stats.get("language_probability")
    if isinstance(lprob, (int, float)) and 0 < lprob < THRESHOLDS["language_probability"]:
        reasons.append(f"语言判定不稳定（p={lprob:.2f} < {THRESHOLDS['language_probability']}）")
    dur_min = float(duration_sec or 0) / 60.0
    if dur_min >= 1:
        chars = sum(len((l or {}).get("text") or "") for l in (lines or []))
        density = chars / dur_min
        if density < THRESHOLDS["chars_per_min"]:
            reasons.append(f"转写密度异常（{density:.0f} 字/分 < {THRESHOLDS['chars_per_min']}）")
    return {"level": "weak" if reasons else "ok", "reasons": reasons}


def needs_upgrade(assessment, model_used: str) -> bool:
    """低档（< medium）且质量弱 → 需强制推荐；档位未知不误报"""
    if not assessment or assessment.get("level") != "weak":
        return False
    idx = tier_index(model_used)
    return 0 <= idx < tier_index("medium")


def missing_dependencies() -> list:
    """缺失的可选依赖（委托 doctor.missing_optional，保持单一实现；异常 → []）"""
    try:
        from doctor import missing_optional
        return missing_optional()
    except Exception:
        return []


# ---------------- 转写幻觉拦截（2026-10-17 实测） ----------------
# 无语音/短循环音频常被 Whisper 写成片尾套话（如 "Thanks for watching..."），
# 与真实口播无法区分；命中即丢弃并如实标注，避免进入报告与知识库。
_TEMPLATE_PHRASES = (
    "thanks for watching", "like and subscribe", "please subscribe",
    "subscribe to the channel", "see you next time",
    "请点赞", "点赞订阅", "订阅频道", "下期再见",
)


def is_suspected_hallucination(lines, stats=None) -> bool:
    """转写是否为疑似幻觉：无语音占比高 / 同一句高频重复 / 全是模板句"""
    import re
    from collections import Counter
    texts = [(l or {}).get("text") or "" for l in (lines or [])]
    texts = [t.strip() for t in texts if t and t.strip()]
    if not texts:
        return False
    nsp = (stats or {}).get("no_speech_ratio")
    if isinstance(nsp, (int, float)) and nsp > 0.6:
        return True
    norm = [re.sub(r"\W+", "", t.lower()) for t in texts]
    norm = [t for t in norm if t]
    if len(norm) >= 3:
        top_n = Counter(norm).most_common(1)[0][1]
        if top_n >= 3 and top_n / len(norm) >= 0.6:
            return True
    joined = " ".join(texts).lower()
    tpl_chars = sum(len(t) for t in texts
                    if any(p in t.lower() for p in _TEMPLATE_PHRASES))
    return tpl_chars / max(1, len(joined)) > 0.5
