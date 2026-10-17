import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import pytest
from core.adapter import SearchNeeded
from providers.wechat_mp import (WeChatMpAdapter, WeChatError,
                                 parse_sogou_results, parse_meta, extract_article,
                                 parse_album, strip_boilerplate)
from tests.contract.harness import run_contract


def test_match_short_and_long():
    a = WeChatMpAdapter(session=object())
    assert a.match("https://mp.weixin.qq.com/s/AbCdEf1234567890_-abc")
    assert a.match("https://mp.weixin.qq.com/s?__biz=MzI3MjY1ODI2Ng==&mid=2247484162&idx=1&sn=abc123")
    assert a.match("http://mp.weixin.qq.com/s/AbCdEf1234567890_-abc")
    assert not a.match("https://mp.weixin.qq.com/")
    assert a.match("https://mp.weixin.qq.com/mp/appmsgalbum?__biz=MzI3&action=getalbum&album_id=9")
    assert not a.match("https://mp.weixin.qq.com/mp/other")
    assert not a.match("https://example.com/s/AbCdEf1234567890_-abc")


def test_resolve_album():
    a = WeChatMpAdapter(session=object())
    ref = a.resolve("https://mp.weixin.qq.com/mp/appmsgalbum?__biz=MzI3&action=getalbum&album_id=9")
    assert ref.extra["kind"] == "album" and ref.extra["album_id"] == "9"


def test_parse_album_shapes():
    single = {"base_resp": {"ret": 0}, "getalbum_resp": {
        "base_info": {"title": "tcp", "nickname": "午夜抵达", "article_count": "1"},
        "article_list": {"title": "A", "url": "http://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1",
                         "msgid": "1", "itemidx": "1"},
        "continue_flag": 0}}
    d = parse_album(single)
    assert d["title"] == "tcp" and d["author"] == "午夜抵达" and len(d["articles"]) == 1
    assert d["articles"][0]["url"].startswith("https://")
    multi = {"getalbum_resp": {
        "base_info": {"article_count": "2"},
        "article_list": [{"title": "A", "url": "http://x/s?__biz=b&mid=1&idx=1",
                          "msgid": "1", "itemidx": "1"},
                         {"title": "B", "url": "http://x/s?__biz=b&mid=2&idx=1",
                          "msgid": "2", "itemidx": "1"}],
        "continue_flag": 1}}
    d2 = parse_album(multi)
    assert len(d2["articles"]) == 2 and d2["continued"] and d2["last"]["msgid"] == "2"


def test_list_items_album_pagination(monkeypatch):
    a = WeChatMpAdapter(session=object())
    pages = [
        {"getalbum_resp": {"base_info": {"title": "T", "nickname": "A", "article_count": "3"},
                           "article_list": [{"title": "A1", "url": "http://mp.weixin.qq.com/s?__biz=b&mid=1&idx=1", "msgid": "1", "itemidx": "1"}],
                           "continue_flag": 1}},
        {"getalbum_resp": {"base_info": {"title": "T", "nickname": "A", "article_count": "3"},
                           "article_list": [{"title": "A2", "url": "http://mp.weixin.qq.com/s?__biz=b&mid=2&idx=1", "msgid": "2", "itemidx": "1"}],
                           "continue_flag": 0}},
    ]
    calls = []
    monkeypatch.setattr(WeChatMpAdapter, "_get_json",
                        lambda self, url: calls.append(url) or pages.pop(0))
    ref = a.resolve("https://mp.weixin.qq.com/mp/appmsgalbum?__biz=b&action=getalbum&album_id=9")
    refs = a.list_items(ref)
    assert len(refs) == 2 and refs[0].sub_id == "1" and refs[0].title == "A1"
    assert "begin_msgid=1" in calls[1]
    meta = a.fetch_root_meta(ref, refs)
    assert meta.title == "T" and meta.author == "A"


def test_resolve_short_link():
    a = WeChatMpAdapter(session=object())
    ref = a.resolve("https://mp.weixin.qq.com/s/AbCdEf1234567890_-abc")
    assert ref.platform == "wechat_mp"
    assert ref.item_id == "wx-AbCdEf1234567890_-abc"
    assert ref.url == "https://mp.weixin.qq.com/s/AbCdEf1234567890_-abc"
    assert ref.extra["kind"] == "article"


