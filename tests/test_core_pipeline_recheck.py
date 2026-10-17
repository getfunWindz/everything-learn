# -*- coding: utf-8 -*-
"""M13：pipeline 政策栈与预算闸门集成测试（全部 mock，无网络无 vision）"""
import json
import os, sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from core import pipeline
from core.models import ContentResult, ItemMeta, ItemRef


def test_same_source_verify_orchestration(monkeypatch):
    """3 个抽查点均有可比文本（字幕覆盖 3 点）→ checked/passed"""
    import numpy as np
    import transcriber

    class Adapter:
        name = "demo"
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "http://m/audio"

    last = {}

    def fake_extract(path, s, e):
        last["start"] = s
        return (np.zeros(10, dtype="float32"), 16000)

    spot_texts = {6: "今天讲注意力机制", 54: "然后我们看扩散模型", 102: "最后总结一下"}
    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: dest)
    monkeypatch.setattr(transcriber, "extract_audio_clip", fake_extract)
    monkeypatch.setattr(transcriber, "transcribe_samples_info",
                        lambda samples, sr, model_size="small", language=None:
                        (spot_texts.get(int(last["start"]), ""), "zh"))
    cfg = {"recheck": {"same_source_threshold": 0.45, "same_source_model": "small"}}
    ref = ItemRef(platform="demo", item_id="1", duration=120)
    lines = [{"start": 0, "end": 10, "text": "今天讲注意力机制"},
             {"start": 54, "end": 66, "text": "然后我们看扩散模型"},
             {"start": 102, "end": 114, "text": "最后总结一下"}]
    r = pipeline._same_source_verify(Adapter(), ref, lines, cfg, subtitle_language="zh")
    assert r["checked"] is True and r["passed"] is True
    assert r["compared"] == 3 and r["empty"] == 0


def test_same_source_verify_insufficient_comparable_points(monkeypatch):
    """最少可比点保护：1 段字幕 + 3 个抽查点 → 可比点 <2 → checked=False"""
    import numpy as np
    import transcriber

    class Adapter:
        name = "demo"
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "http://m/audio"

    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: dest)
    monkeypatch.setattr(transcriber, "extract_audio_clip",
                        lambda path, s, e: (np.zeros(10, dtype="float32"), 16000))
    monkeypatch.setattr(transcriber, "transcribe_samples_info",
                        lambda samples, sr, model_size="small", language=None:
                        ("今天讲注意力机制", "zh"))
    cfg = {"recheck": {"same_source_threshold": 0.45}}
    ref = ItemRef(platform="demo", item_id="1", duration=120)
    lines = [{"start": 0, "end": 10, "text": "今天讲注意力机制"}]  # 仅覆盖第 1 个抽查点
    r = pipeline._same_source_verify(Adapter(), ref, lines, cfg)
    assert r["checked"] is False and r["passed"] is False
    assert r["compared"] == 1 and r["reason_code"] == "insufficient"


def test_same_source_verify_degrades_without_blocking(monkeypatch):
    """无可下载音频流 / 下载异常 → checked=False（降级不阻塞主流程）+ reason_code"""
    import transcriber

    class NoMediaAdapter:
        name = "demo"
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return None

    cfg = {"recheck": {}}
    ref = ItemRef(platform="demo", item_id="1", duration=120)
    r = pipeline._same_source_verify(NoMediaAdapter(), ref, [], cfg)
    assert r["checked"] is False and r["reason"]
    assert r["reason_code"] == "no_audio"

    class Adapter:
        name = "demo"
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "http://m/audio"

    def boom(url, dest, headers=None):
        raise RuntimeError("下载失败")

    monkeypatch.setattr(transcriber, "download_audio", boom)
    r2 = pipeline._same_source_verify(Adapter(), ref, [], cfg)
    assert r2["checked"] is False and "下载失败" in r2["reason"]
    assert r2["reason_code"] == "error"


def test_same_source_verify_fetch_media_url_exception_degrades(monkeypatch):
    """fetch_media_url 抛异常（如 bilibili 无音频流）→ checked=False + reason_code=error，不穿透"""
    class BoomAdapter:
        name = "bilibili"
        def fetch_media_url(self, ref, media="audio", lowest=False):
            raise RuntimeError("无音频流")

    cfg = {"recheck": {}}
    ref = ItemRef(platform="bilibili", item_id="1", duration=120)
    r = pipeline._same_source_verify(BoomAdapter(), ref, [], cfg)
    assert r["checked"] is False and r["reason_code"] == "error"
    assert "无音频流" in r["reason"]


def _patch_same_source_io(monkeypatch, asr_text="今天讲注意力机制", subtitle_text=None,
                          asr_lang="zh"):
    """mock 同源校验 IO（下载/切段/转写）；返回 (Adapter 实例, duration=0 的 ref, 字幕 lines)。
    lines 末条 end=170 → 时长回退后抽查点 (11,23)/(79,91)/(147,159)，三行字幕均可覆盖。"""
    import numpy as np
    import transcriber

    class Adapter:
        name = "demo"
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "http://m/audio"

    sub_text = asr_text if subtitle_text is None else subtitle_text
    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: dest)
    monkeypatch.setattr(transcriber, "extract_audio_clip",
                        lambda path, s, e: (np.zeros(10, dtype="float32"), 16000))
    monkeypatch.setattr(transcriber, "transcribe_samples_info",
                        lambda samples, sr, model_size="small", language=None:
                        (asr_text, asr_lang))
    ref = ItemRef(platform="demo", item_id="1", duration=0)
    lines = [{"start": 12, "end": 24, "text": sub_text},
             {"start": 80, "end": 92, "text": sub_text},
             {"start": 148, "end": 170, "text": sub_text}]
    return Adapter(), ref, lines


def test_same_source_verify_duration_falls_back_to_last_subtitle_end(monkeypatch):
    """时长缺失（TED/YouTube ref.duration=0）→ 用字幕末条 end=170 近似，抽查点恢复 3 个"""
    seen = []
    real_pick = pipeline.recheck.pick_spot_times
    monkeypatch.setattr(pipeline.recheck, "pick_spot_times",
                        lambda d, n=3: seen.append(float(d)) or real_pick(d, n))
    adapter, ref, lines = _patch_same_source_io(monkeypatch)
    cfg = {"recheck": {"same_source_threshold": 0.45}}
    r = pipeline._same_source_verify(adapter, ref, lines, cfg, subtitle_language="zh")
    assert seen == [170.0]                # 不再传 0（单点 → insufficient）
    assert r["compared"] == 3 and r["checked"] is True and r["passed"] is True


def test_same_source_verify_language_gate_mismatch(monkeypatch):
    """翻译轨：字幕 zh-Hans、音轨 ASR en → lang_mismatch（不判同源，交视觉归属兜底）"""
    adapter, ref, lines = _patch_same_source_io(
        monkeypatch, asr_text="attention mechanisms and diffusion models",
        subtitle_text="今天讲注意力和扩散模型", asr_lang="en")
    cfg = {"recheck": {"same_source_threshold": 0.45}}
    r = pipeline._same_source_verify(adapter, ref, lines, cfg, subtitle_language="zh-Hans")
    assert r["checked"] is False and r["passed"] is False
    assert r["reason_code"] == "lang_mismatch"
    assert r["subtitle_language"] == "zh-Hans" and r["asr_languages"] == ["en"]
    assert "疑似翻译轨" in r["reason"]


def test_same_source_verify_same_language_passes_with_duration_fallback(monkeypatch):
    """YouTube 形态回归：时长缺失回退后，同语言（en-US vs en）→ 语言门不误杀，compared=3"""
    adapter, ref, lines = _patch_same_source_io(
        monkeypatch, asr_text="keep your goals to yourself", asr_lang="en")
    cfg = {"recheck": {"same_source_threshold": 0.45}}
    r = pipeline._same_source_verify(adapter, ref, lines, cfg, subtitle_language="en-US")
    assert r["checked"] is True and r["passed"] is True
    assert r["compared"] == 3


def test_lang_base_takes_primary_subtag():
    """语言主代码归一：zh-Hans/zh-cn/zh_CN → zh；en-US → en；空 → ''"""
    assert pipeline._lang_base("zh-Hans") == "zh"
    assert pipeline._lang_base("zh-cn") == "zh"
    assert pipeline._lang_base("zh_CN") == "zh"
    assert pipeline._lang_base("en-US") == "en"
    assert pipeline._lang_base("") == "" and pipeline._lang_base(None) == ""


# ---------------- T12：来源策略接线（process_item） ----------------

class DemoAdapter:
    name = "demo"
    language_hint = "zh"

    def __init__(self, result, media="http://m/audio.m4s"):
        self.result, self.media = result, media

    def fetch_content(self, ref):
        return self.result

    def fetch_meta(self, ref):
        return ItemMeta(title="t", author="a", duration=120)

    def fetch_media_url(self, ref, media="audio", lowest=False):
        return self.media


