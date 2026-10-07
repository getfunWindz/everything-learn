"""知乎适配器（document 形态）：专栏文章 / 回答 / 问题（首屏回答列表）。

鉴权：scripts/.zhihu_cookie 或 config.auth.zhihu.cookies_file（用户浏览器导出，
可用 scripts/cookie_export.py 一键导出）；UA 可用 config.auth.zhihu.user_agent 对齐。
风控：403+zh-zse-ck / 200+「安全验证」→ 抛出可读异常（不裸奔）。
尽调结论见 docs/plans/2026-10-08-m3-zhihu.md（v4 API 需 x-zse-96 签名，本期不做）。
"""
import json
import os
import re

import requests

from core.adapter import SearchNeeded
from core.htmlblocks import html_to_blocks
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.retry import retry_with_backoff

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_ARTICLE_RE = re.compile(r"zhuanlan\.zhihu\.com/p/(\d+)")
_ANSWER_RE = re.compile(r"(?:www\.)?zhihu\.com/question/(\d+)/answer/(\d+)")
_QUESTION_RE = re.compile(r"(?:www\.)?zhihu\.com/question/(\d+)(?:[/?#]|$)")
_INITIAL_RE = re.compile(r'<script id="js-initialData"[^>]*>(.*?)</script>', re.S)
_COOKIE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".zhihu_cookie")
_ANSWER_LIMIT = 5  # SSR 首屏预载的回答条数


class ZhihuError(ValueError):
    """cookie 缺失/失效 / 风控拦截（可读异常）"""


def _auth_cfg() -> dict:
    try:
        from core import config as core_config
        return (core_config.load_config().get("auth") or {}).get("zhihu") or {}
    except Exception:
        return {}


def _cookie_path() -> str:
    return _auth_cfg().get("cookies_file") or _COOKIE_FILE


def _ua() -> str:
    """UA 可配置（auth.zhihu.user_agent）：__zse_ck 与生成它的 UA 绑定，预检失败时对齐浏览器 UA"""
    return _auth_cfg().get("user_agent") or _UA


def load_cookie(session: requests.Session) -> bool:
    """从环境变量 ZHIHU_COOKIE 或 cookie 文件注入；有则 True"""
    raw = os.environ.get("ZHIHU_COOKIE", "") or ""
    path = _cookie_path()
    if not raw and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    if not raw:
        return False
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            session.cookies.set(k.strip(), v.strip(), domain=".zhihu.com")
    return True


def parse_initial_data(html: str) -> dict:
    """从 SSR HTML 提取 js-initialData JSON"""
    m = _INITIAL_RE.search(html or "")
    if not m:
        raise ZhihuError("页面不是知乎内容页（缺少 js-initialData；可能被风控拦截）")
    return json.loads(m.group(1))


def _ts(v) -> str:
    """知乎时间戳（秒/毫秒）→ YYYY-MM-DD"""
    try:
        from datetime import datetime
        n = int(v)
        if n > 10 ** 12:
            n //= 1000
        return datetime.fromtimestamp(n).strftime("%Y-%m-%d")
    except Exception:
        return ""


