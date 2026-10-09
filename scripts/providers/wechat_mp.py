"""微信公众号适配器（document）：单篇文章（正文/图片）+ 搜狗微信搜索（实验性）。

尽调（2026-10-09）：canonical 链接无需 cookie；正文服务端渲染（id="js_content"）；
图片 data-src（mmbiz.qpic.cn 免 Referer 可下）；搜狗搜索路径会话绑定（实验性，可能失效）。
无需 COOKIE_EXPORT（无登录凭据）。
"""
import html as html_mod
import re
import urllib.parse
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests

from core.adapter import SearchNeeded
from core.htmlblocks import html_to_blocks
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_HOST_RE = re.compile(r"mp\.weixin\.qq\.com/s\b")
_SHORT_RE = re.compile(r"mp\.weixin\.qq\.com/s/([A-Za-z0-9_\-]{10,})")
_SOGOU_SEARCH = "https://weixin.sogou.com/weixin?type=2&query={q}"


class WeChatError(ValueError):
    """公众号取数失败（页面异常/搜狗跳转失效，可读）"""


_ERROR_MARKERS = ("参数错误", "系统出错", "该内容已被发布者删除",
                  "此内容因违规无法查看", "该公众号已迁移", "已被屏蔽", "环境异常")


def _find(html: str, pat: str) -> str:
    m = re.search(pat, html or "", re.S)
    return html_mod.unescape(m.group(1).strip()) if m else ""


def parse_meta(html: str) -> dict:
    """页面元信息：title/account（公众号名）/author（文章作者）/published/canonical"""
    title = _find(html, r'og:title"\s*content="([^"]*)"') or _find(html, r"var msg_title = '([^']*)'")
    account = _find(html, r'id="js_name"[^>]*>\s*([^<]{1,40}?)\s*<') or _find(html, r"nick_name:\s*'([^']+)'")
    author = _find(html, r'var author = "([^"]*)"') or account
    ct = _find(html, r'var ct = "(\d+)"')
    published = ""
    if ct:
        try:
            published = datetime.fromtimestamp(int(ct)).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            published = ""
    return {"title": title, "account": account, "author": author,
            "published": published, "canonical": _find(html, r'var msg_link = "([^"]+)"')}


def extract_article(html: str):
    """正文切片（js_content → bottom bar）→ (blocks, images)；页面异常抛 WeChatError"""
    if "rich_media_content" not in (html or ""):
        why = next((k for k in _ERROR_MARKERS if k in (html or "")), "页面不含正文")
        raise WeChatError(f"公众号页面不可读：{why}")
    start = html.find('id="js_content"')
    if start < 0:
        raise WeChatError("公众号页面结构异常：未找到正文容器")
    end = html.find('id="js_article_bottom_bar"', start)
    if end < 0:
        end = html.find("rich_media_area_extra", start)
    seg = html[start:end if end > start else len(html)]
    return html_to_blocks(seg)


def strip_tags(raw: str) -> str:
    s = re.sub(r"<[^>]+>", "", raw or "")
    return re.sub(r"\s+", " ", html_mod.unescape(s)).strip()