def test_resolve_long_link():
    a = WeChatMpAdapter(session=object())
    ref = a.resolve(
        "https://mp.weixin.qq.com/s?__biz=MzI3MjY1ODI2Ng==&mid=2247484162&idx=1&sn=abc123")
    assert ref.item_id == "wx-MzI3MjY1ODI2Ng_2247484162_1"
    assert ref.extra["mid"] == "2247484162"


def test_resolve_name_needs_search():
    a = WeChatMpAdapter(session=object())
    with pytest.raises(SearchNeeded):
        a.resolve("一篇公众号文章")


def test_list_items_and_contract():
    a = WeChatMpAdapter(session=object())
    ref = a.resolve("https://mp.weixin.qq.com/s/AbCdEf1234567890_-abc")
    assert a.list_items(ref) == [ref]
    assert run_contract(a) == []


FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "wechat_mp")


def _fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, text):
        self._text = text
        self.headers = {}

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        return _Resp(self._text)


def test_fetch_content_from_fixture():
    a = WeChatMpAdapter(session=_FakeSession(_fx("article.html")))
    ref = a.resolve("https://mp.weixin.qq.com/s/-cThzr5N2w3IEYYf-duCDA")
    res = a.fetch_content(ref)
    assert res.kind == "document" and res.status == "ok"
    assert any("Cannot assign" in b["text"] for b in res.blocks)
    assert [b["heading"] for b in res.blocks if b["heading"]] == ["一、问题现象", "二、排查过程"]
    assert res.images == [
        "https://mmbiz.qpic.cn/mmbiz_png/9aKOAbMEQ5qcotFGPMS0qazbMibmnluxObCrSQCw3yVmnEOjcvRrSq95NbiaesvqfWKccRx4KOicUIpYtxP5MLWcw/640"]


def test_ts_to_cn_date_forced_timezone():
    """微信/搜狗时间戳一律按中国时区（UTC+8）转日期；不受机器时区影响（CI=UTC 也曾翻车）"""
    from providers.wechat_mp import _ts_to_cn_date
    assert _ts_to_cn_date(1630882800) == "2021-09-06"   # UTC 下为 09-05
    assert _ts_to_cn_date("bad") == ""


def test_fetch_meta_from_fixture():
    a = WeChatMpAdapter(session=_FakeSession(_fx("article.html")))
    ref = a.resolve("https://mp.weixin.qq.com/s/-cThzr5N2w3IEYYf-duCDA")
    meta = a.fetch_meta(ref)
    assert meta.title == "这个 TCP 问题你得懂：Cannot assign requested address"
    assert meta.author == "AlwaysBeta"
    assert meta.published == "2021-09-06"


def test_error_page_readable():
    a = WeChatMpAdapter(session=_FakeSession(_fx("error_param.html")))
    ref = a.resolve("https://mp.weixin.qq.com/s/aaaaaaaaaaaaaaaaaaaaaa")
    res = a.fetch_content(ref)
    assert res.status == "empty" and "参数错误" in res.reason


def test_parse_sogou_results_from_fixture():
    hits = parse_sogou_results(_fx("sogou_search.html"), limit=5)
    assert len(hits) == 2
    assert hits[0]["title"] == "专题 | 基于标准数据集的深度学习目标检测算法"
    assert hits[0]["account"] == "信息通信技术与政策"
    assert hits[0]["date"] == "2026-08-16"
    assert hits[0]["link"] == "https://weixin.sogou.com/link?url=AAAA"
    assert hits[1]["account"] == "算法小站"
    assert hits[1]["date"] == "2021-09-06"


def test_parse_sogou_captcha_readable():
    with pytest.raises(WeChatError) as e:
        parse_sogou_results("<html>请输入验证码</html>")
    assert "验证码" in str(e.value)


def test_search_articles_uses_session():
    a = WeChatMpAdapter(session=_FakeSession(_fx("sogou_search.html")))
    hits = a.search_articles("深度学习", limit=2)
    assert len(hits) == 2 and hits[0]["account"] == "信息通信技术与政策"