def _run(monkeypatch, adapter, tmp_path, same_source_passed=True, whisper_lines=None,
         ss_override=None, attr_override=None, whisper_exc=None,
         no_whisper=False, no_vision_check=False, calls=None):
    """process_item 离线跑：同源校验/归属兜底/Whisper 全部 mock，不落盘。
    calls：可选 dict，记录 {"attribution": [...], "transcribe": [...]} 调用证据。"""
    from core import config as core_config
    cfg = dict(core_config.DEFAULT_CONFIG)
    cfg["out_dir"] = str(tmp_path)
    monkeypatch.setattr(pipeline.config, "load_config", lambda: cfg)
    ss = ss_override or {"checked": True, "passed": same_source_passed,
                         "avg": 0.9 if same_source_passed else 0.1}

    def _ss_spy(a, r, lines, c, subtitle_language=""):
        if calls is not None:
            calls.setdefault("subtitle_language", []).append(subtitle_language)
        return dict(ss)

    monkeypatch.setattr(pipeline, "_same_source_verify", _ss_spy)

    def _attr_spy(a, r, lines, c):
        if calls is not None:
            calls.setdefault("attribution", []).append(lines)
        return dict(attr_override or {"passed": False, "reason": "skipped"})

    monkeypatch.setattr(pipeline, "_attribution_fallback", _attr_spy)
    import transcriber

    def _transcribe(url, **kw):
        if calls is not None:
            calls.setdefault("transcribe", []).append(url)
        if whisper_exc is not None:
            raise whisper_exc
        return (whisper_lines if whisper_lines is not None
                else [{"start": 0, "end": 5, "text": "whisper 内容"}])

    monkeypatch.setattr(transcriber, "transcribe_url", _transcribe)
    monkeypatch.setattr(pipeline.artifacts, "write_item_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.artifacts, "build_info", lambda meta, ref, **k: {"kind": "timeline", **k})
    opts = pipeline.RunOptions(out_root=str(tmp_path), no_cache=True,
                               no_whisper=no_whisper, no_vision_check=no_vision_check)
    return pipeline.process_item(adapter, ItemRef(platform="demo", item_id="1", duration=120),
                                 str(tmp_path), opts, single=True)


def test_auto_provenance_goes_whisper(monkeypatch, tmp_path):
    """auto（AI/自动字幕）→ 不采信字幕主选，直接 Whisper（spec §5.2）"""
    res = ContentResult(kind="timeline", status="ok", provenance="auto",
                        segments=[{"start": 0, "end": 5, "text": "ai 字幕内容"}])
    status, src, n, _, err = _run(monkeypatch, DemoAdapter(res), tmp_path)
    assert (status, src, n) == ("ok", "whisper", 1)


def test_human_provenance_kept_when_same_source_passes(monkeypatch, tmp_path):
    """human 字幕过同源校验 → 采信字幕，src=label"""
    res = ContentResult(kind="timeline", status="ok", provenance="human", label="字幕(人工)",
                        segments=[{"start": 0, "end": 5, "text": "人工字幕内容"}])
    status, src, n, _, err = _run(monkeypatch, DemoAdapter(res), tmp_path, same_source_passed=True)
    assert (status, src, n) == ("ok", "字幕(人工)", 1)


def test_human_provenance_rejected_when_same_source_fails(monkeypatch, tmp_path):
    """human 字幕未过同源校验（串台）→ 转 Whisper"""
    res = ContentResult(kind="timeline", status="ok", provenance="human",
                        segments=[{"start": 0, "end": 5, "text": "串台字幕"}])
    status, src, n, _, err = _run(monkeypatch, DemoAdapter(res), tmp_path, same_source_passed=False)
    assert (status, src, n) == ("ok", "whisper", 1)


def test_no_audio_triggers_attribution_fallback(monkeypatch, tmp_path):
    """无音频流（no_audio）→ 视觉归属兜底通过 → 采信字幕（spec §8.2 降级路径）"""
    res = ContentResult(kind="timeline", status="ok", provenance="human",
                        segments=[{"start": 0, "end": 5, "text": "人工字幕内容"}])
    status, src, n, _, err = _run(
        monkeypatch, DemoAdapter(res), tmp_path,
        ss_override={"checked": False, "passed": False,
                     "reason_code": "no_audio", "reason": "无可下载音频流"},
        attr_override={"passed": True, "checked": True})
    assert (status, src, n) == ("ok", "字幕", 1)


# ---------------- M13 修复：翻译轨语言门 + 视觉归属降级 ----------------

_LANG_MISMATCH_SS = {"checked": False, "passed": False, "reason_code": "lang_mismatch",
                     "subtitle_language": "zh-Hans", "asr_languages": ["en"],
                     "reason": "字幕语言与音轨语言不同（疑似翻译轨），改走视觉归属复检"}


def _lang_mismatch_res():
    return ContentResult(kind="timeline", status="ok", provenance="official",
                         label="字幕(官方 zh-Hans)", language="zh-Hans",
                         segments=[{"start": 0, "end": 5, "text": "官方中文字幕"}])


def test_lang_mismatch_passes_subtitle_language_and_attribution_pass_keeps(monkeypatch, tmp_path):
    """翻译轨 + vision 开：字幕语言透传；视觉归属通过 → 采信字幕（不标注）"""
    res = _lang_mismatch_res()
    calls = {}
    status, src, n, _, err = _run(
        monkeypatch, DemoAdapter(res), tmp_path, calls=calls,
        ss_override=dict(_LANG_MISMATCH_SS),
        attr_override={"passed": True, "checked": True})
    assert (status, src, n) == ("ok", "字幕(官方 zh-Hans)", 1)
    assert calls.get("subtitle_language") == ["zh-Hans"]   # 语言门输入接线
    assert calls.get("attribution")                          # 语言门触发 → 视觉归属兜底
    assert calls.get("transcribe") in (None, [])            # 未走 Whisper


def test_lang_mismatch_attribution_verdict_fail_goes_whisper(monkeypatch, tmp_path):
    """翻译轨 + vision 开：视觉归属明确否决（checked=True/passed=False）→ 丢弃转 Whisper"""
    res = _lang_mismatch_res()
    calls = {}
    status, src, n, _, err = _run(
        monkeypatch, DemoAdapter(res), tmp_path, calls=calls,
        ss_override=dict(_LANG_MISMATCH_SS),
        attr_override={"passed": False, "checked": True,
                       "verdicts": [{"ts": 1, "match": False}]})
    assert (status, src, n) == ("ok", "whisper", 1)
    assert calls.get("attribution")
    assert calls.get("transcribe")


def test_lang_mismatch_without_vision_keeps_subtitle_annotated(monkeypatch, tmp_path):
    """翻译轨 + --no-vision-check：不强制丢弃 → 采信字幕并标注（人工翻译是合法内容）"""
    res = _lang_mismatch_res()
    calls = {}
    status, src, n, _, err = _run(
        monkeypatch, DemoAdapter(res), tmp_path, calls=calls, no_vision_check=True,
        ss_override=dict(_LANG_MISMATCH_SS))
    assert (status, src, n) == ("ok", "字幕(官方 zh-Hans)（语言不匹配，未同源校验）", 1)
    assert calls.get("attribution") in (None, [])           # 不触碰付费视觉兜底
    assert calls.get("transcribe") in (None, [])            # 不丢翻译轨走 Whisper


def test_lang_mismatch_attribution_unavailable_keeps_subtitle_annotated(monkeypatch, tmp_path):
    """翻译轨 + vision 已开但兜底无明确裁决（no_vision/invalid_input）→ 仍采信并标注"""
    res = _lang_mismatch_res()
    calls = {}
    status, src, n, _, err = _run(
        monkeypatch, DemoAdapter(res), tmp_path, calls=calls,
        ss_override=dict(_LANG_MISMATCH_SS),
        attr_override={"passed": False, "checked": False, "reason": "invalid_input"})
    assert (status, src, n) == ("ok", "字幕(官方 zh-Hans)（语言不匹配，未同源校验）", 1)
    assert calls.get("attribution") and calls.get("transcribe") in (None, [])


def test_whisper_failure_falls_back_to_subtitle(monkeypatch, tmp_path):
    """Whisper 抛异常且有字幕候选 → 回退候选并标注（spec §5.3）"""
    res = ContentResult(kind="timeline", status="ok", provenance="auto",
                        segments=[{"start": 0, "end": 5, "text": "AI 字幕"}])
    status, src, n, _, err = _run(monkeypatch, DemoAdapter(res), tmp_path,
                                  whisper_exc=RuntimeError("下载失败"))
    assert (status, src, n) == ("ok", "whisper失败→字幕（未经同源校验）", 1)


def test_whisper_empty_falls_back_to_subtitle(monkeypatch, tmp_path):
    """Whisper 返回空（未抛异常）且有候选 → 同样回退（spec §5.3“失败/为空”）"""
    res = ContentResult(kind="timeline", status="ok", provenance="auto",
                        segments=[{"start": 0, "end": 5, "text": "AI 字幕"}])
    status, src, n, _, err = _run(monkeypatch, DemoAdapter(res), tmp_path, whisper_lines=[])
    assert (status, src, n) == ("ok", "whisper失败→字幕（未经同源校验）", 1)


def test_whisper_failure_without_candidate_fails(monkeypatch, tmp_path):
    """Whisper 失败且无字幕候选 → failed（现有报错语义保持）"""
    res = ContentResult(kind="timeline", status="empty", provenance="none", segments=[])
    status, src, n, _, err = _run(monkeypatch, DemoAdapter(res), tmp_path,
                                  whisper_exc=RuntimeError("下载失败"))
    assert status == "failed" and n == 0


def test_whisper_missing_with_candidate_degrades_lowdep(monkeypatch, tmp_path):
    """低配策略（2026-10-17 取代 T12 裁决）：未安装 faster-whisper + 有字幕候选
    → 降级采信字幕（标注「低配」）而非失败（quality_hint 落盘由 test_core_pipeline 覆盖）"""
    import transcriber

    res = ContentResult(kind="timeline", status="ok", provenance="auto",
                        segments=[{"start": 0, "end": 5, "text": "ai 字幕"}])
    status, src, n, _, err = _run(monkeypatch, DemoAdapter(res), tmp_path,
                                  whisper_exc=transcriber.WhisperNotInstalled("未安装"))
    assert status == "ok" and n == 1
    assert "低配" in src and "未经同源校验" in src


def test_attribution_fallback_requires_vision_config():
    """归属兜底前置：未配置 vision → no_vision（不触碰画面流）"""
    class A:
        def fetch_media_url(self, *a, **k):
            raise AssertionError("vision 未配置时不应取画面流")

    r = pipeline._attribution_fallback(A(), None, [{"start": 0, "end": 1, "text": "x"}],
                                       {"vision": {"enabled": False}})
    assert r["passed"] is False and r["reason_code"] == "no_vision"


def test_attribution_fallback_no_video_stream():
    """归属兜底：最低画质画面流缺失 → no_video"""
    calls = []

    class A:
        def fetch_media_url(self, ref, media="audio", lowest=False):
            calls.append((media, lowest))
            return None

    cfg = {"vision": {"enabled": True, "api_key": "k"}}
    r = pipeline._attribution_fallback(A(), None, [{"start": 0, "end": 1, "text": "x"}], cfg)
    assert r["passed"] is False and r["reason_code"] == "no_video"
    assert calls == [("video", True)]


def test_attribution_fallback_delegates_to_vision(monkeypatch):
    """归属兜底：下载最低画质视频 → vision.check_attribution_aligned 裁决"""
    import transcriber
    import vision
    seen = {}

    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: seen.update(url=url) or dest)
    monkeypatch.setattr(vision, "check_attribution_aligned",
                        lambda conf, vp, lines, dur: {"passed": True, "checked": True, "verdicts": []})

    class A:
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "http://m/video-low"

    class R:
        duration = 120

    cfg = {"vision": {"enabled": True, "api_key": "k"}}
    r = pipeline._attribution_fallback(A(), R(), [{"start": 0, "end": 1, "text": "x"}], cfg)
    assert r["passed"] is True and seen["url"] == ["http://m/video-low"]   # 候选列表契约


def test_attribution_fallback_passes_effective_duration(monkeypatch):
    """duration=0（YouTube/TED 适配器未回填）→ 兜底把有效时长（末条字幕 end=19s）
    传给 vision 裁决，而不是 0（旧实现 invalid_input，视觉兜底实际未执行）"""
    import transcriber
    import vision
    seen = {}
    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: dest)
    monkeypatch.setattr(vision, "check_attribution_aligned",
                        lambda conf, vp, lines, dur:
                        seen.update(dur=dur) or {"passed": True, "checked": True})

    class A:
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "http://m/video-low"

    class R:
        duration = 0

    cfg = {"vision": {"enabled": True, "api_key": "k"}}
    r = pipeline._attribution_fallback(A(), R(), [{"start": 0, "end": 19, "text": "x"}], cfg)
    assert r["passed"] is True
    assert seen["dur"] == 19.0


# ---------------- T12 评审修复：--no-whisper / --no-vision-check / 兜底契约 ----------------

def test_no_whisper_keeps_unverified_candidate(monkeypatch, tmp_path):
    """--no-whisper：同源校验没跑成（checked=False）→ 绝不转写，采信候选并标注"""
    res = ContentResult(kind="timeline", status="ok", provenance="human",
                        segments=[{"start": 0, "end": 5, "text": "人工字幕内容"}])
    calls = {}
    status, src, n, _, err = _run(
        monkeypatch, DemoAdapter(res), tmp_path, no_whisper=True, calls=calls,
        ss_override={"checked": False, "passed": False,
                     "reason_code": "error", "reason": "转写异常"})
    assert (status, src, n) == ("ok", "字幕（--no-whisper，未经同源校验）", 1)
    assert "--no-whisper，未经同源校验" in src
    assert calls.get("transcribe") in (None, [])


def test_no_whisper_discards_verified_mismatch(monkeypatch, tmp_path):
    """--no-whisper：已判定不同源（checked=True, passed=False）→ 丢弃候选 → no_subtitle"""
    res = ContentResult(kind="timeline", status="ok", provenance="human",
                        segments=[{"start": 0, "end": 5, "text": "串台字幕"}])
    calls = {}
    status, src, n, _, err = _run(
        monkeypatch, DemoAdapter(res), tmp_path, no_whisper=True, calls=calls,
        ss_override={"checked": True, "passed": False, "avg": 0.1})
    assert status == "no_subtitle" and n == 0
    assert calls.get("transcribe") in (None, [])


