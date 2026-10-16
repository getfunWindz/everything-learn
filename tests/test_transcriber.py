import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import transcriber

def test_load_whisper_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(transcriber.WhisperNotInstalled):
        transcriber._load_whisper()

def test_detect_device_cpu_without_ctranslate2(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    assert transcriber.detect_device() == "cpu"

def test_download_audio(tmp_path, monkeypatch):
    calls = {}
    def fake_get(url, headers=None, stream=False, timeout=None):
        calls["url"] = url
        class R:
            def raise_for_status(self): pass
            def iter_content(self, chunk_size=1): yield b"1234"
        return R()
    monkeypatch.setattr(transcriber.requests, "get", fake_get)
    dest = transcriber.download_audio("http://x/audio.m4s", str(tmp_path / "a.m4s"))
    assert calls["url"] == "http://x/audio.m4s"
    assert open(dest, "rb").read() == b"1234"


def test_download_audio_retries_transient(tmp_path, monkeypatch):
    """瞬态网络错误（连接失败/超时）→ 同候选重试，成功即返回"""
    calls = {"n": 0}
    class R:
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=1): yield b"abc"
    def fake_get(url, headers=None, stream=False, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise transcriber.requests.exceptions.ConnectionError("boom")
        return R()
    monkeypatch.setattr(transcriber.requests, "get", fake_get)
    monkeypatch.setattr(transcriber.time, "sleep", lambda s: None)
    dest = str(tmp_path / "a.m4s")
    transcriber.download_audio("http://x/a.m4s", dest, backoff=0)
    assert calls["n"] == 2
    assert open(dest, "rb").read() == b"abc"


def test_download_audio_falls_back_to_next_candidate(tmp_path, monkeypatch):
    """候选 URL：4xx（过期/拒绝）不重试，直接换下一候选"""
    order = []
    class R403:
        status_code = 403
        def raise_for_status(self):
            raise transcriber.requests.exceptions.HTTPError("403")
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=1): yield b"ok"
    def fake_get(url, headers=None, stream=False, timeout=None):
        order.append(url)
        return R403() if "dead" in url else R()
    monkeypatch.setattr(transcriber.requests, "get", fake_get)
    monkeypatch.setattr(transcriber.time, "sleep", lambda s: None)
    dest = str(tmp_path / "b.m4s")
    transcriber.download_audio(["http://x/dead.m4s", "http://x/live.m4s"], dest, backoff=0)
    assert order == ["http://x/dead.m4s", "http://x/live.m4s"]
    assert open(dest, "rb").read() == b"ok"


def test_download_audio_all_candidates_fail_raises(tmp_path, monkeypatch):
    """全部候选失败 → 抛出（调用方降级/记录）"""
    def fake_get(url, headers=None, stream=False, timeout=None):
        raise transcriber.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(transcriber.requests, "get", fake_get)
    monkeypatch.setattr(transcriber.time, "sleep", lambda s: None)
    with pytest.raises(transcriber.requests.exceptions.ConnectionError):
        transcriber.download_audio(["http://x/1.m4s", "http://x/2.m4s"],
                                   str(tmp_path / "c.m4s"), attempts=2, backoff=0)

def test_transcribe_gpu_failure_falls_back_cpu(monkeypatch):
    """检测到 CUDA 但库不可用（DLL 缺失）→ 自动回退 CPU 重试"""
    calls = []
    class FakeModel:
        def __init__(self, model_size, device, compute_type):
            calls.append((device, compute_type))
        def transcribe(self, audio_path, language="zh", vad_filter=False, log_progress=None, progress_callback=None):
            if calls[-1][0] == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found")
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "ok"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cuda")
    out = transcriber.transcribe("x.m4s")
    assert calls == [("cuda", "float16"), ("cpu", "int8")]  # 先 GPU，失败回退 CPU
    assert out == [{"start": 0.0, "end": 1.0, "text": "ok"}]

def test_transcribe_progress_callback(monkeypatch):
    seen = []
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, log_progress=None, progress_callback=None):
            if progress_callback:
                progress_callback(10, 100)
                progress_callback(100, 100)
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "hi"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    transcriber.transcribe("x.m4s", progress_callback=lambda a, b: seen.append((a, b)))
    assert seen == [(10, 100), (100, 100)]

def test_clean_text_compresses_fillers():
    assert transcriber.clean_text("啊啊啊你好") == "啊你好"
    assert transcriber.clean_text("哈哈哈哈 对") == "哈哈 对"
    assert transcriber.clean_text("对对对 没错") == "对对 没错"
    assert transcriber.clean_text("这个  是   测试") == "这个 是 测试"
    assert transcriber.clean_text("正常句子") == "正常句子"  # 正常文本不变