def parse_sogou_results(html: str, limit: int = 5) -> list:
    """搜狗微信搜索结果 → [{title, account, date, link, snippet}]（实验性）"""
    if "news-list" not in (html or ""):
        why = "可能需要验证码" if ("验证码" in (html or "") or "antispider" in (html or "").lower()) else "页面异常"
        raise WeChatError(f"搜狗搜索失败（{why}）；稍后再试或直接提供文章链接")
    blocks = re.split(r'<li id="sogou_vr_11002601_box_\d+"', html)[1:]
    out = []
    for b in blocks[:max(0, limit)]:
        m = re.search(r'<h3>\s*<a[^>]*href="(/link\?url=[^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        acc = re.search(r'<span class="all-time-y2">(.*?)</span>', b, re.S)
        ts = re.search(r"timeConvert\('(\d+)'\)", b)
        date = datetime.fromtimestamp(int(ts.group(1))).strftime("%Y-%m-%d") if ts else ""
        snip = re.search(r'<p class="txt-info">(.*?)</p>', b, re.S)
        out.append({
            "title": strip_tags(m.group(2)),
            "account": strip_tags(acc.group(1)) if acc else "",
            "date": date,
            "link": "https://weixin.sogou.com" + html_mod.unescape(m.group(1)),
            "snippet": strip_tags(snip.group(1)) if snip else "",
        })
    return out


@register
class WeChatMpAdapter:
    name = "wechat_mp"
    kinds = {"article"}

    def __init__(self, session: requests.Session = None):
        self._session = session

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = ThrottledSession("wechat_mp")
            self._session.headers.update({"User-Agent": _UA,
                                          "Accept-Language": "zh-CN,zh;q=0.9"})
        return self._session

    # ---------------- 路由 ----------------

    def match(self, url: str) -> bool:
        url = url or ""
        if not _HOST_RE.search(url):
            return False
        return bool(_SHORT_RE.search(url) or re.search(r"/s\?\S*__biz=", url))

    def resolve(self, q: str) -> ItemRef:
        q = (q or "").strip()
        m = _SHORT_RE.search(q)
        if m:
            token = m.group(1)
            return ItemRef(platform="wechat_mp", item_id=f"wx-{token}",
                           url=f"https://mp.weixin.qq.com/s/{token}", sub_id="1",
                           extra={"kind": "article", "token": token})
        if _HOST_RE.search(q) and "?" in q:
            qs = parse_qs(urlparse(q).query)
            biz = (qs.get("__biz") or [""])[0]
            mid = (qs.get("mid") or [""])[0]
            idx = (qs.get("idx") or ["1"])[0]
            if biz and mid:
                slug = re.sub(r"[^A-Za-z0-9]+", "_", biz)[:24].strip("_")
                return ItemRef(platform="wechat_mp", item_id=f"wx-{slug}_{mid}_{idx}",
                               url=q, sub_id="1",
                               extra={"kind": "article", "biz": biz, "mid": mid, "idx": idx})
        raise SearchNeeded(q)

    def list_items(self, ref):
        return [ref]

    # ---------------- 内容 ----------------

    def _get_html(self, url: str) -> str:
        try:
            r = self.session.get(url, headers={"Referer": "https://mp.weixin.qq.com/"},
                                 timeout=25)
        except requests.RequestException as e:
            raise WeChatError(f"网络请求失败：{str(e)[:80]}")
        if r.status_code != 200:
            raise WeChatError(f"公众号页面异常（HTTP {r.status_code}）")
        return r.text

    def fetch_meta(self, ref) -> ItemMeta:
        m = parse_meta(self._get_html(ref.url))
        return ItemMeta(title=m["title"] or "公众号文章",
                        author=m["account"] or m["author"] or "",
                        url=ref.url, published=m["published"],
                        desc="" if m["author"] == m["account"] else m["author"],
                        page=ref.sub_id or "1")

    def fetch_content(self, ref) -> ContentResult:
        try:
            blocks, imgs = extract_article(self._get_html(ref.url))
        except WeChatError as e:
            return ContentResult(kind="document", status="empty",
                                 reason=str(e)[:120], label="正文")
        if not blocks and not imgs:
            return ContentResult(kind="document", status="empty", reason="正文为空", label="正文")
        text_len = sum(len(b.get("text") or "") for b in blocks)
        status = "ok" if (text_len >= 20 or imgs) else "suspect"
        reason = "" if status == "ok" else f"正文过短（{text_len} 字）"
        return ContentResult(kind="document", status=status, blocks=blocks, images=imgs,
                             reason=reason, label="正文")

    # ---------------- 搜狗搜索（实验性：结果链接会话绑定，仅同进程可用） ----------------

    def search_articles(self, query: str, limit: int = 5) -> list:
        url = _SOGOU_SEARCH.format(q=urllib.parse.quote(query or ""))
        r = self.session.get(url, headers={"Referer": "https://weixin.sogou.com/"}, timeout=25)
        return parse_sogou_results(r.text, limit=limit)

    def resolve_sogou_link(self, link: str) -> str:
        """搜狗跳转页 → 微信签名文章 URL（必须与搜索同一会话；实验性）"""
        r = self.session.get(link, headers={"Referer": "https://weixin.sogou.com/weixin"},
                             timeout=25, allow_redirects=False)
        parts = re.findall(r"url \+= '([^']+)'", r.text or "")
        signed = "".join(parts).replace("@", "").strip()
        if not signed.startswith("https://mp.weixin.qq.com/"):
            raise WeChatError("搜狗跳转解析失败（需与搜索同一会话；可改用文章原链接）")
        return signed

    def fetch_media_url(self, ref, media="audio", lowest=False):
        return None