def test_no_whisper_auto_candidate_keeps_with_label(monkeypatch, tmp_path):
    """--no-whisper：auto（AI/自动字幕）无同源校验可言 → 采信并标注 AI 来源"""
    res = ContentResult(kind="timeline", status="ok", provenance="auto", label="字幕(自动)",
                        segments=[{"start": 0, "end": 5, "text": "ai 字幕内容"}])
    calls = {}
    status, src, n, _, err = _run(monkeypatch, DemoAdapter(res), tmp_path,
                                  no_whisper=True, calls=calls)
    assert (status, src, n) == ("ok", "字幕(AI)（--no-whisper，未经同源校验）", 1)
    assert calls.get("transcribe") in (None, [])


def test_no_vision_check_disables_attribution_fallback(monkeypatch, tmp_path):
    """--no-vision-check：no_audio 时不调用付费视觉归属兜底，直接走 Whisper"""
    res = ContentResult(kind="timeline", status="ok", provenance="human",
                        segments=[{"start": 0, "end": 5, "text": "人工字幕内容"}])
    calls = {}
    status, src, n, _, err = _run(
        monkeypatch, DemoAdapter(res), tmp_path, no_vision_check=True, calls=calls,
        ss_override={"checked": False, "passed": False,
                     "reason_code": "no_audio", "reason": "无可下载音频流"})
    assert (status, src, n) == ("ok", "whisper", 1)
    assert calls.get("attribution") in (None, [])


def test_attribution_fallback_only_for_no_audio(monkeypatch, tmp_path):
    """归属兜底仅服务 no_audio 降级；insufficient 等其他未过原因不得触发付费兜底"""
    res = ContentResult(kind="timeline", status="ok", provenance="human",
                        segments=[{"start": 0, "end": 5, "text": "人工字幕内容"}])
    calls = {}
    status, src, n, _, err = _run(
        monkeypatch, DemoAdapter(res), tmp_path, calls=calls,
        ss_override={"checked": False, "passed": False,
                     "reason_code": "insufficient", "reason": "可比点不足"})
    assert (status, src, n) == ("ok", "whisper", 1)
    assert calls.get("attribution") in (None, [])
    assert calls.get("transcribe")  # 确实走了 Whisper


def test_attribution_fallback_fetch_error_contract(monkeypatch):
    """归属兜底：fetch_media_url 异常 → passed=False/reason_code=error，且不触达 vision API"""
    import vision
    called = {"vision": 0}

    def spy(conf, vp, lines, dur):
        called["vision"] += 1
        raise AssertionError("fetch 失败时不应调用 vision")

    monkeypatch.setattr(vision, "check_attribution_aligned", spy)

    class A:
        def fetch_media_url(self, ref, media="audio", lowest=False):
            raise RuntimeError("无画面流")

    class R:
        duration = 120

    cfg = {"vision": {"enabled": True, "api_key": "k"}}
    r = pipeline._attribution_fallback(A(), R(), [{"start": 0, "end": 1, "text": "x"}], cfg)
    assert r["passed"] is False and r["reason_code"] == "error"
    assert "无画面流" in r["reason"] and called["vision"] == 0


def test_attribution_fallback_vision_error_cleans_tmpdir(monkeypatch, tmp_path):
    """归属兜底：vision API 异常 → error + 临时目录清理（异常不外泄、不留垃圾）"""
    import transcriber
    import vision
    tmpdir = tmp_path / "el_attr_tmp"
    tmpdir.mkdir()
    monkeypatch.setattr(pipeline.tempfile, "mkdtemp", lambda prefix="": str(tmpdir))
    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: dest)

    def boom(conf, vp, lines, dur):
        raise RuntimeError("vision API 挂了")

    monkeypatch.setattr(vision, "check_attribution_aligned", boom)

    class A:
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "http://m/video-low"

    class R:
        duration = 120

    cfg = {"vision": {"enabled": True, "api_key": "k"}}
    r = pipeline._attribution_fallback(A(), R(), [{"start": 0, "end": 1, "text": "x"}], cfg)
    assert r["passed"] is False and r["reason_code"] == "error"
    assert "vision API 挂了" in r["reason"]
    assert not tmpdir.exists()


# ---------------- T13/14：预算闸门 / 档位接线 / 窗口复检执行 ----------------

class _Stdin:
    """最小 stdin 替身：只提供 isatty（避免依赖 pytest 捕获对象的可写属性）"""

    def __init__(self, is_tty):
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


def _no_input(monkeypatch):
    """任何交互询问都应视为失败"""
    def _boom(*a, **k):
        raise AssertionError("不应进入交互询问")
    monkeypatch.setattr("builtins.input", _boom)


def _gate_est():
    return {"amount": 5.0, "low": 3.5, "high": 6.5, "currency": "USD",
            "frames": 10000, "windows": 34, "whisper_secs": 120}


def _gate_cfg():
    return {"recheck": {"auto_approve_amount": 0.3, "confirm_over_sec": 3600}}


def test_budget_gate_non_tty_defaults_to_capped(monkeypatch, capsys):
    """非 TTY：不询问，默认【帽内尽力】= B"""
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(False))
    _no_input(monkeypatch)
    assert pipeline._budget_gate(_gate_est(), _gate_cfg(), pipeline.RunOptions()) == "B"
    err = capsys.readouterr().err
    assert "预算预测" in err and "帽内尽力" in err


def test_budget_gate_tty_user_choice_a(monkeypatch, capsys):
    """TTY：用户输入 A → 完整档2；卡片抬头带档位与时长（Q6）"""
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(True))
    monkeypatch.setattr("builtins.input", lambda *a: "a")
    assert pipeline._budget_gate(_gate_est(), _gate_cfg(), pipeline.RunOptions(),
                                 tier=2, duration_sec=120) == "A"
    err = capsys.readouterr().err
    assert "复检预算预测（档2；" in err and "时长 2.0 min" in err


def test_budget_gate_invalid_choice_defaults_b(monkeypatch):
    """非法输入 → 回退默认 B"""
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(True))
    monkeypatch.setattr("builtins.input", lambda *a: "x")
    assert pipeline._budget_gate(_gate_est(), _gate_cfg(), pipeline.RunOptions()) == "B"


def test_budget_gate_yes_recheck_preauthorizes_full_tier2(monkeypatch):
    """--yes-recheck：预授权完整档2，即使非 TTY 也不询问 → A"""
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(False))
    _no_input(monkeypatch)
    opts = pipeline.RunOptions(yes_recheck=True)
    assert pipeline._budget_gate(_gate_est(), _gate_cfg(), opts) == "A"


def test_budget_gate_recheck_1_downgrades_without_prompt(monkeypatch):
    """--recheck 1：预算闸门下直接降档1（C），不询问"""
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(True))
    _no_input(monkeypatch)
    opts = pipeline.RunOptions(recheck="1")
    assert pipeline._budget_gate(_gate_est(), _gate_cfg(), opts) == "C"


def test_budget_gate_eof_defaults_b(monkeypatch):
    """EOF（管道断开）→ 默认 B"""
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(True))

    def _eof(*a, **k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert pipeline._budget_gate(_gate_est(), _gate_cfg(), pipeline.RunOptions()) == "B"


# ---------------- artifacts.build_info 扩展 ----------------

def test_build_info_recheck_provenance_language_fields():
    from core import artifacts
    meta = ItemMeta(title="t", author="a", duration=120)
    ref = ItemRef(platform="demo", item_id="1", sub_id="1", url="u", duration=120)
    info = artifacts.build_info(meta, ref, kind="timeline", source="whisper",
                                recheck={"tier": 2}, provenance="auto", language="zh")
    assert info["recheck"] == {"tier": 2}
    assert info["provenance"] == "auto" and info["language"] == "zh"
    assert info["source"] == "whisper" and info["platform"] == "demo" and info["title"] == "t"


def test_build_info_optional_fields_omitted_when_absent():
    from core import artifacts
    meta = ItemMeta(title="t", author="a", duration=120)
    ref = ItemRef(platform="demo", item_id="1")
    info = artifacts.build_info(meta, ref, kind="timeline", source="字幕")
    assert "recheck" not in info and "provenance" not in info and "language" not in info


# ---------------- 术语表加载 ----------------

def test_load_glossary_terms_returns_str_list():
    terms = pipeline._load_glossary_terms()
    assert isinstance(terms, list) and all(isinstance(t, str) and t for t in terms)


def test_load_glossary_terms_degrades_to_empty(monkeypatch):
    import glossary
    monkeypatch.setattr(glossary, "load_glossary",
                        lambda: (_ for _ in ()).throw(RuntimeError("坏了")))
    assert pipeline._load_glossary_terms() == []


# ---------------- process_item 档位接线 ----------------

_WIRING_SIGNALS = {"dur_min": 2.0, "kp_density": 4.0,
                   "kp_density_detail": {"blocks": 8, "windows": []},
                   "kp_spread": 0.5, "src_risk": 1.0, "vis_ref": 1.0,
                   "gap_ratio": 0.1, "max_gap": 3.0}


def _run_wiring(monkeypatch, tmp_path, *, tier=1, vision_enabled=True,
                no_vision_check=False, provenance="human", language_hint="zh",
                window_meta=None, opts_extra=None, has_video=True, real_decide=False,
                cfg_recheck=None, cfg_vision_extra=None, res_language="",
                whisper_lines=None, segment_text="人工字幕内容",
                ss_override=None):
    """process_item 档位接线测试：recheck 判决 mock，复检/落盘 mock。
    real_decide=True 走真实 decide_tier（仅 gather_signals 被 mock），锁定 policy/has_video 接线。
    → (process 结果, captured)：captured["info"]=build_info 关键字，captured["window_args"]=复检参数，
      captured["window_disputed"]=透传给 _run_window_recheck 的 disputed，
      captured["decide_calls"]=decide_tier 实参记录"""
    from core import config as core_config
    cfg = dict(core_config.DEFAULT_CONFIG)
    cfg["vision"] = dict(cfg["vision"], enabled=vision_enabled,
                         api_key="k" if vision_enabled else "",
                         base_url="http://x" if vision_enabled else "")
    if cfg_vision_extra:
        cfg["vision"] = dict(cfg["vision"], **cfg_vision_extra)
    if cfg_recheck:
        cfg["recheck"] = dict(cfg["recheck"], **cfg_recheck)
    cfg["out_dir"] = str(tmp_path)
    monkeypatch.setattr(pipeline.config, "load_config", lambda: cfg)
    monkeypatch.setattr(pipeline, "_same_source_verify",
                        lambda a, r, lines, c, subtitle_language="":
                        dict(ss_override or {"checked": True, "passed": True, "avg": 0.9}))
    monkeypatch.setattr(pipeline.recheck, "gather_signals", lambda *a, **k: dict(_WIRING_SIGNALS))
    captured = {}
    decide_calls = []
    if real_decide:
        _real_decide = pipeline.recheck.decide_tier

        def _decide(signals, cfg_, prov, has_video=True, policy=None):
            decide_calls.append({"has_video": has_video, "policy": policy})
            return _real_decide(signals, cfg_, prov, has_video=has_video, policy=policy)
    else:
        def _decide(*a, **k):
            decide_calls.append(k)
            return {"tier": tier, "score": 0.7, "reasons": ["r"]}
    monkeypatch.setattr(pipeline.recheck, "decide_tier", _decide)
    captured["decide_calls"] = decide_calls

    def _fake_window_recheck(adapter, ref, lines, cfg_, decision, opts_, disputed=None):
        captured["window_args"] = (adapter, ref, lines, cfg_, decision, opts_)
        captured["window_disputed"] = disputed
        return dict(window_meta) if window_meta is not None else {}

    monkeypatch.setattr(pipeline, "_run_window_recheck", _fake_window_recheck)

    def _fake_info(meta, ref, **kw):
        captured["info"] = kw
        return {"kind": "timeline", **kw}

    monkeypatch.setattr(pipeline.artifacts, "build_info", _fake_info)
    monkeypatch.setattr(pipeline.artifacts, "write_item_artifacts", lambda *a, **k: None)
    import transcriber
    monkeypatch.setattr(transcriber, "transcribe_url",
                        lambda url, **kw: ([dict(l) for l in whisper_lines]
                                           if whisper_lines is not None
                                           else [{"start": 0, "end": 5, "text": "whisper 内容"}]))

    class A(DemoAdapter):
        pass

    A.language_hint = language_hint
    A.has_video = has_video

    res = ContentResult(kind="timeline", status="ok", provenance=provenance,
                        label="字幕(人工)", language=res_language,
                        segments=[{"start": 0, "end": 5, "text": segment_text}])
    opts = pipeline.RunOptions(out_root=str(tmp_path), no_cache=True,
                               no_vision_check=no_vision_check)
    for k, v in (opts_extra or {}).items():
        setattr(opts, k, v)
    out = pipeline.process_item(A(res), ItemRef(platform="demo", item_id="1", duration=120),
                                str(tmp_path), opts, single=True)
    return out, captured


def test_process_item_wires_tier2_recheck_into_info(monkeypatch, tmp_path):
    """档2：复检结果并入 content_info.recheck（含 estimates/coverage/capped）"""
    wm = {"coverage_pct": 90.0, "windows": [],
          "budget": {"estimate": {"frames": 1}, "user_choice": "A"}, "capped": False}
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=2, window_meta=wm)
    assert out[0] == "ok"
    info = cap["info"]
    assert info["recheck"]["tier"] == 2
    assert info["recheck"]["coverage_pct"] == 90.0 and info["recheck"]["capped"] is False
    assert info["provenance"] == "human" and info["language"] == "zh"
    assert set(info["recheck"]["signals"]) == {"dur_min", "kp_density", "kp_spread",
                                                "src_risk", "vis_ref", "gap_ratio", "max_gap"}
    assert len(cap["window_args"]) == 6 and cap["window_args"][4]["tier"] == 2
    # Q1：复检结果未携带 effective_tier 时回退判决档
    assert info["recheck"]["effective_tier"] == 2


