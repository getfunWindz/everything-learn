import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import json

from providers.zhihu import ZhihuAdapter, load_cookie, parse_initial_data
from tests.contract.harness import run_contract

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "zhihu")


def _fixture(fname):
    return json.load(open(os.path.join(FIX, fname), encoding="utf-8"))


def _page_html(fname):
    return f'<script id="js-initialData">{json.dumps(_fixture(fname), ensure_ascii=False)}</script>'


def test_match_urls():
    a = ZhihuAdapter()
    assert a.match("https://zhuanlan.zhihu.com/p/62628781")
    assert a.match("https://www.zhihu.com/question/19550224/answer/12345678")
    assert a.match("https://www.zhihu.com/question/19550224")
    assert not a.match("https://www.bilibili.com/video/BV1xx")
    assert not a.match("")


def test_resolve_article():
    ref = ZhihuAdapter().resolve("https://zhuanlan.zhihu.com/p/62628781")
    assert ref.platform == "zhihu" and ref.extra["kind"] == "article"
    assert ref.extra["article_id"] == "62628781"
    assert ref.item_id == "article-62628781"


def test_resolve_answer():
    ref = ZhihuAdapter().resolve("https://www.zhihu.com/question/19550224/answer/12345678")
    assert ref.extra["kind"] == "answer"
    assert ref.extra["question_id"] == "19550224" and ref.extra["answer_id"] == "12345678"


def test_resolve_question():
    ref = ZhihuAdapter().resolve("https://www.zhihu.com/question/19550224")
    assert ref.extra["kind"] == "question" and ref.extra["question_id"] == "19550224"


def test_contract():
    assert run_contract(ZhihuAdapter()) == []


def test_parse_initial_data():
    html = ('<html><script id="js-initialData" type="text/json">'
            '{"initialState":{"entities":{"articles":{}}}}</script></html>')
    assert parse_initial_data(html)["initialState"]["entities"]["articles"] == {}


def test_parse_initial_data_missing():
    try:
        parse_initial_data("<html>风控页</html>")
        assert False
    except ValueError:
        pass


# ---------------- fixture 驱动（真实 SSR 样本，Task 0 取样） ----------------


def test_article_fetch_content(monkeypatch):
    a = ZhihuAdapter(session=object())  # _get 被替换，session 不会被用到
    monkeypatch.setattr(ZhihuAdapter, "_get", lambda self, url: _page_html("article.json"))
    ref = a.resolve("https://zhuanlan.zhihu.com/p/62628781")
    res = a.fetch_content(ref)
    assert res.kind == "document" and res.status == "ok" and res.label == "正文"
    assert len(res.blocks) >= 5
    assert any("前言" in (b["heading"] or "") for b in res.blocks)
    assert all(isinstance(u, str) and u.startswith("https://") for u in res.images)
    meta = a.fetch_meta(ref)
    assert meta.title and meta.author and meta.published
    assert meta.stats.get("voteup") is not None


def test_question_list_items(monkeypatch):
    a = ZhihuAdapter(session=object())
    monkeypatch.setattr(ZhihuAdapter, "_get", lambda self, url: _page_html("question.json"))
    ref = a.resolve("https://www.zhihu.com/question/363128773")
    items = a.list_items(ref)
    answers = [i for i in items if i is not ref]
    assert answers and all(i.extra["kind"] == "answer" for i in answers)
    assert [i.sub_id for i in answers] == [str(n) for n in range(1, len(answers) + 1)]
    assert all(i.extra.get("question_title") == "PMP应该如何备考？" for i in answers)


def test_standalone_answer_fetch_content(monkeypatch):
    """单条回答链接（非问题展开）：answer id 直接 resolve → document"""
    data = _fixture("question.json")
    aid = list(data["initialState"]["entities"]["answers"].keys())[0]
    a = ZhihuAdapter(session=object())
    monkeypatch.setattr(ZhihuAdapter, "_get", lambda self, url: _page_html("answer.json"))
    ref = a.resolve(f"https://www.zhihu.com/question/363128773/answer/{aid}")
    res = a.fetch_content(ref)
    assert res.kind == "document" and res.blocks
    meta = a.fetch_meta(ref)
    assert meta.page_part.startswith("回答：") and meta.author


def test_fetch_content_failure_is_readable(monkeypatch):
    """风控/无实体 → empty + 可读 reason（不抛裸异常）"""
    from providers.zhihu import ZhihuError

    def boom(self, url):
        raise ZhihuError("知乎风控挑战：cookie 缺失/失效")
    monkeypatch.setattr(ZhihuAdapter, "_get", boom)
    ref = ZhihuAdapter().resolve("https://zhuanlan.zhihu.com/p/62628781")
    res = ZhihuAdapter(session=object()).fetch_content(ref)
    assert res.status == "empty" and "cookie" in res.reason