def test_transcribe_applies_clean(monkeypatch):
    """转写输出经过 clean_text 清洗"""
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, log_progress=None, progress_callback=None):
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "啊啊啊  大家好"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    out = transcriber.transcribe("x.m4s")
    assert out[0]["text"] == "啊 大家好"

def test_transcribe_vad_fallback(monkeypatch):
    """vad_filter 滤空时自动降级为 vad_filter=False 重试"""
    calls = []
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, log_progress=None, progress_callback=None):
            calls.append(vad_filter)
            if vad_filter:
                return [], None
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "hello"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    out = transcriber.transcribe("x.m4s")
    assert calls == [True, False]  # 先 VAD，空则降级重试
    assert out == [{"start": 0.0, "end": 1.0, "text": "hello"}]

def test_transcribe_video_flow(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self): self.url_called = None
        def get_audio_url(self, bvid, cid): self.url_called = (bvid, cid); return "http://audio"
    client = FakeClient()
    monkeypatch.setattr(transcriber, "download_audio", lambda url, dest, headers=None: dest)
    monkeypatch.setattr(transcriber, "transcribe",
                        lambda path, model_size="medium", progress_callback=None, **kw: [{"start": 0.0, "end": 1.0, "text": "hi"}])
    out = transcriber.transcribe_video(client, "BV1GJ411x7h7", 1001)
    assert out == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    assert client.url_called == ("BV1GJ411x7h7", 1001)


def test_transcribe_url_flow(monkeypatch):
    """平台无关入口：给媒体直链 → 下载 → 转写"""
    calls = {}
    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: calls.setdefault("url", url) or dest)
    monkeypatch.setattr(transcriber, "transcribe",
                        lambda path, model_size="medium", **kw: [{"start": 0.0, "end": 1.0, "text": "hi"}])
    out = transcriber.transcribe_url("http://media/audio.m4s", model_size="small", duration_sec=10)
    assert out == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    assert calls["url"] == "http://media/audio.m4s"


def test_transcribe_url_passes_language(monkeypatch):
    """language 透传（X 等多语言平台用 None 自动检测；默认仍 zh 保持既有行为）"""
    captured = {}
    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: dest)

    def fake_transcribe(path, **kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(transcriber, "transcribe", fake_transcribe)
    transcriber.transcribe_url("http://media/audio.m4s", language=None)
    assert captured.get("language") is None


# ---------- VAD 策略：长视频默认禁用，可手动打开 ----------

def _fake_model_factory(calls):
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, log_progress=None, progress_callback=None):
            calls.append(vad_filter)
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "ok"
            return [s], None
    return FakeModel

def test_vad_plan_forced_on():
    assert transcriber._vad_plan(True, 7200) == [True]
    assert transcriber._vad_plan(False, 10) == [False]

def test_vad_plan_long_video_default_off():
    assert transcriber._vad_plan(None, 7200) == [False]       # 长视频默认禁 VAD（不回退）
    assert transcriber._vad_plan(None, 600) == [True, False]  # 短视频保持原兜底

def test_transcribe_long_video_disables_vad(monkeypatch):
    calls = []
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: _fake_model_factory(calls))
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cpu")
    transcriber.transcribe("x.m4s", duration_sec=7200)
    assert calls == [False]

def test_transcribe_vad_manual_on(monkeypatch):
    calls = []
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: _fake_model_factory(calls))
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cpu")
    transcriber.transcribe("x.m4s", duration_sec=7200, vad=True)
    assert calls == [True]

def test_default_model_is_medium():
    import inspect
    assert inspect.signature(transcriber.transcribe).parameters["model_size"].default == "medium"


def test_download_audio_headers_passthrough(tmp_path, monkeypatch):
    """媒体 CDN 平台化请求头：显式 headers 生效；缺省用内置 HEADERS（B站向）"""
    import transcriber
    seen = {}

    class R:
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=None): yield b"x"

    def fake_get(url, headers=None, stream=None, timeout=None):
        seen["headers"] = headers
        return R()
    monkeypatch.setattr(transcriber.requests, "get", fake_get)
    dest = str(tmp_path / "a.bin")
    transcriber.download_audio("http://x/1", dest, headers={"X": "1"})
    assert seen["headers"] == {"X": "1"}
    transcriber.download_audio("http://x/1", dest)
    assert seen["headers"] is transcriber.HEADERS