def test_process_item_recheck_meta_includes_same_source_and_uncovered(monkeypatch, tmp_path):
    """T16 配套：content_info.recheck 顶层补齐 same_source/uncovered（骨架「复检说明」数据源）"""
    wm = {"coverage_pct": 50.0,
          "windows": [{"range": [30.0, 60.0], "kind": "uncovered",
                       "text": "超出复检帧预算（max_check_frames）"}],
          "uncovered": [[30.0, 60.0]], "capped": True}
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=2, window_meta=wm)
    assert out[0] == "ok"
    rc = cap["info"]["recheck"]
    assert rc["same_source"] == {"checked": True, "passed": True, "avg": 0.9}
    assert rc["uncovered"] == [[30.0, 60.0]]
    assert rc["uncovered"] == [w["range"] for w in rc["windows"] if w["kind"] == "uncovered"]


def test_process_item_recheck_meta_same_source_empty_when_unverified(monkeypatch, tmp_path):
    """T16 配套：auto 来源不跑同源校验 → recheck.same_source == {}（骨架显示「未执行」不失真）"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, provenance="auto")
    assert out[0] == "ok" and out[1] == "whisper"
    rc = cap["info"]["recheck"]
    assert rc["same_source"] == {}
    assert rc["uncovered"] == []


def test_process_item_recheck_skipped_without_vision(monkeypatch, tmp_path):
    """vision 未配置：档1/2 判决也跳过执行，并记录 skipped"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, vision_enabled=False)
    rc = cap["info"]["recheck"]
    assert rc["tier"] == 1 and "未配置" in rc["skipped"]
    assert "window_args" not in cap


def test_process_item_recheck_skipped_by_no_vision_check(monkeypatch, tmp_path):
    """--no-vision-check：即使 vision 已配置也不执行付费复检"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, no_vision_check=True)
    rc = cap["info"]["recheck"]
    assert "用户关闭" in rc["skipped"] and "--no-vision-check" in rc["skipped"]
    assert "window_args" not in cap


def test_process_item_recheck_tier3_marks_skip(monkeypatch, tmp_path):
    """档3（不检）：记录不检原因，不执行窗口复检"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=3)
    rc = cap["info"]["recheck"]
    assert rc["tier"] == 3 and "不检" in rc["skipped"]
    assert "window_args" not in cap


# ---------------- D2：旧 subtitle_check 键降级（T18-20 评审修复） ----------------

def test_process_item_legacy_subtitle_check_off_downgrades_policy(monkeypatch, tmp_path):
    """D2：旧键 vision.subtitle_check=off（+ tier_policy=auto，未传 --recheck）
    → decide_tier policy=off（档3 不检），旧配置语义平滑降级。"""
    out, cap = _run_wiring(monkeypatch, tmp_path, real_decide=True,
                           cfg_vision_extra={"subtitle_check": "off"})
    assert out[0] == "ok"
    assert cap["decide_calls"][0]["policy"] == "off"
    assert cap["info"]["recheck"]["tier"] == 3


def test_process_item_legacy_key_yields_to_explicit_cli_policy(monkeypatch, tmp_path):
    """D2：显式 --recheck 优先于旧键降级"""
    out, cap = _run_wiring(monkeypatch, tmp_path, real_decide=True,
                           cfg_vision_extra={"subtitle_check": "off"},
                           opts_extra={"recheck": "tier1"})
    assert cap["decide_calls"][0]["policy"] == "tier1"
    assert cap["info"]["recheck"]["tier"] == 1


def test_process_item_legacy_key_yields_to_configured_tier_policy(monkeypatch, tmp_path):
    """D2：仅当 tier_policy=auto 时才吃旧键；已配置策略保持不变"""
    out, cap = _run_wiring(monkeypatch, tmp_path, real_decide=True,
                           cfg_vision_extra={"subtitle_check": "off"},
                           cfg_recheck={"tier_policy": "tier2"})
    assert cap["decide_calls"][0]["policy"] is None   # 旧键不越权改写显式策略
    assert cap["info"]["recheck"]["tier"] == 2


# ---------------- D3：产物 language 断链（T18-20 评审修复） ----------------

def test_process_item_language_from_subtitle_result(monkeypatch, tmp_path):
    """D3：字幕采信路径 → 产物 language 取 res.language（YouTube 形态：无 language_hint）"""
    out, cap = _run_wiring(monkeypatch, tmp_path, language_hint=None, res_language="en")
    assert out[1] == "字幕(人工)"
    assert cap["info"]["language"] == "en"


def test_process_item_language_falls_back_to_adapter_hint(monkeypatch, tmp_path):
    """D3：字幕未声明语言 → 回退适配器主语言（bilibili 形态 → zh）"""
    out, cap = _run_wiring(monkeypatch, tmp_path, language_hint="zh", res_language="")
    assert cap["info"]["language"] == "zh"


def test_process_item_language_whisper_autodetect_is_none(monkeypatch, tmp_path):
    """D3：Whisper 自动检测（无 --lang / 无 language_hint）→ 不写 language 键"""
    out, cap = _run_wiring(monkeypatch, tmp_path, language_hint=None, provenance="auto")
    assert out[1] == "whisper"
    assert cap["info"]["language"] is None


def test_process_item_language_from_subtitle_on_whisper_fallback(monkeypatch, tmp_path):
    """D3：Whisper 失败回退字幕 → language 仍取字幕声明"""
    out, cap = _run_wiring(monkeypatch, tmp_path, language_hint=None, res_language="ja",
                           provenance="auto", whisper_lines=[])
    assert out[1].startswith("whisper失败→字幕")
    assert cap["info"]["language"] == "ja"


# ---------------- T14.5：双 ASR 交叉比对接线 ----------------

def test_process_item_cross_check_auto_secondary_wired(monkeypatch, tmp_path):
    """T14.5：auto 字幕作第二 ASR 意见 —— Whisper 成功后比对落盘 cross_check，
    分歧区间透传给档1 _run_window_recheck（disputed kwarg）"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, provenance="auto")
    assert out[0] == "ok" and out[1] == "whisper"
    cc = cap["info"]["recheck"]["cross_check"]
    assert cc["compared"] == 1 and cc["agree_pct"] == 0.0
    assert cc["disputed"] == [[0.0, 5.0]]
    assert cap["window_disputed"] == [[0.0, 5.0]]


def test_process_item_cross_check_agree_texts(monkeypatch, tmp_path):
    """T14.5：双 ASR 文本一致 → agree_pct=1.0、disputed 空透传"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, provenance="auto",
                           segment_text="今天讲注意力机制",
                           whisper_lines=[{"start": 0, "end": 5, "text": "今天讲注意力机制"}])
    assert cap["info"]["recheck"]["cross_check"] == {"agree_pct": 1.0, "compared": 1,
                                                     "disputed": []}
    assert cap["window_disputed"] == []


def test_process_item_cross_check_threshold_from_config(monkeypatch, tmp_path):
    """T14.5：cross_threshold 可选键（rc.get 读取）——阈值 0.0 时全部分歧转一致"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, provenance="auto",
                           cfg_recheck={"cross_threshold": 0.0})
    cc = cap["info"]["recheck"]["cross_check"]
    assert cc["agree_pct"] == 1.0 and cc["disputed"] == []


def test_process_item_cross_check_disabled_config(monkeypatch, tmp_path):
    """T14.5：recheck.cross_check=False → 不比对、不写 cross_check、disputed 为空"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, provenance="auto",
                           cfg_recheck={"cross_check": False})
    assert "cross_check" not in cap["info"]["recheck"]
    assert cap["window_disputed"] == []


def test_process_item_cross_check_absent_when_subtitle_not_whisper(monkeypatch, tmp_path):
    """T14.5：采信字幕（未跑 Whisper）→ 保持现状不写 cross_check"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, provenance="human")
    assert out[1] == "字幕(人工)"
    assert "cross_check" not in cap["info"]["recheck"]
    assert cap["window_disputed"] == []


def test_process_item_cross_check_absent_auto_no_whisper(monkeypatch, tmp_path):
    """T14.5 三路径：auto + --no-whisper（采信未校验字幕）→ 不产生 cross_check"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, provenance="auto",
                           opts_extra={"no_whisper": True})
    assert out[0] == "ok" and "--no-whisper" in out[1]
    assert "cross_check" not in cap["info"]["recheck"]
    assert cap["window_disputed"] == []


