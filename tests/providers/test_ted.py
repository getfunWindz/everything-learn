import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from providers.ted import TedAdapter
from tests.contract.harness import run_contract

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "ted")


class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text
    def json(self):
        return json.loads(self.text)


class _FakeSession:
    headers = {}
    def __init__(self, routes): self.routes = routes      # 子串 → (status, text)
    def get(self, url, **kw):
        for key in sorted(self.routes, key=len, reverse=True):   # 最长键优先（避免页面键遮蔽字幕键）
            if key in url:
                code, text = self.routes[key]
                return _Resp(code, text)
        raise AssertionError(f"unexpected URL: {url}")


def _page_html():
    fx = json.load(open(os.path.join(FIX, "talk_2458.json"), encoding="utf-8"))
    return f'<script id="__NEXT_DATA__">{json.dumps(fx, ensure_ascii=False)}</script>'


def _adapter(routes):
    a = TedAdapter(session=_FakeSession(routes))
    ref = a.resolve("https://www.ted.com/talks/tim_urban_inside_the_mind_of_a_master_procrastinator")
    return a, ref


def test_match_urls():
    a = TedAdapter()
    assert a.match("https://www.ted.com/talks/tim_urban_inside_the_mind_of_a_master_procrastinator")
    assert a.match("https://www.ted.com/talks/sir_ken_robinson_do_schools_kill_creativity/transcript?utm_source=x")
    assert not a.match("https://www.youtube.com/watch?v=x")


def test_resolve_strips_suffix():
    ref = TedAdapter().resolve(
        "https://www.ted.com/talks/tim_urban_inside_the_mind_of_a_master_procrastinator/transcript?utm=1")
    assert ref.platform == "ted" and ref.extra["slug"] == "tim_urban_inside_the_mind_of_a_master_procrastinator"
    assert ref.url == "https://www.ted.com/talks/tim_urban_inside_the_mind_of_a_master_procrastinator"


def test_fetch_meta_from_fixture():
    a, ref = _adapter({"talks/tim_urban": (200, _page_html())})
    meta = a.fetch_meta(ref)
    assert meta.title == "Inside the mind of a master procrastinator"
    assert meta.author == "Tim Urban" and meta.duration == 834
    assert meta.published == "2016-03-15"


def test_fetch_media_url_returns_mp4():
    a, ref = _adapter({"talks/tim_urban": (200, _page_html())})
    url = a.fetch_media_url(ref, media="audio")
    assert url and url.endswith(".mp4") and "py.tedcdn.com" in url


def test_contract():
    assert run_contract(TedAdapter()) == []


def test_session_is_throttled():
    from core.throttle import ThrottledSession
    a = TedAdapter()
    assert isinstance(a.session, ThrottledSession) and a.session.platform == "ted"


def test_fetch_content_zh_cn():
    fx = open(os.path.join(FIX, "subtitles_zh_cn.json"), encoding="utf-8").read()
    routes = {"talks/tim_urban": (200, _page_html()), "/lang/zh-cn": (200, fx)}
    a, ref = _adapter(routes)
    res = a.fetch_content(ref)
    assert res.kind == "timeline" and res.status == "ok"
    assert res.label == "字幕(官方 zh-cn)" and len(res.segments) == 200
    assert abs(res.segments[0]["start"] - 0.621) < 1e-6      # 毫秒 → 秒
    assert res.segments[0]["text"] == "上大学那会儿，"


def test_fetch_content_lang_fallback():
    """zh-cn 404 → 回退 en"""
    en = json.dumps({"captions": [{"content": f"line {i}", "startTime": i * 70000, "duration": 60000}
                                  for i in range(12)]})
    routes = {"talks/tim_urban": (200, _page_html()),
              "/lang/zh-cn": (404, '{"error":"Not Found"}'),
              "/lang/zh-tw": (404, '{"error":"Not Found"}'),
              "/lang/en": (200, en)}
    a, ref = _adapter(routes)
    res = a.fetch_content(ref)
    assert res.status == "ok" and res.label == "字幕(官方 en)" and len(res.segments) == 12


def test_fetch_content_all_missing_returns_empty():
    routes = {"talks/tim_urban": (200, _page_html()), "/lang/": (404, '{"error":"Not Found"}')}
    a, ref = _adapter(routes)
    res = a.fetch_content(ref)
    assert res.status == "empty" and "无可用官方字幕" in res.reason


def test_fetch_content_short_is_suspect():
    short = json.dumps({"captions": [{"content": "hi", "startTime": 0, "duration": 1000}]})
    routes = {"talks/tim_urban": (200, _page_html()), "/lang/zh-cn": (200, short)}
    a, ref = _adapter(routes)
    res = a.fetch_content(ref)
    assert res.status == "suspect" and "覆盖不足" in res.reason


def test_fetch_content_lang_alias_zh():
    """--lang zh → 规范化为 zh-cn"""
    fx = open(os.path.join(FIX, "subtitles_zh_cn.json"), encoding="utf-8").read()
    routes = {"talks/tim_urban": (200, _page_html()), "/lang/zh-cn": (200, fx)}
    a, ref = _adapter(routes)
    a.lang = "zh"
    res = a.fetch_content(ref)
    assert res.label == "字幕(官方 zh-cn)"
