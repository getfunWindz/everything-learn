# -*- coding: utf-8 -*-
"""M13：pipeline 政策栈与预算闸门集成测试（全部 mock，无网络无 vision）"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from core import pipeline
from core.models import ItemRef


def test_same_source_verify_orchestration(monkeypatch):
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
    cfg = {"recheck": {"same_source_threshold": 0.45, "same_source_model": "small"}}
    ref = ItemRef(platform="demo", item_id="1", duration=120)
    lines = [{"start": 0, "end": 10, "text": "今天讲注意力机制"}]
    r = pipeline._same_source_verify(Adapter(), ref, lines, cfg)
    assert r["checked"] is True and r["passed"] is True


def test_same_source_verify_degrades_without_blocking(monkeypatch):
    """无可下载音频流 / 下载异常 → checked=False（降级不阻塞主流程）"""
    import transcriber

    class NoMediaAdapter:
        name = "demo"
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return None

    cfg = {"recheck": {}}
    ref = ItemRef(platform="demo", item_id="1", duration=120)
    r = pipeline._same_source_verify(NoMediaAdapter(), ref, [], cfg)
    assert r["checked"] is False and r["reason"]

    class Adapter:
        name = "demo"
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "http://m/audio"

    def boom(url, dest, headers=None):
        raise RuntimeError("下载失败")

    monkeypatch.setattr(transcriber, "download_audio", boom)
    r2 = pipeline._same_source_verify(Adapter(), ref, [], cfg)
    assert r2["checked"] is False and "下载失败" in r2["reason"]
