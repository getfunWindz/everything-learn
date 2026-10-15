# -*- coding: utf-8 -*-
"""M13：pipeline 政策栈与预算闸门集成测试（全部 mock，无网络无 vision）"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from core import pipeline
from core.models import ItemRef


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