def test_extract_audio_clip(tmp_path):
    pytest.importorskip("av")
    import numpy as np
    import av as _av
    # 生成 3 秒 16k 单声道 wav，分段常数振幅（非零信号：0-1s=6554，1-2s=16384，2-3s=26214）
    pcm = np.concatenate([
        np.full(16000, 6554, dtype=np.int16),
        np.full(16000, 16384, dtype=np.int16),
        np.full(16000, 26214, dtype=np.int16),
    ])
    container = _av.open(str(tmp_path / "a.wav"), mode="w")
    stream = container.add_stream("pcm_s16le", rate=16000)
    for i in range(0, len(pcm), 1024):
        block = pcm[i:i + 1024]
        if len(block) < 1024:
            block = np.pad(block, (0, 1024 - len(block)))
        frame = _av.AudioFrame.from_ndarray(
            block.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 16000
        for p in stream.encode(frame):
            container.mux(p)
    for p in stream.encode():
        container.mux(p)
    container.close()
    from transcriber import extract_audio_clip
    samples, sr = extract_audio_clip(str(tmp_path / "a.wav"), 1.0, 2.0)
    assert sr == 16000                                  # API 契约
    assert samples.dtype == np.float32                  # 归一化输出类型
    assert 0.1 < float(np.abs(samples).max()) <= 1.0    # 有信号且已 /32768 归一化
    assert 12000 <= len(samples) <= 20000               # ≈1s @16k（帧粒度）


def test_transcribe_samples_uses_model(monkeypatch):
    import numpy as np
    import transcriber

    class Seg:
        start, end, text = 0.0, 1.0, " 你好，世界 "

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, samples, language=None, vad_filter=False):
            assert isinstance(samples, np.ndarray)
            return [Seg()], None

    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cpu")
    out = transcriber.transcribe_samples(np.zeros(16000, dtype=np.float32),
                                         model_size="small", language="zh")
    assert "你好" in out


def test_transcribe_samples_info_returns_language(monkeypatch):
    """同源校验需要 ASR 检测语言 → transcribe_samples_info 返回 (text, language)"""
    import numpy as np
    import transcriber

    class Seg:
        start, end, text = 0.0, 1.0, "  hello world "

    class Info:
        language = "en"

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, samples, language=None, vad_filter=False):
            return [Seg()], Info()

    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cpu")
    text, lang = transcriber.transcribe_samples_info(np.zeros(16000, dtype=np.float32),
                                                     model_size="small", language="zh")
    assert text == "hello world" and lang == "en"


def test_transcribe_samples_info_missing_language_degrades_to_empty(monkeypatch):
    """info 缺失/无 language 属性 → 语言退化为空串（不抛异常）"""
    import numpy as np
    import transcriber

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, samples, language=None, vad_filter=False):
            return [], None

    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cpu")
    assert transcriber.transcribe_samples_info(np.zeros(10, dtype=np.float32)) == ("", "")


def test_transcribe_samples_compat_returns_text_only(monkeypatch):
    """旧接口 transcribe_samples 签名与返回值保持不变（只返回 text）"""
    import numpy as np
    import transcriber

    monkeypatch.setattr(transcriber, "transcribe_samples_info",
                        lambda samples, sample_rate=16000, model_size="small", language=None:
                        ("ok", "en"))
    assert transcriber.transcribe_samples(np.zeros(10, dtype=np.float32)) == "ok"


def test_download_audio_resumes_after_incomplete_read(tmp_path, monkeypatch):
    """连接中断（IncompleteRead/ChunkedEncodingError）→ 带 Range 续传，已收内容不丢"""
    calls = []
    class R1:
        status_code = 200
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=1):
            yield b"AAAA"
            raise transcriber.requests.exceptions.ChunkedEncodingError(
                "Connection broken: IncompleteRead(4 bytes read, 4 more expected)")
    class R2:
        status_code = 206
        headers = {"Content-Range": "bytes 4-7/8"}
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=1): yield b"BBBB"
    def fake_get(url, headers=None, stream=False, timeout=None):
        calls.append(dict(headers or {}))
        return R1() if len(calls) == 1 else R2()
    monkeypatch.setattr(transcriber.requests, "get", fake_get)
    monkeypatch.setattr(transcriber.time, "sleep", lambda s: None)
    dest = str(tmp_path / "r.m4s")
    transcriber.download_audio("http://x/a.m4s", dest, backoff=0)
    assert open(dest, "rb").read() == b"AAAABBBB"
    assert "Range" not in calls[0]
    assert calls[1].get("Range") == "bytes=4-"


