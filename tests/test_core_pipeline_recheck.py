# -*- coding: utf-8 -*-
"""M13：pipeline 政策栈与预算闸门集成测试（全部 mock，无网络无 vision）"""
import os, sys
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
    monkeypatch.setattr(transcriber, "transcribe_samples",
                        lambda samples, sr, model_size="small", language=None:
                        spot_texts.get(int(last["start"]), ""))
    cfg = {"recheck": {"same_source_threshold": 0.45, "same_source_model": "small"}}
    ref = ItemRef(platform="demo", item_id="1", duration=120)
    lines = [{"start": 0, "end": 10, "text": "今天讲注意力机制"},
             {"start": 54, "end": 66, "text": "然后我们看扩散模型"},
             {"start": 102, "end": 114, "text": "最后总结一下"}]
    r = pipeline._same_source_verify(Adapter(), ref, lines, cfg)
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
    monkeypatch.setattr(transcriber, "transcribe_samples",
                        lambda samples, sr, model_size="small", language=None: "今天讲注意力机制")
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
    monkeypatch.setattr(pipeline, "_same_source_verify", lambda a, r, lines, c: dict(ss))

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


def test_whisper_missing_with_candidate_still_fails(monkeypatch, tmp_path):
    """T12 裁决：未安装 faster-whisper 是环境问题 → 保留 whisper_missing，
    即使有字幕候选也不回退（与“转写失败/为空 → 回退字幕”区分开）"""
    import transcriber

    res = ContentResult(kind="timeline", status="ok", provenance="auto",
                        segments=[{"start": 0, "end": 5, "text": "ai 字幕"}])
    status, src, n, _, err = _run(monkeypatch, DemoAdapter(res), tmp_path,
                                  whisper_exc=transcriber.WhisperNotInstalled("未安装"))
    assert status == "whisper_missing"
    assert n == 0 and err == "未安装 faster-whisper"


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
    assert r["passed"] is True and seen["url"] == "http://m/video-low"


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
