import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import pytest
from providers.bilibili import ApiClient, BiliError, VideoInfo, BilibiliAdapter, _length_to_sec

class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        if isinstance(payload, str):
            self.text = payload
        else:
            self.text = json.dumps(payload, ensure_ascii=False)
    def json(self): return json.loads(self.text)
    def raise_for_status(self): pass

class FakeCookies:
    def __init__(self): self._d = {}
    def get(self, k, default=None): return self._d.get(k, default)
    def set(self, k, v, domain=None): self._d[k] = v
    def items(self): return self._d.items()
    def __iter__(self): return iter(self._d)

class FakeSession:
    """handler(path, params) -> dict（API payload）；path 为相对路径或绝对字幕 URL"""
    BASE = "https://api.bilibili.com"
    def __init__(self, handler): self._handler = handler; self.headers = {}; self.cookies = FakeCookies()
    def get(self, path, params=None, timeout=None):
        if path.startswith(self.BASE):
            path = path[len(self.BASE):]
        return FakeResponse(self._handler(path, params or {}))

VIEW_PAYLOAD = {"code": 0, "data": {
    "bvid": "BV1GJ411x7h7", "aid": 170001, "title": "测试视频",
    "owner": {"name": "测试UP"}, "duration": 300, "pubdate": 1600000000,
    "pages": [
        {"cid": 1001, "page": 1, "part": "P1 基础", "duration": 100},
        {"cid": 1002, "page": 2, "part": "P2 进阶", "duration": 200},
    ],
}}

def make_client(view=VIEW_PAYLOAD, subtitles=None, sub_body=None, search=None, playurl=None):
    def handler(path, params):
        if path == "/x/web-interface/nav":
            return {"code": 0, "data": {"wbi_img": {
                "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}
        if path == "/x/web-interface/view": return view
        if path == "/x/player/v2": return {"code": 0, "data": {"subtitle": {"subtitles": subtitles or []}}}
        if path.startswith("https://"): return {"font_size": 0.4, "body": sub_body or []}  # 字幕文件是裸 JSON，无 code 信封
        if path == "/x/web-interface/search/type": return {"code": 0, "data": {"result": search or []}}
        if path == "/x/web-interface/wbi/search/type": return {"code": 0, "data": {"result": search or []}}
        if path == "/x/player/playurl": return {"code": 0, "data": {"dash": {"audio": playurl or []}}}
        raise AssertionError(f"unexpected path: {path}")
    return ApiClient(FakeSession(handler))

def test_video_info_parsing():
    info = make_client().get_video_info(bvid="BV1GJ411x7h7")
    assert info.title == "测试视频" and info.owner == "测试UP"
    assert info.page_count == 2
    assert info.page_by_index(2).cid == 1002 and info.page_by_index(2).part == "P2 进阶"

def test_video_info_stats_parsing():
    """C3：view 接口的 stat（播放/点赞/收藏）与 desc（简介）进入 VideoInfo"""
    payload = {"code": 0, "data": dict(VIEW_PAYLOAD["data"],
                desc="这是一个测试简介",
                stat={"view": 12345, "like": 678, "favorite": 90, "coin": 12, "share": 3, "danmaku": 456})}
    info = make_client(view=payload).get_video_info(bvid="BV1GJ411x7h7")
    assert info.desc == "这是一个测试简介"
    assert info.stat["view"] == 12345 and info.stat["like"] == 678
    assert info.stat.get("favorite") == 90

def test_page_out_of_range():
    client = make_client()
    with pytest.raises(BiliError):
        client.get_video_info(bvid="BV1GJ411x7h7").page_by_index(5)

def test_subtitle_flow():
    subs = [{"lan": "ai-zh", "lan_doc": "中文（自动生成）", "subtitle_url": "//s1.hdslb.com/x.json"}]
    body = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"第{i}句"} for i in range(12)]
    client = make_client(subtitles=subs, sub_body=body)
    lines, provenance = client.fetch_subtitle("BV1GJ411x7h7", 1001)
    assert len(lines) == 12 and lines[0].content == "第0句" and lines[0].start == 0.0
    assert provenance == "auto"   # ai-* 轨 → 自动字幕

def test_fetch_subtitle_returns_lines_and_provenance():
    """取轨道 → 取全文 → (lines, provenance)；人工轨标 human"""
    subs = [{"lan": "zh-CN", "lan_doc": "中文（中国）", "subtitle_url": "//s1.hdslb.com/x.json"}]
    body = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"稳定{i}"} for i in range(12)]
    client = make_client(subtitles=subs, sub_body=body)
    lines, provenance = client.fetch_subtitle("BV1GJ411x7h7", 1001)
    assert len(lines) == 12 and provenance == "human"