@register
class ZhihuAdapter:
    name = "zhihu"
    kinds = {"article"}

    def __init__(self, session: requests.Session = None):
        self._session = session

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": _ua(), "Accept-Language": "zh-CN,zh;q=0.9"})
            load_cookie(self._session)
        return self._session

    # ---------------- 路由 ----------------

    def match(self, url: str) -> bool:
        return bool(_ARTICLE_RE.search(url or "") or _ANSWER_RE.search(url or "")
                    or _QUESTION_RE.search(url or ""))

    def resolve(self, q: str) -> ItemRef:
        m = _ARTICLE_RE.search(q or "")
        if m:
            return ItemRef(platform="zhihu", item_id=f"article-{m.group(1)}",
                           url=f"https://zhuanlan.zhihu.com/p/{m.group(1)}", sub_id="1",
                           extra={"kind": "article", "article_id": m.group(1)})
        m = _ANSWER_RE.search(q or "")
        if m:
            return ItemRef(platform="zhihu", item_id=f"answer-{m.group(2)}",
                           url=f"https://www.zhihu.com/question/{m.group(1)}/answer/{m.group(2)}",
                           sub_id="1", extra={"kind": "answer", "question_id": m.group(1),
                                              "answer_id": m.group(2)})
        m = _QUESTION_RE.search(q or "")
        if m:
            return ItemRef(platform="zhihu", item_id=f"question-{m.group(1)}",
                           url=f"https://www.zhihu.com/question/{m.group(1)}", sub_id="1",
                           extra={"kind": "question", "question_id": m.group(1)})
        raise SearchNeeded(q)

    # ---------------- 取数 ----------------

    def _get(self, url: str) -> str:
        def do():
            r = self.session.get(url, timeout=30, headers={"Referer": "https://www.zhihu.com/"})
            if r.status_code == 403 and "zh-zse-ck" in r.text:
                raise ZhihuError("知乎风控挑战：cookie 缺失/失效，请重新导出 scripts/.zhihu_cookie"
                                 "（可用 python scripts/cookie_export.py zhihu 一键导出）")
            if r.status_code == 200 and "安全验证" in r.text:
                raise ZhihuError("知乎要求安全验证：请在浏览器登录并通过验证后重新导出 cookie")
            r.raise_for_status()
            return r.text
        return retry_with_backoff(do, attempts=2, base_delay=1.0)

    def _entities(self, url: str) -> dict:
        return parse_initial_data(self._get(url)).get("initialState", {}).get("entities", {})

    # ---------------- 列表 / 元信息 ----------------

    def list_items(self, ref):
        if ref.extra.get("kind") != "question":
            return [ref]
        qid = ref.extra["question_id"]
        ent = self._entities(ref.url)
        q = (ent.get("questions") or {}).get(qid) or {}
        items = []
        for i, (aid, a) in enumerate(list((ent.get("answers") or {}).items())[:_ANSWER_LIMIT], 1):
            author = (a.get("author") or {}).get("name") or ""
            items.append(ItemRef(platform="zhihu", item_id=f"answer-{aid}",
                                 url=f"https://www.zhihu.com/question/{qid}/answer/{aid}",
                                 sub_id=str(i), title=author or f"回答{i}",
                                 extra={"kind": "answer", "question_id": qid, "answer_id": aid,
                                        "question_title": q.get("title") or ref.title,
                                        "question_author": (q.get("author") or {}).get("name", "")}))
        return items or [ref]

    def fetch_meta(self, ref) -> ItemMeta:
        kind = ref.extra.get("kind")
        if kind == "question":
            ent = self._entities(ref.url)
            q = (ent.get("questions") or {}).get(ref.extra["question_id"]) or {}
            return ItemMeta(title=q.get("title") or ref.title,
                            author=(q.get("author") or {}).get("name") or "知乎问答", url=ref.url,
                            desc=(q.get("detail") or "")[:200],
                            page=ref.sub_id or "1", page_part="", extra={"kind": "question"})
        ent = self._entities(ref.url)
        if kind == "article":
            obj = (ent.get("articles") or {}).get(ref.extra["article_id"]) or {}
            return ItemMeta(title=obj.get("title") or ref.title,
                            author=(obj.get("author") or {}).get("name", ""),
                            url=ref.url, published=_ts(obj.get("created")), desc=obj.get("excerpt", ""),
                            stats={"view": obj.get("visitCount"), "voteup": obj.get("voteupCount"),
                                   "comment": obj.get("commentCount")},
                            page=ref.sub_id or "1", page_part="")
        aid = ref.extra.get("answer_id", "")
        obj = (ent.get("answers") or {}).get(aid) or {}
        ans_author = (obj.get("author") or {}).get("name", "") or "知乎问答"
        q = (ent.get("questions") or {}).get(ref.extra.get("question_id")) or {}
        # 标题统一用问题级标题（列表来源用 extra，单条链接从实体回填，避免 untitled）
        title = ref.extra.get("question_title") or q.get("title") or ref.title
        if ref.extra.get("question_title"):
            # 问题展开的回答：合集归属提问者
            author = ref.extra.get("question_author") or (q.get("author") or {}).get("name") or "知乎问答"
        else:
            author = ans_author
        return ItemMeta(title=title, author=author, url=ref.url,
                        published=_ts(obj.get("createdTime")), desc="",
                        stats={"voteup": obj.get("voteupCount")},
                        page=ref.sub_id or "1", page_part=f"回答：{ans_author}")

    # ---------------- 内容 ----------------

    def fetch_content(self, ref) -> ContentResult:
        kind = ref.extra.get("kind")
        try:
            html = self._get(ref.url)
            ent = parse_initial_data(html).get("initialState", {}).get("entities", {})
        except ZhihuError as e:
            return ContentResult(kind="document", status="empty", reason=str(e)[:120], label="正文")
        if kind == "article":
            obj = (ent.get("articles") or {}).get(ref.extra["article_id"]) or {}
        elif kind == "answer":
            obj = (ent.get("answers") or {}).get(ref.extra.get("answer_id", "")) or {}
        else:
            return ContentResult(kind="document", status="empty",
                                 reason="问题页未解析到回答实体（SSR 无 answers，可能与页面结构有关）",
                                 label="正文")
        blocks, images = html_to_blocks(obj.get("content") or "")
        if not blocks:
            return ContentResult(kind="document", status="empty", reason="正文为空", label="正文")
        text_len = sum(len(b.get("text") or "") for b in blocks)
        status, reason = ("ok", "") if text_len >= 200 else ("suspect", f"正文过短（{text_len} 字）")
        return ContentResult(kind="document", status=status, blocks=blocks, images=images,
                             reason=reason, label="正文")

    def fetch_media_url(self, ref, media="audio", lowest=False):
        return None