def test_process_item_cross_check_absent_auto_whisper_fallback(monkeypatch, tmp_path):
    """T14.5 三路径：auto + Whisper 空结果回退字幕 → 不产生 cross_check"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, provenance="auto",
                           whisper_lines=[])
    assert out[0] == "ok" and out[1].startswith("whisper失败→字幕")
    assert "cross_check" not in cap["info"]["recheck"]
    assert cap["window_disputed"] == []


def test_process_item_cross_check_absent_human_same_source_fail(monkeypatch, tmp_path):
    """T14.5 三路径：human 同源失败 → Whisper（无第二 ASR 意见）→ 不产生 cross_check"""
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, provenance="human",
                           ss_override={"checked": True, "passed": False, "avg": 0.1})
    assert out[0] == "ok" and out[1] == "whisper"
    assert "cross_check" not in cap["info"]["recheck"]
    assert cap["window_disputed"] == []


# ---------------- _run_window_recheck 直接执行（全 mock） ----------------

class _VideoAdapter:
    name = "demo"

    def __init__(self, url="http://m/video-low"):
        self.url = url
        self.calls = []

    def fetch_media_url(self, ref, media="audio", lowest=False):
        self.calls.append((media, lowest))
        return self.url


def _rc_cfg(**over):
    from core import config as core_config
    cfg = dict(core_config.DEFAULT_CONFIG)
    cfg["recheck"] = dict(cfg["recheck"], **over)
    cfg["vision"] = dict(cfg["vision"], enabled=True, api_key="k", base_url="http://x")
    return cfg


def _patch_window_io(monkeypatch, frames=None, items=None, download_exc=None, check_exc=None):
    """mock：下载 / 抽帧 / 模型调用。frames=None 时默认给 1 帧"""
    import transcriber
    import vision
    seen = {"download": [], "sample": [], "check": []}

    def _download(url, dest, headers=None):
        seen["download"].append(url)
        if download_exc is not None:
            raise download_exc
        with open(dest, "wb") as f:
            f.write(b"video")
        return dest

    monkeypatch.setattr(transcriber, "download_audio", _download)

    def _sample(vp, start, end, fps=3, dedup=True):
        seen["sample"].append((start, end, fps, dedup))
        return list(frames) if frames is not None else [(0.0, b"jpg")]

    monkeypatch.setattr(vision, "sample_window_frames", _sample)

    def _check(conf, fr, transcript_text="", secondary_text=""):
        seen["check"].append((transcript_text, list(fr)))
        if check_exc is not None:
            raise check_exc
        return list(items or [])

    monkeypatch.setattr(vision, "check_window", _check)
    return seen


def test_window_recheck_tier1_supplement_merged_sorted(monkeypatch):
    """档1：补充行以 source=vision补充 并入并按 start 排序；预算未触发 → A"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = [{"start": 40, "end": 45, "text": "后段内容"}]
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda lines_, dur, cfg_, disputed=None:
                        [{"start": 0, "end": 30, "reason": "知识点密集区", "score": 3.0}])
    adapter = _VideoAdapter()
    seen = _patch_window_io(monkeypatch, items=[
        {"ts": 10.0, "kind": "supplement", "text": "画面公式", "original": ""}])
    out = pipeline._run_window_recheck(adapter, ref, lines, cfg,
                                       {"tier": 1, "score": 0.4, "reasons": []},
                                       pipeline.RunOptions())
    assert adapter.calls == [("video", True)]
    assert seen["download"] == [["http://m/video-low"]]   # 候选列表契约
    assert seen["sample"] == [(0.0, 30.0, 3, True)]
    assert seen["check"] == [("", [(0.0, b"jpg")])]
    assert out["coverage_pct"] == 50.0
    assert out["budget"]["user_choice"] == "A" and out["capped"] is False
    assert lines == [
        {"start": 10.0, "end": 11.0, "text": "画面公式", "source": "vision补充"},
        {"start": 40, "end": 45, "text": "后段内容"},
    ]


def test_window_recheck_correction_replaces_line(monkeypatch):
    """校正：原转写命中行内文本 → 替换并标 source/corrected_from"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = [{"start": 0, "end": 5, "text": "他说到了注意利机制"}]
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, items=[
        {"ts": 2.0, "kind": "correction", "text": "注意力机制", "original": "注意利机制"}])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, lines, cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert lines[0]["text"] == "他说到了注意力机制"
    assert lines[0]["source"] == "vision校正"
    assert lines[0]["corrected_from"] == "注意利机制"
    assert lines[0]["corrected_to"] == "注意力机制"   # 2026-10-17：供术语库自动反哺
    assert out["windows"] == []


def test_window_recheck_uncertain_records_report_and_sorts(monkeypatch):
    """不确定项进 reports；lines 收尾按 start 排序"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = [{"start": 40, "end": 45, "text": "后段"},
             {"start": 2, "end": 3, "text": "前段"}]
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, items=[
        {"ts": 3.0, "kind": "uncertain", "text": "画面过暗", "original": ""}])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, lines, cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert out["windows"] == [{"range": [0.0, 30.0], "kind": "uncertain", "text": "画面过暗"}]
    assert [l["start"] for l in lines] == [2, 40]


def test_window_recheck_per_window_exception_recorded(monkeypatch):
    """Q2：单窗模型调用异常 → 记录 error/window_errors 并继续；该窗不计入覆盖"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = []
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, check_exc=RuntimeError("多模态 API 超时"))
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, lines, cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert out["windows"][0]["kind"] == "error"
    assert "多模态 API 超时" in out["windows"][0]["text"]
    assert out["window_errors"][0]["kind"] == "error"
    assert out["coverage_pct"] == 0.0  # Q2：复检失败窗口不计入 covered


def test_window_recheck_budget_gate_b_keeps_capped_plan(monkeypatch):
    """超阈值 + 非 TTY → B（帽内尽力）；plan_frames 已按硬帽裁剪并保持"""
    cfg = _rc_cfg(auto_approve_amount=0.001, max_check_frames=30)
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(False))
    _no_input(monkeypatch)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, frames=[])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert out["budget"]["user_choice"] == "B"
    assert out["budget"]["estimate"]["frames"] == 30
    # Q5：非 TTY 自动 B 未获用户确认，须在 budget 字段留痕
    assert out["budget"]["confirmed"] is False
    assert out["budget"]["reason"] == "non_tty_default"
    assert out["capped"] is True


def test_budget_cap_forces_best_effort(monkeypatch):
    """--budget 上限：估算超用户金额上限 → 直接 B（budget_cap），帧预算按金额上限裁剪。
    Q2：confirmed=False（自动约束，非用户确认）；Q3：estimate 回写裁剪后值 + pre_cap 留痕；
    Q5：未覆盖窗口文案按原因（金额预算）。"""
    cfg = _rc_cfg()  # 默认免打扰阈值 0.3：本场景闸门不触发，仅金额上限生效
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    opts = pipeline.RunOptions(budget=0.02)  # 0.02/0.00018 ≈ 111 帧 < 180 帧
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, opts)
    assert out["budget"]["user_choice"] == "B"
    assert out["budget"]["reason"] == "budget_cap"
    assert out["budget"]["cap"] == 0.02
    assert out["budget"]["confirmed"] is False          # Q2：自动约束未获用户确认
    assert out["budget"]["estimate"]["frames"] == 111   # Q3：裁剪后估算回写
    assert out["budget"]["estimate_pre_cap"]["frames"] == 180  # Q3：原估算留痕
    assert out["budget"]["estimate_pre_cap"]["amount"] == pytest.approx(0.03468, abs=1e-6)
    assert out["budget"]["spent_total"] == pytest.approx(0.02, abs=1e-6)
    assert seen["sample"] == [(0.0, 30.0, 3, True)]    # 90 ≤ 111 → 第 2 窗超上限不抽
    assert out["capped"] is True
    assert out["windows"] == [{"range": [30.0, 60.0], "kind": "uncovered",
                               "text": "超出复检金额预算（--budget）"}]  # Q5


def test_budget_cap_skips_gate_no_fake_prompt(monkeypatch):
    """Q2：超金额上限且同时满足 needs_gate → 不调用 _budget_gate（不弹假闸门），
    直接 B / confirmed=False / reason=budget_cap，杜绝“选 A 被改 B 且 confirmed=True”。"""
    cfg = _rc_cfg(auto_approve_amount=0.001)  # 免打扰阈值极低：闸门本会触发
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return "A"  # 旧实现：先弹闸门拿到 A，再被 cap 改写成 B（confirmed=True）

    monkeypatch.setattr(pipeline, "_budget_gate", _spy)
    seen = _patch_window_io(monkeypatch, frames=[])
    opts = pipeline.RunOptions(budget=0.001)  # est 0.03468 > 0.001 → cap 生效
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, opts)
    assert called["n"] == 0                              # Q2：不弹假闸门
    assert out["budget"]["user_choice"] == "B"
    assert out["budget"]["confirmed"] is False
    assert out["budget"]["reason"] == "budget_cap"
    assert out["budget"]["estimate"]["frames"] == 5      # 0.001/0.00018 ≈ 5
    assert out["capped"] is True and seen["sample"] == []


def test_budget_cap_respects_smaller_frame_hard_cap(monkeypatch):
    """Q6：金额允许帧数 > 硬帽时取小 —— frames == min(allowed, max_check_frames)
    且硬帽外窗口全记 uncovered（capped=True）。"""
    cfg = _rc_cfg(max_check_frames=30)  # 硬帽 30 < 金额允许 38
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[])
    opts = pipeline.RunOptions(budget=0.007)  # est(30帧/2窗)=0.00768 > 0.007；allowed=38
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, opts)
    assert out["budget"]["estimate"]["frames"] == min(38, 30)
    assert out["budget"]["estimate_pre_cap"]["frames"] == 30  # 初始 plan 已按硬帽裁剪
    assert out["capped"] is True
    assert seen["sample"] == []
    assert out["windows"] == [
        {"range": [0.0, 30.0], "kind": "uncovered",
         "text": "超出复检金额预算（--budget）"},
        {"range": [30.0, 60.0], "kind": "uncovered",
         "text": "超出复检金额预算（--budget）"}]


def test_budget_cap_below_single_window_uncovered(monkeypatch):
    """M13 金额预算边界：cap=56 帧恰好小于单窗理论需求 57（18.88s）→
    该窗 uncovered，文案按金额预算；cap 作运行帽参与准入。"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=18.88)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0.0, "end": 18.88,
                                          "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    opts = pipeline.RunOptions(budget=0.01008)  # allowed=56 帧 < 57
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, opts)
    assert out["budget"]["reason"] == "budget_cap"
    assert out["budget"]["estimate"]["frames"] == 56  # cap 裁剪后口径
    assert seen["sample"] == []
    assert out["capped"] is True
    assert out["uncovered"] == [[0.0, 18.88]]
    assert out["windows"] == [{"range": [0.0, 18.88], "kind": "uncovered",
                               "text": "超出复检金额预算（--budget）"}]


def test_budget_cap_between_truncated_plan_and_window_need(monkeypatch):
    """M13 预算路径运行帽=cap（非 plan["frames"]）：cap=58 允许 57 帧单窗，
    旧实现用 plan["frames"]=56 会误判 uncovered；新实现正常处理。"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=18.88)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0.0, "end": 18.88,
                                          "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    opts = pipeline.RunOptions(budget=0.0105)  # est 0.01122 > 剩余；allowed=58
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, opts)
    assert out["budget"]["reason"] == "budget_cap"
    assert seen["sample"] == [(0.0, 18.88, 3, True)]  # cap 58 ≥ 57 → 正常抽帧
    assert out["uncovered"] == [] and out["capped"] is False
    assert out["coverage_pct"] == 100.0


def test_budget_cap_zero_unit_price_forces_zero_frames(monkeypatch):
    """Q1：单价边界（per_frame_price==0）→ 超限时帧预算置 0，全部窗口 uncovered。"""
    cfg = _rc_cfg()
    cfg["vision"] = dict(cfg["vision"],
                         pricing={"currency": "USD", "tokens_per_frame": 600,
                                  "input_per_1m": 0.0, "output_per_1m": 1.2})
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    opts = pipeline.RunOptions(budget=0.001)  # 输出侧估算 0.00192 > 0.001 → 超限
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, opts)
    assert out["budget"]["reason"] == "budget_cap"
    assert out["budget"]["estimate"]["frames"] == 0
    assert seen["sample"] == []
    assert out["capped"] is True
    assert out["windows"] == [
        {"range": [0.0, 30.0], "kind": "uncovered",
         "text": "超出复检金额预算（--budget）"},
        {"range": [30.0, 60.0], "kind": "uncovered",
         "text": "超出复检金额预算（--budget）"}]


def test_budget_negative_cap_displays_zero(monkeypatch):
    """Q4：--budget -1 → 展示 cap 钳到 0.0（负值不泄漏），且不抽帧（全部 uncovered）。"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    opts = pipeline.RunOptions(budget=-1)
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, opts)
    assert out["budget"]["cap"] == 0.0
    assert out["budget"]["reason"] == "budget_cap"
    assert out["budget"]["estimate"]["frames"] == 0
    assert seen["sample"] == []
    assert out["capped"] is True