def test_provenance_ai_lan_is_auto():
    """lan 以 ai- 开头 → 自动字幕 auto"""
    assert ApiClient._provenance_of({"lan": "ai-zh", "lan_doc": "中文"}) == "auto"
    assert ApiClient._provenance_of({"lan": "ai-en", "lan_doc": "English"}) == "auto"

def test_provenance_lan_doc_auto_is_auto():
    """lan_doc 含「自动生成」 → 自动字幕 auto（平台轨道命名的兼容判定）"""
    assert ApiClient._provenance_of({"lan": "zh", "lan_doc": "中文（自动生成）"}) == "auto"

def test_provenance_human_track():
    assert ApiClient._provenance_of({"lan": "zh-CN", "lan_doc": "中文（中国）"}) == "human"
    assert ApiClient._provenance_of({}) == "human"

def test_fetch_subtitle_single_fetch_no_double_grab():
    """旧双抓一致性防线退役：一次 fetch_subtitle 只抓一次字幕文件"""
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"}]
    body = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"稳定{i}"} for i in range(12)]
    calls = {"n": 0}
    def handler(path, params):
        if path == "/x/player/v2":
            return {"code": 0, "data": {"subtitle": {"subtitles": subs}}}
        if path.startswith("https://") and "hdslb" in path:
            calls["n"] += 1
            return {"font_size": 0.4, "body": body}
        raise AssertionError(path)
    client = ApiClient(FakeSession(handler), warm=False)
    lines, provenance = client.fetch_subtitle("BV1GJ411x7h7", 1001)
    assert len(lines) == 12 and provenance == "auto" and calls["n"] == 1

def test_fetch_subtitle_empty_body_returns_none():
    """轨道存在但内容为空 → 视为无字幕 ([], none)，交上层降级 Whisper"""
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"}]
    client = make_client(subtitles=subs, sub_body=[])
    assert client.fetch_subtitle("BV1GJ411x7h7", 1001) == ([], "none")

def test_fetch_subtitle_duration_param_accepted():
    """duration 参数仅为调用兼容保留（旧覆盖度防线退役后不再参与判定）"""
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"}]
    body = [{"from": 0.0, "to": 1.0, "content": "只有一句"}]
    client = make_client(subtitles=subs, sub_body=body)
    lines, provenance = client.fetch_subtitle("BV1GJ411x7h7", 1001, duration=900)
    assert [l.content for l in lines] == ["只有一句"] and provenance == "auto"

def test_no_subtitle_returns_none():
    client = make_client(subtitles=[])
    assert client.fetch_subtitle("BV1GJ411x7h7", 1001) == ([], "none")

def test_search_cleans_html():
    results = [{"bvid": "BV1xx", "title": "Python<em class=\"keyword\">教程</em>", "author": "某UP", "duration": "10:00"}]
    client = make_client(search=results)
    client.session.cookies.set("SESSDATA", "x")  # 搜索需登录态
    out = client.search("Python 教程")
    assert out[0]["title"] == "Python教程" and out[0]["bvid"] == "BV1xx"

def test_audio_url_picks_highest_bandwidth():
    audios = [{"bandwidth": 100, "baseUrl": "http://low"}, {"bandwidth": 900, "baseUrl": "http://high"}]
    url = make_client(playurl=audios).get_audio_url("BV1GJ411x7h7", 1001)
    assert url == "http://high"

def test_video_url_picks_highest_bandwidth():
    def handler(path, params):
        if path == "/x/player/playurl":
            return {"code": 0, "data": {"dash": {"video": [{"bandwidth": 100, "baseUrl": "http://lowv"}, {"bandwidth": 800, "baseUrl": "http://highv"}]}}}
        raise AssertionError(path)
    client = ApiClient(FakeSession(handler))
    assert client.get_video_url("BV1GJ411x7h7", 1001) == "http://highv"

def test_video_url_missing_raises():
    client = make_client(playurl=[])
    with pytest.raises(BiliError):
        client.get_video_url("BV1GJ411x7h7", 1001)

def test_pick_subtitle_by_lang():
    from providers.bilibili import ApiClient as A
    subs = [{"lan": "ai-zh", "lan_doc": "中文"}, {"lan": "ai-ja", "lan_doc": "日本語"}, {"lan": "ai-en", "lan_doc": "English"}]
    assert A._pick_subtitle(subs, "ja")["lan"] == "ai-ja"
    assert A._pick_subtitle(subs, "en")["lan"] == "ai-en"
    assert A._pick_subtitle(subs, "zh")["lan"] == "ai-zh"