def test_resolve_sogou_link_js_concat():
    a = WeChatMpAdapter(session=_FakeSession(_fx("sogou_link.html")))
    url = a.resolve_sogou_link("https://weixin.sogou.com/link?url=AAAA")
    assert url == ("https://mp.weixin.qq.com/s?src=11&timestamp=1791540606"
                   "&ver=7016&signature=FAKE&new=1")


def test_resolve_sogou_link_expired_readable():
    a = WeChatMpAdapter(session=_FakeSession("<html>系统出错</html>"))
    with pytest.raises(WeChatError) as e:
        a.resolve_sogou_link("https://weixin.sogou.com/link?url=AAAA")
    assert "同一会话" in str(e.value)


def test_html_fetch_cached_within_run():
    class CountingSession(_FakeSession):
        def __init__(self, text):
            super().__init__(text)
            self.calls = []

        def get(self, *a, **kw):
            self.calls.append(a[0])
            return super().get(*a, **kw)

    s = CountingSession(_fx("article.html"))
    a = WeChatMpAdapter(session=s)
    ref = a.resolve("https://mp.weixin.qq.com/s/-cThzr5N2w3IEYYf-duCDA")
    a.fetch_content(ref)
    a.fetch_meta(ref)
    assert s.calls == [ref.url]


def test_match_signed_sogou_url_and_resolve():
    signed = ("https://mp.weixin.qq.com/s?src=11&timestamp=1791540606&ver=7016"
              "&signature=FAKE&new=1")
    a = WeChatMpAdapter(session=object())
    assert a.match(signed)
    ref = a.resolve(signed)
    assert ref.item_id.startswith("wx-signed-")
    assert ref.extra["referer"] == "https://weixin.sogou.com/"


def test_signed_url_uses_sogou_referer():
    class HeaderSession(_FakeSession):
        def __init__(self, text):
            super().__init__(text)
            self.headers_seen = []

        def get(self, url, headers=None, timeout=None, allow_redirects=True):
            self.headers_seen.append(dict(headers or {}))
            return super().get(url, headers=headers, timeout=timeout,
                               allow_redirects=allow_redirects)

    s = HeaderSession(_fx("article.html"))
    a = WeChatMpAdapter(session=s)
    ref = a.resolve("https://mp.weixin.qq.com/s?src=11&timestamp=1&ver=1&signature=FAKE&new=1")
    res = a.fetch_content(ref)
    assert res.status == "ok"
    assert s.headers_seen[0]["Referer"] == "https://weixin.sogou.com/"


def test_strip_boilerplate_removes_ui_noise():
    """实测：正文混入「预览时标签不可点 / 微信扫一扫 / <div」等模板噪声"""
    blocks = [{"heading": "", "text": "正文第一段"},
              {"heading": "", "text": "预览时标签不可点"},
              {"heading": "", "text": "微信扫一扫\n关注该公众号"},
              {"heading": "", "text": "阅读原文"},
              {"heading": "", "text": "<div class=x"},
              {"heading": "", "text": "正文第二段"}]
    out = strip_boilerplate(blocks)
    assert [b["text"] for b in out] == ["正文第一段", "正文第二段"]


def test_strip_boilerplate_multiline_block():
    """实测：噪声多条挤在同一块里（预览/阅读原文/扫一扫…）"""
    blocks = [{"heading": "", "text": "正文段\n预览时标签不可点\n阅读原文\n微信扫一扫\n<div\n分析"}]
    out = strip_boilerplate(blocks)
    assert out and out[0]["text"] == "正文段"


def test_wechat_search_fingerprint_detects_drift(tmp_path, monkeypatch):
    """搜狗结果顺序漂移：指纹不同 → 抓取前必须要求复核"""
    import providers.wechat_mp as w
    monkeypatch.setattr(w, "_SEARCH_FP_DIR", str(tmp_path))
    a = [{"title": "A", "account": "X"}, {"title": "B", "account": "Y"}]
    b = [{"title": "B", "account": "Y"}, {"title": "A", "account": "X"}]
    w.save_search_fingerprint("DeepSeek", a)
    prev = w.load_search_fingerprint("DeepSeek")
    assert prev and w.search_fingerprint(a) == prev["fp"]
    assert w.search_fingerprint(b) != prev["fp"]