def test_budget_cap_none_no_effect(monkeypatch):
    """不传 --budget：行为不变（needs_gate 照常出卡；cap=None 且闸门 reason 保留）"""
    cfg = _rc_cfg(auto_approve_amount=0.001)
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(False))
    _no_input(monkeypatch)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert out["budget"]["user_choice"] == "B"                # 非 TTY 闸门默认 B
    assert out["budget"]["confirmed"] is False
    assert out["budget"]["reason"] == "non_tty_default"       # 闸门原因保留
    assert out["budget"]["cap"] is None
    assert "estimate_pre_cap" not in out["budget"]  # Q3：未裁剪 → 无 pre_cap
    assert seen["sample"] == [(0.0, 30.0, 3, True), (30.0, 60.0, 3, True)]  # 无上限裁剪
    assert out["capped"] is False


def test_budget_is_cumulative_across_items(monkeypatch):
    """spec §11.2 运行级上限：同一 opts 跨条目累计 _budget_spent。
    条目1（30s，估算 0.01734 < 剩余 0.02）正常执行并累计；
    条目2（60s，估算 0.03468 > 剩余 0.00266）帧预算裁至 14 帧 → 全部窗口 uncovered。"""
    cfg = _rc_cfg()  # 默认免打扰阈值 0.3：闸门不触发，仅金额上限生效
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda lines_, dur, cfg_, disputed=None:
                        [{"start": 0.0, "end": float(dur), "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    opts = pipeline.RunOptions(budget=0.02)

    first = pipeline._run_window_recheck(
        _VideoAdapter(), ItemRef(platform="demo", item_id="1", duration=30),
        [], cfg, {"tier": 1}, opts)
    assert opts._budget_spent > 0
    assert first["budget"]["cap"] == 0.02
    assert first["budget"]["spent_total"] == pytest.approx(0.01734, abs=1e-6)
    assert first["capped"] is False and first["frames_used"] == 1

    second = pipeline._run_window_recheck(
        _VideoAdapter(), ItemRef(platform="demo", item_id="2", duration=60),
        [], cfg, {"tier": 1}, opts)
    # spent_total 单调递增；cap 恒为运行上限
    assert second["budget"]["spent_total"] > first["budget"]["spent_total"]
    assert second["budget"]["cap"] == first["budget"]["cap"] == 0.02
    assert second["budget"]["reason"] == "budget_cap"
    assert second["budget"]["confirmed"] is False        # Q2：cap 自动约束未获用户确认
    assert second["budget"]["estimate"]["frames"] == 14  # Q3：按剩余预算裁剪后回写
    assert second["budget"]["estimate_pre_cap"]["frames"] == 180
    assert second["budget"]["spent_total"] == pytest.approx(0.02, abs=1e-6)
    assert len(seen["sample"]) == 1                      # 条目2：0 帧被抽（剩余预算截断）
    assert second["frames_used"] < first["frames_used"]    # 第二次 frames 更少
    assert second["capped"] is True
    assert second["windows"] == [
        {"range": [0.0, 30.0], "kind": "uncovered",
         "text": "超出复检金额预算（--budget）"},
        {"range": [30.0, 60.0], "kind": "uncovered",
         "text": "超出复检金额预算（--budget）"}]


def test_window_recheck_choice_c_downgrades_to_tier1_regions(monkeypatch):
    """超阈值 + 用户选 C → 重新按档1 区域选择并重算帧预算"""
    cfg = _rc_cfg(auto_approve_amount=0.001)
    ref = ItemRef(platform="demo", item_id="1", duration=300)
    calls = []
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(True))
    monkeypatch.setattr("builtins.input", lambda *a: "C")
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: calls.append("select") or
                        [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, frames=[])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 2}, pipeline.RunOptions())
    assert out["budget"]["user_choice"] == "C"
    assert out["effective_tier"] == 1  # Q1：C 降回档1
    assert calls == ["select"]
    assert out["budget"]["estimate"]["frames"] == 90  # 重算：30s × 3fps
    assert out["capped"] is False


def test_window_recheck_forwards_disputed_to_select_regions(monkeypatch):
    """T14.5：档1 区域选择收到 disputed（双 ASR 分歧区进入重点复检候选）"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    seen = {}

    def _select(lines_, dur, cfg_, disputed=None):
        seen["disputed"] = disputed
        return [{"start": 0, "end": 30, "reason": "r", "score": 3.0}]

    monkeypatch.setattr(pipeline.recheck, "select_regions", _select)
    _patch_window_io(monkeypatch, frames=[])
    pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                 {"tier": 1}, pipeline.RunOptions(),
                                 disputed=[[10.0, 20.0]])
    assert seen["disputed"] == [[10.0, 20.0]]


def test_window_recheck_choice_c_forwards_disputed(monkeypatch):
    """T14.5：档2 选 C 降档1 重选区域时同样透传 disputed"""
    cfg = _rc_cfg(auto_approve_amount=0.001)
    ref = ItemRef(platform="demo", item_id="1", duration=300)
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(True))
    monkeypatch.setattr("builtins.input", lambda *a: "C")
    seen = {}

    def _select(lines_, dur, cfg_, disputed=None):
        seen["disputed"] = disputed
        return [{"start": 0, "end": 30, "reason": "r", "score": 3.0}]

    monkeypatch.setattr(pipeline.recheck, "select_regions", _select)
    _patch_window_io(monkeypatch, frames=[])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 2}, pipeline.RunOptions(),
                                       disputed=[[50.0, 80.0]])
    assert out["effective_tier"] == 1
    assert seen["disputed"] == [[50.0, 80.0]]


def test_window_recheck_tier2_full_region_coverage(monkeypatch):
    """档2：不调用区域选择，全量时长按 window_sec 窗口化（60s → 2 窗）"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("档2 不应调用 select_regions")))
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 2}, pipeline.RunOptions())
    assert seen["sample"] == [(0.0, 30.0, 3, True), (30.0, 60.0, 3, True)]
    assert out["coverage_pct"] == 100.0
    assert out["budget"]["estimate"]["windows"] == 2


def test_window_recheck_tier2_duration_zero_uses_lines_fallback(monkeypatch):
    """duration=0（YouTube/TED 适配器未回填）→ 档2 区域回退末条字幕 end=19s，
    实抽 [0,19) 一窗、coverage>0（旧实现区域 [0,0] → 0 窗、coverage 0.0）"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=0)
    lines = [{"start": 0, "end": 19, "text": "内容"}]
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("档2 不应调用 select_regions")))
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, lines, cfg,
                                       {"tier": 2}, pipeline.RunOptions())
    assert seen["sample"] == [(0.0, 19.0, 3, True)]
    assert out["coverage_pct"] == 100.0
    assert out["budget"]["estimate"]["windows"] == 1


def test_window_recheck_tier2_non_integer_duration_full_coverage(monkeypatch):
    """M13 回归：非整数时长 18.88s → 窗口理论帧数 ceil(56.64)=57 > plan["frames"]=56
    （int 截断）。运行准入必须按运行帽（max_check_frames）而非 plan["frames"]，
    否则首窗即误判「超出复检帧预算」→ uncovered、coverage 0。"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=18.88)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("档2 不应调用 select_regions")))
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 2}, pipeline.RunOptions())
    assert out["budget"]["estimate"]["frames"] == 56  # 估算口径不变（int 截断）
    assert seen["sample"] == [(0.0, 18.88, 3, True)]  # 57 帧首窗未被误判超帽
    assert out["uncovered"] == [] and out["capped"] is False
    assert out["coverage_pct"] == 100.0
    assert out["frames_used"] == 1


def test_window_recheck_without_video_stream_skipped(monkeypatch):
    """无可下载画面流 → skipped，不下载不抽帧"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch)
    out = pipeline._run_window_recheck(_VideoAdapter(url=None), ref, [], cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert out == {"skipped": "无可下载画面流"}
    assert seen["download"] == [] and seen["sample"] == []


def test_window_recheck_download_failure_degrades(monkeypatch):
    """画面流下载异常 → skipped（不阻塞主流程）"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, download_exc=RuntimeError("画面流下载中断"))
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert "skipped" in out and "画面流下载中断" in out["skipped"]


def test_window_recheck_capped_stops_at_frame_budget(monkeypatch):
    """硬帽：帧预算耗尽后剩余窗口记 uncovered，不再抽帧（A/B 均受 max_check_frames 保护）"""
    cfg = _rc_cfg(max_check_frames=90)  # 仅够 30s×3fps 一窗
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert out["capped"] is True
    assert seen["sample"] == [(0.0, 30.0, 3, True)]  # 第二窗超帽不抽
    assert out["coverage_pct"] == 50.0
    assert out["windows"] == [{"range": [30.0, 60.0], "kind": "uncovered",
                               "text": "超出复检帧预算（max_check_frames）"}]


# ---------------- T13/14 评审修复（Q1-Q8 + 测试补强） ----------------

def test_budget_gate_card_tier_duration_and_whisper_line(monkeypatch, capsys):
    """Q6：卡片抬头带档位/时长；whisper_secs=0 时不打印 Whisper 行"""
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(False))
    _no_input(monkeypatch)
    est = dict(_gate_est())
    est["whisper_secs"] = 0
    assert pipeline._budget_gate(est, _gate_cfg(), pipeline.RunOptions(),
                                 tier=1, duration_sec=90) == "B"
    err = capsys.readouterr().err
    assert "复检预算预测（档1；" in err and "时长 1.5 min" in err
    assert "Whisper" not in err


def test_budget_gate_info_non_tty_unconfirmed(monkeypatch):
    """Q5：非 TTY 自动 B → info 标记 confirmed=False / non_tty_default"""
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(False))
    _no_input(monkeypatch)
    info = {}
    assert pipeline._budget_gate(_gate_est(), _gate_cfg(),
                                 pipeline.RunOptions(), info=info) == "B"
    assert info == {"confirmed": False, "reason": "non_tty_default"}


def test_budget_gate_info_confirmed_on_interactive(monkeypatch):
    """Q5：交互选择路径 → confirmed=True"""
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(True))
    monkeypatch.setattr("builtins.input", lambda *a: "B")
    info = {}
    assert pipeline._budget_gate(_gate_est(), _gate_cfg(),
                                 pipeline.RunOptions(), info=info) == "B"
    assert info.get("confirmed") is True and info.get("reason") == ""


def test_budget_gate_info_confirmed_on_preauthorization(monkeypatch):
    """Q5：--yes-recheck 预授权路径 → confirmed=True（即使非 TTY）"""
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(False))
    _no_input(monkeypatch)
    info = {}
    opts = pipeline.RunOptions(yes_recheck=True)
    assert pipeline._budget_gate(_gate_est(), _gate_cfg(), opts, info=info) == "A"
    assert info.get("confirmed") is True and info.get("reason") == ""


def test_budget_gate_keyboard_interrupt_defaults_b(monkeypatch):
    """测试补强：Ctrl+C（KeyboardInterrupt）→ 默认 B 且标未确认"""
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(True))

    def _interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _interrupt)
    info = {}
    assert pipeline._budget_gate(_gate_est(), _gate_cfg(),
                                 pipeline.RunOptions(), info=info) == "B"
    assert info.get("confirmed") is False