def test_pick_subtitle_lang_fallback():
    """指定语言不存在 → 回退默认逻辑（中文优先）"""
    from providers.bilibili import ApiClient as A
    subs = [{"lan": "ai-ja", "lan_doc": "日本語"}, {"lan": "ai-en", "lan_doc": "English"}]
    assert A._pick_subtitle(subs, "zh")["lan"] == "ai-ja"  # 无中文 → 任意第一个

def test_subtitle_lang_parameter_flow():
    """fetch_subtitle 的 lang 参数透传到字幕选择（provenance 随所选轨道）"""
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"},
            {"lan": "ai-ja", "lan_doc": "日本語", "subtitle_url": "//s2.hdslb.com/ja.json"}]
    body_zh = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"中{i}"} for i in range(12)]
    body_ja = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"日{i}"} for i in range(12)]
    def handler(path, params):
        if path == "/x/web-interface/nav":
            return {"code": 0, "data": {"wbi_img": {"img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png", "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}
        if path == "/x/player/v2": return {"code": 0, "data": {"subtitle": {"subtitles": subs}}}
        if "ja.json" in path: return {"font_size": 0.4, "body": body_ja}
        if "x.json" in path: return {"font_size": 0.4, "body": body_zh}
        raise AssertionError(path)
    client = ApiClient(FakeSession(handler))
    lines, provenance = client.fetch_subtitle("BV1GJ411x7h7", 1001, lang="ja")
    assert lines[0].content == "日0"  # 选中了日文字幕
    assert provenance == "auto"


def test_api_error_raises_bili_error():
    client = ApiClient(FakeSession(lambda p, q: {"code": -404, "message": "视频不存在"}))
    with pytest.raises(BiliError):
        client.get_video_info(bvid="BVxxx")


def test_video_url_lowest_picks_min_bandwidth():
    """归属复检用最低码率流（省带宽）"""
    def handler(path, params):
        if path == "/x/player/playurl":
            return {"code": 0, "data": {"dash": {"video": [
                {"bandwidth": 100, "baseUrl": "http://lowv"},
                {"bandwidth": 800, "baseUrl": "http://highv"}]}}}
        raise AssertionError(path)
    client = ApiClient(FakeSession(handler))
    assert client.get_video_url("BV1GJ411x7h7", 1001, lowest=True) == "http://lowv"
    assert client.get_video_url("BV1GJ411x7h7", 1001) == "http://highv"


# ---------- 限流/风控重试（指数退避 + 412 刷新 cookie） ----------

class RetrySession:
    """可编排响应序列的 Session；记录调用次数与主页预热次数"""
    def __init__(self, responses):
        self.responses, self.calls, self.homepage_calls = list(responses), 0, 0
        self.headers, self.cookies = {}, FakeCookies()
    def get(self, path, params=None, timeout=None):
        if "www.bilibili.com" in path and "api.bilibili" not in path:
            self.homepage_calls += 1
            return FakeResponse({"ok": True})
        self.calls += 1
        status, payload = self.responses.pop(0)
        return FakeResponse(payload, status_code=status)

def _no_sleep(monkeypatch):
    from providers import bilibili as ac
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    monkeypatch.setattr(ac.random, "uniform", lambda a, b: 0.0)

def test_get_retries_on_412_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    sess = RetrySession([(412, "风控"), (412, "风控"), (200, {"code": 0, "data": {"ok": 1}})])
    client = ApiClient(sess, warm=False, max_retries=3, retry_base_delay=0.01)
    assert client._get("/x/player/v2", {}) == {"ok": 1}
    assert sess.calls == 3 and sess.homepage_calls >= 1  # 412 后刷新 cookie

def test_get_gives_up_after_retries(monkeypatch):
    _no_sleep(monkeypatch)
    sess = RetrySession([(412, "风控")] * 5)
    client = ApiClient(sess, warm=False, max_retries=4, retry_base_delay=0.01)
    with pytest.raises(BiliError) as e:
        client._get("/x/player/v2", {})
    assert e.value.code == 412 and sess.calls == 5  # 1 次 + 4 重试（D4 决策）

def test_get_business_error_not_retried(monkeypatch):
    _no_sleep(monkeypatch)
    sess = RetrySession([(200, {"code": -101, "message": "未登录"})])
    client = ApiClient(sess, warm=False)
    with pytest.raises(BiliError):
        client._get("/x/web-interface/nav", {})
    assert sess.calls == 1  # 业务错误不重试

def test_get_html_body_retried(monkeypatch):
    _no_sleep(monkeypatch)
    sess = RetrySession([(200, "<html>风控</html>"), (200, {"code": 0, "data": {"ok": 2}})])
    client = ApiClient(sess, warm=False, retry_base_delay=0.01)
    assert client._get("/x/player/v2", {}) == {"ok": 2}
    assert sess.calls == 2

def test_config_retry_defaults(tmp_path, monkeypatch):
    from core import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    c = cfg.load_config()
    assert c["api_retries"] == 4 and c["api_retry_base"] == 1.5  # D4：默认 4 次

def test_non_json_response_friendly_error():
    client = ApiClient(FakeSession(lambda p, q: "<!DOCTYPE html><html>风控页</html>"))
    with pytest.raises(BiliError) as e:
        client.get_video_info(bvid="BV1GJ411x7h7")
    assert "JSON" in str(e.value)

def test_warm_tolerates_unexpected_path():
    """FakeSession 无 cookies，warm 访问主页的异常应被静默吞掉"""
    client = make_client()
    assert client.get_video_info(bvid="BV1GJ411x7h7").bvid == "BV1GJ411x7h7"

def test_check_login_when_logged_in():
    def handler(path, params):
        if path == "/x/web-interface/nav":
            return {"code": 0, "data": {"wbi_img": {"img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png", "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}
        raise AssertionError(path)
    client = ApiClient(FakeSession(handler))
    assert client.check_login() is True

def test_check_login_detects_expired():
    def handler(path, params):
        if path == "/x/web-interface/nav":
            return {"code": -101, "message": "账号未登录"}
        raise AssertionError(path)
    client = ApiClient(FakeSession(handler))
    assert client.check_login() is False

def test_mixin_key_matches_documented_vector():
    """bilibili 官方文档已知向量：img_key+sub_key → mixin_key"""
    from providers.bilibili import get_mixin_key
    mixin = get_mixin_key("7cd084941338484aae1ad9425b84077c",
                          "4932caff0ff746eab6f01bf08b70ac45")
    assert mixin == "ea1db124af3c7062474693fa704f4ff8"

def test_mixin_key_must_be_32_chars():
    from providers.bilibili import get_mixin_key
    mixin = get_mixin_key("a" * 32, "b" * 32)
    assert len(mixin) == 32

def test_load_cookie_from_env(monkeypatch):
    from providers.bilibili import load_cookie
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=abc123")
    c = ApiClient(FakeSession(lambda p, q: {"code": 0, "data": {}}))
    assert load_cookie(c) is True
    assert c.session.cookies.get("SESSDATA") == "abc123"

def test_init_auto_loads_cookie(monkeypatch):
    """ApiClient 初始化时自动注入环境变量 cookie（搜索/字幕均受益）"""
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=auto123")
    c = ApiClient(FakeSession(lambda p, q: {"code": 0, "data": {}}))
    assert c.session.cookies.get("SESSDATA") == "auto123"

def test_search_without_cookie_raises_guidance(monkeypatch):
    from providers import bilibili as api_client
    monkeypatch.setattr(api_client, "load_cookie", lambda client: False)  # 隔离真实 .bili_cookie 文件
    monkeypatch.delenv("BILI_COOKIE", raising=False)
    client = make_client()
    with pytest.raises(BiliError) as e:
        client.search("Python 入门")
    assert "cookie" in str(e.value).lower() or "SESSDATA" in str(e.value)


def test_get_toview(monkeypatch):
    c = ApiClient()
    monkeypatch.setattr(ApiClient, "_get",
                        lambda self, path, params=None, envelope=True:
                        {"list": [{"bvid": "BV1", "title": "稍后看", "duration": 300,
                                   "owner": {"name": "UP"}}]})
    items = c.get_toview()
    assert items[0]["bvid"] == "BV1" and items[0]["owner"] == "UP"


def test_get_history_pagination(monkeypatch):
    c = ApiClient()
    pages = [[{"bvid": f"BV{i}", "title": f"历史{i}", "duration": 60, "view_at": 1000 - i}
              for i in range(1, 4)],
             [{"bvid": "BV9", "title": "历史9", "duration": 60, "view_at": 900}]]
    calls = []
    monkeypatch.setattr(ApiClient, "_get",
                        lambda self, path, params=None, envelope=True:
                        calls.append(params) or pages.pop(0))
    items = c.get_history(limit=10, ps=3)
    assert [i["bvid"] for i in items] == ["BV1", "BV2", "BV3", "BV9"]
    assert calls[1]["max"] == 997          # 第二页用末条 view_at 作游标


def test_get_followings(monkeypatch):
    c = ApiClient()
    monkeypatch.setattr(ApiClient, "_get",
                        lambda self, path, params=None, envelope=True:
                        {"list": [{"mid": 1, "uname": "小明", "sign": "签名"}]}
                        if "followings" in path else {"mid": 42})
    items = c.get_followings(limit=10, ps=50)
    assert items[0]["uname"] == "小明"


def test_length_to_sec():
    assert _length_to_sec("12:34") == 754
    assert _length_to_sec("1:02:03") == 3723
    assert _length_to_sec("") == 0


def test_resolve_space_page():
    a = BilibiliAdapter()
    ref = a.resolve("https://space.bilibili.com/946974/video")
    assert ref.extra["kind"] == "space" and ref.extra["mid"] == "946974"


def test_get_space_videos_signs(monkeypatch):
    c = ApiClient()
    monkeypatch.setattr("providers.bilibili._get_wbi_keys",
                        lambda session, timeout: ("i" * 64, "s" * 64))
    captured = {}
    monkeypatch.setattr(ApiClient, "_get",
                        lambda self, path, params=None, envelope=True:
                        captured.update(params or {})
                        or {"list": {"vlist": []}, "page": {"count": 0, "ps": 5}})
    c.get_space_videos(946974, ps=5, pn=2)
    assert captured["mid"] == 946974 and captured["pn"] == 2
    assert "w_rid" in captured and "wts" in captured


def test_list_items_space(monkeypatch):
    def _v(i):
        return {"bvid": f"BV{i}", "aid": i, "title": f"T{i}", "length": "12:34",
                "play": i * 10, "description": "d" + str(i), "author": "UP", "created": 1700000000}

    def fake(self, mid, ps=30, pn=1, order="pubdate"):
        return {"videos": [_v(i) for i in range((pn - 1) * 30, min(pn * 30, 31))], "total": 31}

    monkeypatch.setattr(ApiClient, "get_space_videos", fake)
    a = BilibiliAdapter(client=ApiClient())
    refs = a.list_items(a.resolve("https://space.bilibili.com/946974"))
    assert len(refs) == 31
    assert refs[0].item_id == "BV0" and refs[0].duration == 754 and refs[0].sub_id == "1"
    assert refs[0].extra["owner"] == "UP" and refs[0].extra["view"] == 0
    assert refs[30].item_id == "BV30"


def test_fetch_subtitle_human_preferred_over_ai():
    """人工优先选轨：同时存在 ai-zh 与 zh-CN 时选人工轨，provenance=human"""
    subs = [{"lan": "ai-zh", "lan_doc": "中文（自动生成）", "subtitle_url": "//s1.hdslb.com/ai.json"},
            {"lan": "zh-CN", "lan_doc": "中文（中国）", "subtitle_url": "//s1.hdslb.com/human.json"}]
    def handler(path, params):
        if path == "/x/player/v2":
            return {"code": 0, "data": {"subtitle": {"subtitles": subs}}}
        if "human.json" in path:
            return {"body": [{"from": 0.0, "to": 1.0, "content": "人工内容"}]}
        if "ai.json" in path:
            return {"body": [{"from": 0.0, "to": 1.0, "content": "AI内容"}]}
        raise AssertionError(path)
    client = ApiClient(FakeSession(handler), warm=False)
    lines, provenance = client.fetch_subtitle("BV1GJ411x7h7", 1001)
    assert [l.content for l in lines] == ["人工内容"] and provenance == "human"


def test_fetch_subtitle_track_without_url_returns_none():
    """轨道缺 subtitle_url（接口偶发）→ 视为无字幕 ([], none)"""
    subs = [{"lan": "zh-CN", "lan_doc": "中文（中国）", "subtitle_url": ""}]
    client = make_client(subtitles=subs)
    assert client.fetch_subtitle("BV1GJ411x7h7", 1001) == ([], "none")


def test_fetch_subtitle_lang_fallback_provenance_follows_chosen_track():
    """指定 lang 不存在 → 回退默认选轨；provenance 以实际所选轨道为准"""
    subs = [{"lan": "ai-en", "lan_doc": "English", "subtitle_url": "//s1.hdslb.com/en.json"}]
    body = [{"from": 0.0, "to": 1.0, "content": "only english"}]
    client = make_client(subtitles=subs, sub_body=body)
    lines, provenance = client.fetch_subtitle("BV1GJ411x7h7", 1001, lang="zh")
    assert lines and provenance == "auto"   # 无中文 → 回退 ai-en