def test_download_audio_respects_time_budget(tmp_path, monkeypatch):
    """总时长预算耗尽 → 有界退出（不无限重试/不阻塞流水线）"""
    clock = {"t": 100.0, "n": 0}
    def fake_get(url, headers=None, stream=False, timeout=None):
        clock["n"] += 1
        raise transcriber.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(transcriber.requests, "get", fake_get)
    monkeypatch.setattr(transcriber.time, "sleep",
                        lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(transcriber.time, "monotonic", lambda: clock["t"])
    with pytest.raises(transcriber.requests.exceptions.ConnectionError):
        transcriber.download_audio("http://x/1.m4s", str(tmp_path / "t.m4s"),
                                   attempts=10, backoff=1.0, max_seconds=5.0)
    assert clock["n"] <= 4   # 预算内最多几次尝试（非 attempts=10 全跑）


def test_download_audio_dns_failure_fails_fast(tmp_path, monkeypatch):
    """DNS 解析失败（域名不存在）→ 不重试、不退避（非瞬态），立即失败"""
    calls = {"n": 0, "sleeps": []}
    def fake_get(url, headers=None, stream=False, timeout=None):
        calls["n"] += 1
        raise transcriber.requests.exceptions.ConnectionError(
            "HTTPConnectionPool: Max retries exceeded (Caused by "
            "NameResolutionError('Failed to resolve host'))")
    monkeypatch.setattr(transcriber.requests, "get", fake_get)
    monkeypatch.setattr(transcriber.time, "sleep", lambda s: calls["sleeps"].append(s))
    with pytest.raises(transcriber.requests.exceptions.ConnectionError):
        transcriber.download_audio("http://nonexistent-host-xyz.invalid/a.m4s",
                                   str(tmp_path / "d.m4s"), attempts=4)
    assert calls["n"] == 1 and calls["sleeps"] == []


def test_download_audio_resumes_beyond_no_progress_attempts(tmp_path, monkeypatch):
    """有续传进展的失败不计入「连续失败上限」：每次连接只给一段也能最终完成"""
    class Partial:
        status_code = 206
        def __init__(self, ch):
            self.ch = ch
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=1):
            yield self.ch
            raise transcriber.requests.exceptions.ChunkedEncodingError("broken")
    class Final:
        status_code = 206
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=1):
            yield b"Z"
    order = [Partial(b"A"), Partial(b"B"), Final()]
    monkeypatch.setattr(transcriber.requests, "get",
                        lambda *a, **k: order.pop(0))
    monkeypatch.setattr(transcriber.time, "sleep", lambda s: None)
    dest = str(tmp_path / "p.m4s")
    transcriber.download_audio("http://x/a.m4s", dest, attempts=1, backoff=0)
    assert open(dest, "rb").read() == b"ABZ"


def test_download_audio_aborts_trickle_over_budget(tmp_path, monkeypatch):
    """trickle（缓慢但持续有数据）也不得超过时长预算（预算检查在流内逐块进行）"""
    clock = {"t": 0.0, "chunks": 0}
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=1):
            for _ in range(100):
                clock["t"] += 1.0
                clock["chunks"] += 1
                yield b"x" * 10
    monkeypatch.setattr(transcriber.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(transcriber.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(transcriber.time, "sleep", lambda s: None)
    with pytest.raises(Exception):
        transcriber.download_audio("http://x/a.m4s", str(tmp_path / "tr.m4s"),
                                   max_seconds=5.0)
    assert clock["chunks"] <= 7          # 预算 5s 后即中止（不是完整 100 块）


def test_transcribe_collects_stats(monkeypatch):
    """whisper 置信度采集（质量检测用）：avg_logprob / no_speech_ratio / language(_probability)"""
    class Seg:
        def __init__(self, text, lp, nsp):
            self.start, self.end, self.text = 0.0, 1.0, text
            self.avg_logprob, self.no_speech_prob = lp, nsp
    class Info:
        language = "zh"
        language_probability = 0.92
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language=None, vad_filter=None, **kw):
            return [Seg("你好", -0.3, 0.1), Seg("世界", -0.9, 0.7)], Info()
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cpu")
    stats = {}
    lines = transcriber.transcribe("x.m4s", model_size="small", stats=stats)
    assert len(lines) == 2
    assert abs(stats["avg_logprob"] - (-0.6)) < 1e-6
    assert abs(stats["no_speech_ratio"] - 0.5) < 1e-6
    assert stats["language"] == "zh" and stats["language_probability"] == 0.92
    assert stats["segments"] == 2


def test_transcribe_url_passes_stats(monkeypatch):
    """transcribe_url 透传 stats（下载桩）"""
    monkeypatch.setattr(transcriber, "download_audio", lambda url, dest, headers=None: dest)

    def fake_transcribe(path, model_size="medium", **kw):
        if kw.get("stats") is not None:
            kw["stats"]["avg_logprob"] = -0.2
        return [{"start": 0.0, "end": 1.0, "text": "x"}]

    monkeypatch.setattr(transcriber, "transcribe", fake_transcribe)
    stats = {}
    transcriber.transcribe_url("http://x/a.m4s", stats=stats)
    assert stats["avg_logprob"] == -0.2