def test_window_recheck_gate_a_upgrades_tier1_to_full_tier2(monkeypatch):
    """Q1：档1 + 闸门内用户选 A → 真正升级全量档2（采样覆盖 [0,dur)）"""
    cfg = _rc_cfg(auto_approve_amount=0.001)
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = [{"start": 0, "end": 5, "text": "内容"}]
    monkeypatch.setattr(pipeline.sys, "stdin", _Stdin(True))
    monkeypatch.setattr("builtins.input", lambda *a: "A")
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, lines, cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert out["budget"]["user_choice"] == "A"
    assert out["effective_tier"] == 2
    assert seen["sample"] == [(0.0, 30.0, 3, True), (30.0, 60.0, 3, True)]
    assert out["coverage_pct"] == 100.0
    assert out["budget"]["estimate"]["windows"] == 2


def test_window_recheck_effective_tier_wired_into_info(monkeypatch, tmp_path):
    """Q1：process_item 把执行档 effective_tier 写入 content_info.recheck"""
    wm = {"coverage_pct": 100.0, "windows": [], "effective_tier": 2, "capped": False,
          "budget": {"estimate": {}, "user_choice": "A"}}
    out, cap = _run_wiring(monkeypatch, tmp_path, tier=1, window_meta=wm)
    rc = cap["info"]["recheck"]
    assert rc["effective_tier"] == 2 and rc["tier"] == 1  # 判决档保留，执行档另记


def test_window_recheck_coverage_uses_effective_duration_fallback(monkeypatch):
    """Q2：duration=0 且 lines 非空 → 分母回退末条字幕 end（coverage 不为 0）"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=0)
    lines = [{"start": 0, "end": 120, "text": "内容"}]
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, lines, cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert out["coverage_pct"] == 25.0  # 30 / 120（回退 duration）


def test_window_recheck_sample_error_continues(monkeypatch):
    """Q3：第 2 窗抽帧异常 → 记录 window_errors 并继续，不整体中断"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = [{"start": 0, "end": 5, "text": "原文"}]
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    import transcriber
    import vision
    monkeypatch.setattr(transcriber, "download_audio", lambda url, dest, headers=None: dest)
    calls = []

    def _sample(vp, start, end, fps=3, dedup=True):
        calls.append(start)
        if start >= 30.0:
            raise RuntimeError("抽帧失败")
        return [(0.0, b"jpg")]

    monkeypatch.setattr(vision, "sample_window_frames", _sample)
    monkeypatch.setattr(vision, "check_window",
                        lambda conf, fr, transcript_text="": [])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, lines, cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert "skipped" not in out
    assert calls == [0.0, 30.0]
    assert any("抽帧失败" in e["text"] for e in out["window_errors"])
    assert out["coverage_pct"] == 50.0  # 仅第 1 窗成功
    assert lines == [{"start": 0, "end": 5, "text": "原文"}]


def test_window_recheck_outer_exception_rolls_back_lines(monkeypatch):
    """Q3：外层异常（模型返回坏数据）→ lines 回滚到执行前快照，不留半应用校正"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = [{"start": 0, "end": 5, "text": "他说到了注意利机制"}]
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, items=[
        {"ts": 1.0, "kind": "correction", "text": "注意力机制", "original": "注意利机制"},
        {"ts": "坏数据", "kind": "supplement", "text": "画面公式"}])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, lines, cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert "skipped" in out
    assert lines == [{"start": 0, "end": 5, "text": "他说到了注意利机制"}]  # 校正如未发生


def test_window_recheck_download_failure_keeps_lines(monkeypatch):
    """Q3：下载失败（外层异常）→ skipped 且 lines 复原"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = [{"start": 0, "end": 5, "text": "原文"}]
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, download_exc=RuntimeError("画面流下载中断"))
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, lines, cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert "skipped" in out and "下载中断" in out["skipped"]
    assert lines == [{"start": 0, "end": 5, "text": "原文"}]


def test_window_recheck_correction_prefers_time_neighborhood(monkeypatch):
    """Q7：邻域（|start-ts|≤2s）行优先；行内仅替换 original 首次出现"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = [{"start": 40, "end": 45, "text": "注意利机制"},
             {"start": 0, "end": 5, "text": "注意利机制注意利机制"}]
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, items=[
        {"ts": 0.5, "kind": "correction", "text": "注意力机制", "original": "注意利机制"}])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, lines, cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    by_start = {l["start"]: l for l in lines}
    assert by_start[40]["text"] == "注意利机制"  # 远处行不动
    assert by_start[0]["text"] == "注意力机制注意利机制"  # 邻域行 + 仅首次替换
    assert by_start[0]["source"] == "vision校正"
    assert by_start[0]["corrected_from"] == "注意利机制"
    assert out["windows"] == []


def test_window_recheck_correction_unmatched_to_reports(monkeypatch):
    """Q7：找不到原句 → correction_unmatched 进 reports，不误改"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = [{"start": 0, "end": 5, "text": "原文"}]
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, items=[
        {"ts": 1.0, "kind": "correction", "text": "改后", "original": "不存在的句子"}])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, lines, cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert out["windows"] == [{"range": [0.0, 30.0], "kind": "correction_unmatched",
                               "text": "改后", "original": "不存在的句子"}]
    assert lines == [{"start": 0, "end": 5, "text": "原文"}]


def test_window_recheck_admission_uses_theoretical_frames(monkeypatch):
    """Q8：准入按理论帧数（去重前）——max=100 仅够第一窗(90)，第二窗记 uncovered"""
    cfg = _rc_cfg(max_check_frames=100)
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])  # 实际仅 1 帧（模拟去重）
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert seen["sample"] == [(0.0, 30.0, 3, True)]
    assert out["capped"] is True
    assert out["windows"] == [{"range": [30.0, 60.0], "kind": "uncovered",
                               "text": "超出复检帧预算（max_check_frames）"}]
    assert out["frames_used"] == 1  # 统计记实际去重后帧数


def test_window_recheck_returns_uncovered_ranges_matching_reports(monkeypatch):
    """T16 配套：_run_window_recheck 顶层返回 uncovered 区间，与 windows 明细逐条一致"""
    cfg = _rc_cfg(max_check_frames=100)
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    clipped = [w["range"] for w in out["windows"] if w["kind"] == "uncovered"]
    assert out["uncovered"] == clipped == [[30.0, 60.0]]


def test_window_recheck_partial_region_window_count(monkeypatch):
    """M6：非整窗区域计数——[0,95] / win=30 → 4 窗（含 5s 尾窗）"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=95)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 95, "reason": "r", "score": 3.0}])
    seen = _patch_window_io(monkeypatch, frames=[])
    out = pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                       {"tier": 1}, pipeline.RunOptions())
    assert out["budget"]["estimate"]["windows"] == 4
    assert [s for (s, _e, _f, _d) in seen["sample"]] == [0.0, 30.0, 60.0, 90.0]


def test_window_recheck_tmpdir_cleaned_after_exception(monkeypatch, tmp_path):
    """测试补强：复检临时目录在异常路径也被清理（无残留垃圾）"""
    cfg = _rc_cfg()
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    created = []

    def _mkdtemp(prefix="", **kw):
        d = tmp_path / f"{prefix}spy"
        d.mkdir(parents=True, exist_ok=True)
        created.append(d)
        return str(d)

    monkeypatch.setattr(pipeline.tempfile, "mkdtemp", _mkdtemp)
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 30, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, check_exc=RuntimeError("复检挂了"))
    pipeline._run_window_recheck(_VideoAdapter(), ref, [], cfg,
                                 {"tier": 1}, pipeline.RunOptions())
    assert created and not created[0].exists()


def test_process_item_forwards_recheck_policy_off(monkeypatch, tmp_path):
    """M9：opts.recheck 透传 decide_tier（policy=off）→ tier=3 不检"""
    out, cap = _run_wiring(monkeypatch, tmp_path, real_decide=True,
                           opts_extra={"recheck": "off"})
    rc = cap["info"]["recheck"]
    assert rc["tier"] == 3 and "不检" in rc["skipped"]
    assert cap["decide_calls"][0]["policy"] == "off"
    assert "window_args" not in cap


def test_process_item_audio_only_marks_recheck_na(monkeypatch, tmp_path):
    """Q4：纯音频来源（has_video=False）→ tier=n/a + 专属文案（锁定 M8 接线）"""
    out, cap = _run_wiring(monkeypatch, tmp_path, real_decide=True, has_video=False)
    rc = cap["info"]["recheck"]
    assert rc["tier"] == "n/a"
    assert rc["skipped"] == "纯音频来源：多模态复检不适用"
    assert cap["decide_calls"][0]["has_video"] is False
    assert "window_args" not in cap
    assert out[0] == "ok"


# ---------------- T15：产物透明度（recheck_report.json + content.txt 标注） ----------------

_WM_T15 = {"coverage_pct": 50.0,
           "windows": [{"range": [0.0, 30.0], "kind": "uncertain", "text": "画面过暗"},
                       {"range": [30.0, 60.0], "kind": "uncovered",
                        "text": "超出复检帧预算（max_check_frames）"}],
           "window_errors": [], "capped": True,
           "budget": {"estimate": {"frames": 30}, "user_choice": "B",
                      "confirmed": False, "reason": "non_tty_default"},
           "effective_tier": 1, "frames_used": 10}


def _run_full_artifacts(monkeypatch, tmp_path, *, window_meta=None, tier=1,
                        vision_enabled=True, mutate_lines=None,
                        provenance="human", segment_text="他说到了注意利机制",
                        whisper_lines=None, ss_override=None, label="字幕(人工)",
                        res_language=""):
    """T15：真实落盘路径（build_info/write_item_artifacts 均不 mock），
    仅 mock 网络/模型；mutate_lines 用于模拟窗口复检对 lines 的补充/校正。"""
    from core import config as core_config
    cfg = dict(core_config.DEFAULT_CONFIG)
    cfg["vision"] = dict(cfg["vision"], enabled=vision_enabled,
                         api_key="k" if vision_enabled else "",
                         base_url="http://x" if vision_enabled else "")
    cfg["out_dir"] = str(tmp_path)
    monkeypatch.setattr(pipeline.config, "load_config", lambda: cfg)
    monkeypatch.setattr(pipeline, "_same_source_verify",
                        lambda a, r, lines, c, subtitle_language="":
                        dict(ss_override or {"checked": True, "passed": True,
                                             "avg": 0.9, "compared": 3}))
    monkeypatch.setattr(pipeline.recheck, "gather_signals",
                        lambda *a, **k: dict(_WIRING_SIGNALS))
    monkeypatch.setattr(pipeline.recheck, "decide_tier",
                        lambda *a, **k: {"tier": tier, "score": 0.7, "reasons": ["r"]})
    calls = {"window": 0}

    def _fake_window(adapter, ref, lines, cfg_, decision, opts_, disputed=None):
        calls["window"] += 1
        if mutate_lines is not None:
            mutate_lines(lines)
        return dict(window_meta or {})

    monkeypatch.setattr(pipeline, "_run_window_recheck", _fake_window)
    import transcriber
    monkeypatch.setattr(transcriber, "transcribe_url",
                        lambda url, **kw: ([dict(l) for l in whisper_lines]
                                           if whisper_lines is not None
                                           else [{"start": 0, "end": 5, "text": "whisper 内容"}]))

    class A(DemoAdapter):
        pass

    A.language_hint = "zh"
    A.has_video = True
    res = ContentResult(kind="timeline", status="ok", provenance=provenance,
                        label=label, language=res_language,
                        segments=[{"start": 0, "end": 5, "text": segment_text}])
    opts = pipeline.RunOptions(out_root=str(tmp_path), no_cache=True)
    out = pipeline.process_item(A(res), ItemRef(platform="demo", item_id="1", duration=120),
                                str(tmp_path), opts, single=True)
    return out, calls


def test_process_item_writes_recheck_report(monkeypatch, tmp_path):
    """T15：执行复检的单条处理 → recheck_report.json 含 tier/budget/same_source/uncertain"""
    out, calls = _run_full_artifacts(monkeypatch, tmp_path, window_meta=_WM_T15)
    assert out[0] == "ok" and calls["window"] == 1
    report_path = os.path.join(out[3], "recheck_report.json")
    assert os.path.exists(report_path)
    data = json.load(open(report_path, encoding="utf-8"))
    assert data["tier"] == 1 and data["effective_tier"] == 1
    assert data["budget"]["user_choice"] == "B" and data["budget"]["confirmed"] is False
    assert data["same_source"]["checked"] is True and data["same_source"]["passed"] is True
    assert data["uncertain"] == [{"range": [0.0, 30.0], "kind": "uncertain",
                                  "text": "画面过暗"}]
    assert data["coverage_pct"] == 50.0 and data["capped"] is True
    assert data["frames_used"] == 10


def test_process_item_content_info_carries_uncovered_from_window_clip(monkeypatch, tmp_path):
    """T16 配套（真实落盘）：帧帽裁剪出的未覆盖区间透传进 content_info.recheck；
    且 recheck_report.json 保持既有结构（未覆盖逐条在 windows 明细，不另设顶层键）"""
    from core import config as core_config
    cfg = dict(core_config.DEFAULT_CONFIG)
    cfg["vision"] = dict(cfg["vision"], enabled=True, api_key="k", base_url="http://x")
    cfg["recheck"] = dict(cfg["recheck"], max_check_frames=100)
    cfg["out_dir"] = str(tmp_path)
    monkeypatch.setattr(pipeline.config, "load_config", lambda: cfg)
    monkeypatch.setattr(pipeline, "_same_source_verify",
                        lambda a, r, lines, c, subtitle_language="":
                        {"checked": True, "passed": True, "avg": 0.9})
    monkeypatch.setattr(pipeline.recheck, "gather_signals",
                        lambda *a, **k: dict(_WIRING_SIGNALS))
    monkeypatch.setattr(pipeline.recheck, "decide_tier",
                        lambda *a, **k: {"tier": 1, "score": 0.7, "reasons": ["r"]})
    monkeypatch.setattr(pipeline.recheck, "select_regions",
                        lambda *a, **k: [{"start": 0, "end": 60, "reason": "r", "score": 3.0}])
    _patch_window_io(monkeypatch, frames=[(0.0, b"jpg")])

    class A(DemoAdapter):
        pass

    A.language_hint = "zh"
    A.has_video = True
    res = ContentResult(kind="timeline", status="ok", provenance="human",
                        label="字幕(人工)",
                        segments=[{"start": 0, "end": 5, "text": "人工字幕内容"}])
    opts = pipeline.RunOptions(out_root=str(tmp_path), no_cache=True)
    out = pipeline.process_item(A(res), ItemRef(platform="demo", item_id="1", duration=60),
                                str(tmp_path), opts, single=True)
    assert out[0] == "ok"
    info = json.load(open(os.path.join(out[3], "content_info.json"), encoding="utf-8"))
    rc = info["recheck"]
    assert rc["same_source"]["avg"] == 0.9
    assert rc["uncovered"] == [[30.0, 60.0]]
    assert rc["uncovered"] == [w["range"] for w in rc["windows"] if w["kind"] == "uncovered"]
    report_data = json.load(open(os.path.join(out[3], "recheck_report.json"), encoding="utf-8"))
    assert "uncovered" not in report_data  # 保持既有结构：未覆盖已在 windows 明细留痕
    assert report_data["same_source"]["avg"] == 0.9


def test_process_item_content_txt_annotates_vision_lines(monkeypatch, tmp_path):
    """T15：content.txt 校正/补充行带标注（校正含 corrected_from）"""
    def _mutate(lines):
        lines[0]["text"] = "他说到了注意力机制"
        lines[0]["source"] = "vision校正"
        lines[0]["corrected_from"] = "注意利机制"
        lines.append({"start": 10.0, "end": 11.0, "text": "画面公式",
                      "source": "vision补充"})
        lines.sort(key=lambda x: float(x["start"]))

    out, _ = _run_full_artifacts(monkeypatch, tmp_path, window_meta=_WM_T15,
                                 mutate_lines=_mutate)
    content = open(os.path.join(out[3], "content.txt"), encoding="utf-8").read()
    assert "[0.0-5.0] 他说到了注意力机制 【画面校正：原「注意利机制」】" in content
    assert "[10.0-11.0] 画面公式 【画面补充】" in content


def test_process_item_cross_check_persisted_to_recheck_report(monkeypatch, tmp_path):
    """T14.5：auto + Whisper + 字幕候选 → cross_check 真实落盘（content_info + recheck_report）"""
    out, _ = _run_full_artifacts(
        monkeypatch, tmp_path, tier=1, provenance="auto", segment_text="人工字幕内容",
        whisper_lines=[{"start": 0, "end": 5, "text": "whisper 内容"}])
    assert out[0] == "ok" and out[1] == "whisper"
    info = json.load(open(os.path.join(out[3], "content_info.json"), encoding="utf-8"))
    report = json.load(open(os.path.join(out[3], "recheck_report.json"), encoding="utf-8"))
    expected = {"agree_pct": 0.0, "compared": 1, "disputed": [[0.0, 5.0]]}
    assert info["recheck"]["cross_check"] == expected
    assert report["cross_check"] == expected


def test_process_item_recheck_report_skipped_branch(monkeypatch, tmp_path):
    """T15：复检未执行（vision 未配置）→ 报告仍落盘并记 skipped；
    budget 不可达故不出现，uncertain 为空列表，same_source 照常留痕"""
    out, calls = _run_full_artifacts(monkeypatch, tmp_path, vision_enabled=False)
    assert calls["window"] == 0
    data = json.load(open(os.path.join(out[3], "recheck_report.json"), encoding="utf-8"))
    assert data["tier"] == 1 and "未配置" in data["skipped"]
    assert "budget" not in data and data["uncertain"] == []
    assert data["same_source"]["compared"] == 3


def test_process_item_lang_mismatch_no_vision_keeps_subtitle_and_same_source(monkeypatch, tmp_path):
    """翻译轨（vision 不可用）采信 → src 带标注；recheck_report 仍写 same_source（含兜底证据）"""
    out, _ = _run_full_artifacts(
        monkeypatch, tmp_path, vision_enabled=False, provenance="official",
        label="字幕(官方 zh-Hans)", res_language="zh-Hans",
        ss_override=dict(_LANG_MISMATCH_SS))
    assert out[0] == "ok"
    assert out[1] == "字幕(官方 zh-Hans)（语言不匹配，未同源校验）"
    report = json.load(open(os.path.join(out[3], "recheck_report.json"), encoding="utf-8"))
    assert report["same_source"]["reason_code"] == "lang_mismatch"
    assert report["same_source"]["asr_languages"] == ["en"]
    assert report["same_source"]["attribution_fallback"]["reason_code"] == "no_vision"


def test_process_item_document_writes_no_recheck_report(monkeypatch, tmp_path):
    """T15：document 形态无档位概念 → 不写 recheck_report.json（三件套照常）"""
    from core import config as core_config
    cfg = dict(core_config.DEFAULT_CONFIG)
    cfg["out_dir"] = str(tmp_path)
    monkeypatch.setattr(pipeline.config, "load_config", lambda: cfg)

    class DocAdapter:
        name = "demo"

        def fetch_content(self, ref):
            return ContentResult(kind="document", status="ok",
                                 blocks=[{"heading": "引言", "text": "正文"}])

        def fetch_meta(self, ref):
            return ItemMeta(title="t", author="a")

    opts = pipeline.RunOptions(out_root=str(tmp_path), no_cache=True)
    out = pipeline.process_item(DocAdapter(), ItemRef(platform="demo", item_id="1"),
                                str(tmp_path), opts, single=True)
    assert out[0] == "ok"
    assert os.path.exists(os.path.join(out[3], "content.txt"))
    assert not os.path.exists(os.path.join(out[3], "recheck_report.json"))



def test_window_recheck_skips_when_vision_deps_missing(monkeypatch):
    """低配：Pillow/PyAV 缺失 → 提前跳过复检（不逐窗报错、不下载）"""
    import vision

    monkeypatch.setattr(vision, "deps_available", lambda: False)

    class A:
        def fetch_media_url(self, ref, media="audio", lowest=False):
            raise AssertionError("依赖缺失时不应尝试下载")

    class R:
        duration = 60

    cfg = {"vision": {"enabled": True, "api_key": "k"}, "recheck": {}}
    out = pipeline._run_window_recheck(A(), R(), [{"start": 0, "end": 10, "text": "x"}],
                                       cfg, {"tier": 1, "score": 0.4, "reasons": []},
                                       pipeline.RunOptions())
    assert "缺少画面复检依赖" in out.get("skipped", "")


def test_window_recheck_reports_progress_and_checked_windows(monkeypatch, tmp_path, capsys):
    """复检逐窗进度 + checked_windows：2 个窗口无问题时也必须可区分「已检查」"""
    import transcriber
    import vision

    class Adapter:
        name = "demo"
        def fetch_media_url(self, ref, media="video", lowest=False):
            return "http://v/1"

    monkeypatch.setattr(vision, "deps_available", lambda: True)
    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: dest)
    monkeypatch.setattr(vision, "sample_window_frames",
                        lambda *a, **k: [(0.0, b"jpg")])
    calls = []
    monkeypatch.setattr(vision, "check_window",
                        lambda cfg, frames, transcript_text="": calls.append(transcript_text) or [])

    cfg = {"recheck": {"frame_fps": 3, "window_sec": 30, "dedup": True},
           "vision": {"enabled": True, "pricing": {}}}
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = [{"start": 0, "end": 60, "text": "讲了一堆东西"}]
    opts = pipeline.RunOptions(out_root=str(tmp_path), budget=None)
    meta = pipeline._run_window_recheck(Adapter(), ref, lines, cfg,
                                        {"tier": 2}, opts)
    assert meta["checked_windows"] == 2
    assert meta["coverage_pct"] == 100.0
    err = capsys.readouterr().err
    assert "复检窗口 1/2" in err and "复检窗口 2/2" in err


def test_low_confidence_correction_not_applied(monkeypatch, tmp_path):
    """置信度分级：低置信校正只留痕、不改正文（避免图表数字串行误改）"""
    import transcriber
    import vision

    class Adapter:
        name = "demo"
        def fetch_media_url(self, ref, media="video", lowest=False):
            return "http://v/1"

    monkeypatch.setattr(vision, "deps_available", lambda: True)
    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: dest)
    monkeypatch.setattr(vision, "sample_window_frames", lambda *a, **k: [(0.0, b"jpg")])
    monkeypatch.setattr(vision, "check_window",
                        lambda *a, **k: [{"ts": 0.0, "kind": "correction",
                                          "text": "19.1", "original": "74.0",
                                          "confidence": "低"}])

    cfg = {"recheck": {"frame_fps": 3, "window_sec": 30},
           "vision": {"enabled": True, "pricing": {}}}
    ref = ItemRef(platform="demo", item_id="1", duration=60)
    lines = [{"start": 0, "end": 60, "text": "Opus 5 的 74.0"}]
    meta = pipeline._run_window_recheck(Adapter(), ref, lines, cfg, {"tier": 2},
                                        pipeline.RunOptions(out_root=str(tmp_path)))
    assert lines[0]["text"] == "Opus 5 的 74.0"
    assert any(w.get("kind") == "low_confidence" for w in meta["windows"])
