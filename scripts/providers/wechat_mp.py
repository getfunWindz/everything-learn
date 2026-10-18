"""微信公众号适配器（document）：单篇文章（正文/图片）+ 搜狗微信搜索（实验性）。

尽调（2026-10-09）：canonical 链接无需 cookie；正文服务端渲染（id="js_content"）；
图片 data-src（mmbiz.qpic.cn 免 Referer 可下）；搜狗搜索路径会话绑定（实验性，可能失效）。
无需 COOKIE_EXPORT（无登录凭据）。
"""
import hashlib
import html as html_mod
import json
import os
import time
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import requests

from core.adapter import SearchNeeded
from core import browserfetch
from core.htmlblocks import html_to_blocks
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_HOST_RE = re.compile(r"mp\.weixin\.qq\.com/s\b")
_SHORT_RE = re.compile(r"mp\.weixin\.qq\.com/s/([A-Za-z0-9_\-]{10,})")
_ALBUM_RE = re.compile(r"mp\.weixin\.qq\.com/mp/appmsgalbum")
_ALBUM_API = ("https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum&__biz={biz}"
              "&scene=1&album_id={album_id}&f=json&count={count}")
_ALBUM_COUNT = 10
_ALBUM_MAX_PAGES = 30
_SOGOU_SEARCH = "https://weixin.sogou.com/weixin?type=2&query={q}"
_SEARCH_FP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
_SEARCH_FP_TTL = 3600

MIN_DOC_TEXT = 20      # 正文最小可用字数；有正文图时放行（图片为主文章）
_BROWSER_ATTEMPTS = 2  # 浏览器兜底次数（首屏没有正文容器 → 重载一次）


def doc_text_len(blocks) -> int:
    """正文块字数合计"""
    return sum(len((b or {}).get("text") or "") for b in (blocks or []))


def usable_document(blocks, images=()) -> bool:
    """文档可用性闸门：正文 ≥ MIN_DOC_TEXT 字，或至少 1 张正文图"""
    return doc_text_len(blocks) >= MIN_DOC_TEXT or bool(images)


def search_fingerprint(hits) -> str:
    """搜索结果指纹（标题+账号，顺序敏感）：用于检测搜狗结果漂移"""
    raw = chr(10).join(f"{(h or {}).get('title', '')}|{(h or {}).get('account', '')}"
                        for h in (hits or []))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _fp_path(query: str) -> str:
    return os.path.join(_SEARCH_FP_DIR, f"wechat_search_{hashlib.sha1(str(query).encode('utf-8')).hexdigest()[:16]}.json")


def save_search_fingerprint(query: str, hits) -> None:
    try:
        os.makedirs(_SEARCH_FP_DIR, exist_ok=True)
        tmp = _fp_path(query) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "query": query, "fp": search_fingerprint(hits),
                       "titles": [(h or {}).get("title", "") for h in (hits or [])]},
                      f, ensure_ascii=False)
        os.replace(tmp, _fp_path(query))
    except OSError:
        pass


def load_search_fingerprint(query: str):
    try:
        with open(_fp_path(query), encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - float(data.get("ts") or 0) > _SEARCH_FP_TTL:
            return None
        return data
    except (OSError, ValueError):
        return None


class WeChatError(ValueError):
    """公众号取数失败（页面异常/搜狗跳转失效，可读）"""


_ERROR_MARKERS = ("参数错误", "系统出错", "该内容已被发布者删除",
                  "此内容因违规无法查看", "该公众号已迁移", "已被屏蔽", "环境异常")


def _find(html: str, pat: str) -> str:
    m = re.search(pat, html or "", re.S)
    return html_mod.unescape(m.group(1).strip()) if m else ""


_CN_TZ = timezone(timedelta(hours=8))    # 微信/搜狗时间戳均按中国时区展示


def _ts_to_cn_date(ts) -> str:
    """Unix 时间戳 → 北京时间 YYYY-MM-DD（显式时区，不受机器时区影响；非法值返回空）"""
    try:
        return datetime.fromtimestamp(int(ts), tz=_CN_TZ).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def parse_meta(html: str) -> dict:
    """页面元信息：title/account（公众号名）/author（文章作者）/published/canonical"""
    title = _find(html, r'og:title"\s*content="([^"]*)"') or _find(html, r"var msg_title = '([^']*)'")
    account = _find(html, r'id="js_name"[^>]*>\s*([^<]{1,40}?)\s*<') or _find(html, r"nick_name:\s*'([^']+)'")
    author = _find(html, r'var author = "([^"]*)"') or account
    ct = _find(html, r'var ct = "(\d+)"')
    published = _ts_to_cn_date(ct) if ct else ""
    return {"title": title, "account": account, "author": author,
            "published": published, "canonical": _find(html, r'var msg_link = "([^"]+)"')}


def parse_album(payload: dict) -> dict:
    """getalbum_resp → {title, author, count, articles, continued, last}；
    article_list 兼容单条（dict）与多条（list）（微信 PHP json 的老癖好）"""
    resp = (payload or {}).get("getalbum_resp") or {}
    bi = resp.get("base_info") or {}
    al = resp.get("article_list")
    raw = al if isinstance(al, list) else ([al] if isinstance(al, dict) and al else [])
    arts = []
    for it in raw:
        url = (it.get("url") or "").strip()
        if url.startswith("http://"):
            url = "https://" + url[7:]
        if not url:
            continue
        arts.append({"title": (it.get("title") or "").strip(), "url": url,
                     "msgid": it.get("msgid"), "itemidx": it.get("itemidx")})
    return {"title": (bi.get("title") or "").strip(),
            "author": (bi.get("nickname") or bi.get("username") or "").strip(),
            "count": int(bi.get("article_count") or 0),
            "articles": arts,
            "continued": bool(resp.get("continue_flag")),
            "last": (arts[-1] if arts else None)}


def _album_article_id(url: str) -> str:
    qs = parse_qs(urlparse(url).query)
    biz = (qs.get("__biz") or [""])[0]
    mid = (qs.get("mid") or [""])[0]
    idx = (qs.get("idx") or ["1"])[0]
    if biz and mid:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", biz)[:24].strip("_")
        return f"wx-{slug}_{mid}_{idx}"
    return f"wx-{hashlib.md5(url.encode('utf-8')).hexdigest()[:12]}"


_NOISE_EXACT = {
    "预览时标签不可点", "阅读原文", "赞", "分享", "收藏", "在看", "微信扫一扫",
    "关注该公众号", "知道了", "取消", "允许", "分析", "使用完整服务",
    "轻点两下取消赞", "轻点两下取消在看", "网页由 mp.weixin.qq.com 提供",
    "不喜欢", "使用小程序", "×", "投诉", "写留言", "复制链接", "保存图片",
    "打开", "关闭", "更多", "发送", "返回", "搜索", "设置",
}
_NOISE_PREFIX = ("<", "微信扫一扫", "预览时标签不可点", "使用完整服务",
                 "轻点两下取消")


def _is_noise_line(line: str) -> bool:
    line = (line or "").strip()
    return bool(line) and (line in _NOISE_EXACT or line.startswith(_NOISE_PREFIX))


def strip_boilerplate(blocks):
    """过滤公众号页面模板噪声（正文切片边界不稳时混入的 UI 文案/残片）

    噪声可能多条挤在同一块里 → 按行过滤，保留块内真实段落。"""
    out = []
    for b in blocks or []:
        raw = (b.get("text") or "").strip()
        if not raw:
            continue
        kept = [ln for ln in raw.split(chr(10)) if not _is_noise_line(ln)]
        text = chr(10).join(kept).strip()
        if not text:
            continue
        if text == raw:
            out.append(b)
        else:
            out.append({**b, "text": text})
    return out


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
    blocks, images = html_to_blocks(seg)
    return strip_boilerplate(blocks), images


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
        date = _ts_to_cn_date(ts.group(1)) if ts else ""
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
        self._browser_meta = {}

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
        if _ALBUM_RE.search(url):
            return True
        if not _HOST_RE.search(url):
            return False
        return bool(_SHORT_RE.search(url) or ("?" in url))

    def resolve(self, q: str) -> ItemRef:
        q = (q or "").strip()
        if _ALBUM_RE.search(q):
            qs = parse_qs(urlparse(q).query)
            biz = (qs.get("__biz") or [""])[0]
            album_id = (qs.get("album_id") or [""])[0]
            if biz and album_id:
                return ItemRef(platform="wechat_mp", item_id=f"wx-album-{album_id}",
                               url=q, sub_id="1",
                               extra={"kind": "album", "biz": biz, "album_id": album_id})
            raise SearchNeeded(q)
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
            if qs.get("src") or qs.get("signature") or qs.get("timestamp"):
                digest = hashlib.md5(q.encode("utf-8")).hexdigest()[:12]
                return ItemRef(platform="wechat_mp", item_id=f"wx-signed-{digest}",
                               url=q, sub_id="1",
                               extra={"kind": "article", "signed": True,
                                      "referer": "https://weixin.sogou.com/"})
        raise SearchNeeded(q)

    def _get_json(self, url: str) -> dict:
        try:
            r = self.session.get(url, headers={"Referer": "https://mp.weixin.qq.com/"}, timeout=25)
        except requests.RequestException as e:
            raise WeChatError(f"网络请求失败：{str(e)[:80]}")
        if r.status_code != 200:
            raise WeChatError(f"公众号合集接口异常（HTTP {r.status_code}）")
        try:
            d = r.json()
        except Exception:
            raise WeChatError("公众号合集接口返回非 JSON（可能被风控）")
        ret = (d.get("base_resp") or {}).get("ret")
        if ret:
            raise WeChatError(f"公众号合集接口错误（ret={ret}）")
        return d

    def _album_data(self, ref) -> dict:
        """合集数据（分页拼接 + 实例缓存：list_items/根 meta 共用）"""
        key = ref.item_id or ref.url
        cached = getattr(self, "_album_cache", None)
        if cached and cached.get("key") == key:
            return cached["data"]
        biz, album_id = ref.extra["biz"], ref.extra["album_id"]
        articles, title, author, last = [], "", "", None
        for _ in range(_ALBUM_MAX_PAGES):
            url = _ALBUM_API.format(biz=urllib.parse.quote(biz), album_id=album_id,
                                    count=_ALBUM_COUNT)
            if last:
                url += f"&begin_msgid={last['msgid']}&begin_itemidx={last['itemidx']}"
            d = parse_album(self._get_json(url))
            title = title or d["title"]
            author = author or d["author"]
            seen = {a["url"] for a in articles}
            new = [a for a in d["articles"] if a["url"] not in seen]
            articles.extend(new)
            last = d["last"]
            if not d["continued"] or not new:
                break
        data = {"title": title, "author": author, "articles": articles}
        self._album_cache = {"key": key, "data": data}
        return data

    def fetch_root_meta(self, ref, refs) -> ItemMeta:
        """合集根元信息：公众号合集用 collection 自身标题/账号；其余沿用首条"""
        if (ref.extra or {}).get("kind") == "album":
            d = self._album_data(ref)
            return ItemMeta(title=d["title"] or "公众号合集", author=d["author"] or "",
                            url=ref.url, page="1")
        return self.fetch_meta(refs[0])

    def list_items(self, ref):
        if (ref.extra or {}).get("kind") == "album":
            data = self._album_data(ref)
            return [ItemRef(platform="wechat_mp", item_id=_album_article_id(a["url"]),
                            url=a["url"], sub_id=str(i), title=a["title"],
                            extra={"kind": "article"})
                    for i, a in enumerate(data["articles"], 1)]
        return [ref]

    # ---------------- 内容 ----------------

    def _get_html(self, url: str, referer: str = None) -> str:
        cached = getattr(self, "_html_cache", None)
        if cached and cached[0] == url:
            return cached[1]
        try:
            r = self.session.get(url, headers={"Referer": referer or "https://mp.weixin.qq.com/"},
                                 timeout=25)
        except requests.RequestException as e:
            raise WeChatError(f"网络请求失败：{str(e)[:80]}")
        if r.status_code != 200:
            raise WeChatError(f"公众号页面异常（HTTP {r.status_code}）")
        self._html_cache = (url, r.text)
        return r.text

    def fetch_meta(self, ref) -> ItemMeta:
        try:
            m = parse_meta(self._get_html(ref.url, ref.extra.get("referer")))
        except Exception:
            m = {}
        if not m.get("title"):
            m = self._browser_meta.get(ref.url) or {}
            if not m:
                try:
                    _, _, m = self._fetch_browser(ref)
                except Exception:
                    m = {}
        return ItemMeta(title=m.get("title") or "公众号文章",
                        author=m.get("account") or m.get("author") or "",
                        url=ref.url, published=m.get("published") or "",
                        desc="" if m.get("author") == m.get("account") else (m.get("author") or ""),
                        page=ref.sub_id or "1")

    def fetch_content(self, ref) -> ContentResult:
        blocks, imgs, err = [], [], None
        try:
            blocks, imgs = extract_article(self._get_html(ref.url, ref.extra.get("referer")))
        except WeChatError as e:
            err = e
        if doc_text_len(blocks) < MIN_DOC_TEXT:          # 闸门①：请求侧正文偏短 → 浏览器兜底
            req_len = doc_text_len(blocks)
            try:
                b2, i2, _ = self._fetch_browser(ref)
                # 闸门②：浏览器结果也要过可用性检查，且不能比请求侧更短
                if usable_document(b2, i2) and doc_text_len(b2) >= req_len:
                    blocks, imgs = b2, i2
                elif not usable_document(blocks, imgs):
                    err = WeChatError(f"浏览器兜底正文过短（{doc_text_len(b2)} 字 / {len(i2)} 图）")
            except Exception as e:
                if not usable_document(blocks, imgs) and err is None:
                    err = e
        text_len = doc_text_len(blocks)
        if not usable_document(blocks, imgs):
            return ContentResult(kind="document", status="empty",
                                 reason=str(err or "正文为空")[:120], label="正文")
        status = "ok" if text_len >= MIN_DOC_TEXT else "suspect"
        reason = "" if status == "ok" else f"正文过短（{text_len} 字，含 {len(imgs)} 图）"
        return ContentResult(kind="document", status=status, blocks=blocks, images=imgs,
                             reason=reason, label="正文")

    # ---------------- 浏览器兜底（2026-10-17） ----------------
    # 实测：签名链接在 requests 侧返回「环境异常」，但同一 URL 在浏览器可正常渲染；
    # 搜狗搜索 requests 触发验证码，浏览器会话可正常返回结果。故提供浏览器兜底。

    _BROWSER_JS = r"""(() => {
      const root = document.querySelector('#js_content');
      if (!root) return {ok: false};
      const blocks = [], seen = new Set();
      const push = (t) => { t = (t || '').trim(); if (t && !seen.has(t)) { seen.add(t); blocks.push({heading: '', text: t}); } };
      const desc = root.querySelector('#js_image_desc');
      if (desc) {
        (desc.innerText || '').split(/\n+/).forEach(push);
      } else {
        root.querySelectorAll('h1,h2,h3,h4,p,li,blockquote,section').forEach(el => {
          if (el.querySelector('h1,h2,h3,h4,p,li,blockquote,section')) return;
          const t = (el.innerText || '').trim();
          if (!t) return;
          if (/^H[1-4]$/.test(el.tagName)) blocks.push({heading: t, text: ''});
          else push(t);
        });
        if (!blocks.length) (root.innerText || '').split(/\n+/).forEach(push);
      }
      const images = [...root.querySelectorAll('img')]
        .map(im => im.getAttribute('data-src') || im.getAttribute('src') || '')
        .filter(Boolean);
      const q = (sel) => { const e = document.querySelector(sel); return e ? (e.innerText || '').trim() : ''; };
      return {ok: true, blocks, images,
              title: q('#activity-name') || document.title || '',
              account: q('#js_name'), published: q('#publish_time')};
    })()"""

    @staticmethod
    def _browser_page_reason(html: str) -> str:
        """把浏览器拿到的拦截页翻译成可读原因"""
        html = html or ""
        if "环境异常" in html:
            return "浏览器打开被拦截（环境异常）：稍后重试或改用文章原链接"
        if "请在微信客户端打开" in html:
            return "该链接要求在微信客户端打开（浏览器不可读）"
        return "浏览器也未找到正文容器（#js_content）"

    def _fetch_browser(self, ref):
        """浏览器渲染文章 → (blocks, images, meta)；首屏不可用则重载一次（质量闸门）"""
        last = None
        for attempt in range(_BROWSER_ATTEMPTS):
            with browserfetch.BrowserSession("wechat_mp") as s:
                s.goto(ref.url)
                s.pause(2500 + attempt * 1500)     # 第二遍多等 1.5s（渲染/反爬校验）
                data = s.evaluate(self._BROWSER_JS) or {}
                if not data.get("ok"):
                    last = WeChatError(self._browser_page_reason(s.html()))
                    continue
                blocks = [b for b in (data.get("blocks") or [])
                          if (b.get("text") or b.get("heading"))]
                images = []
                for u in data.get("images") or []:
                    if u.startswith("//"):
                        u = "https:" + u
                    if u.startswith("http"):
                        images.append(u)
                if not usable_document(blocks, images):
                    last = WeChatError(
                        f"浏览器正文过短（{doc_text_len(blocks)} 字 / {len(images)} 图）")
                    continue
                meta = {"title": data.get("title") or "",
                        "account": data.get("account") or "",
                        "author": data.get("account") or "",
                        "published": data.get("published") or ""}
                self._browser_meta[ref.url] = meta
                return blocks, images, meta
        raise last

    def _search_browser(self, query: str, limit: int) -> list:
        """搜狗搜索浏览器兜底：解析结果 → 逐个解析为 mp.weixin 直链（会话内）"""
        with browserfetch.BrowserSession("wechat_mp") as s:
            s.goto(_SOGOU_SEARCH.format(q=urllib.parse.quote(query or "")))
            s.pause(3000)
            hits = parse_sogou_results(s.html(), limit=limit)
            out = []
            for h in hits[:limit]:
                link = h.get("link") or ""
                if link and not link.startswith("https://mp.weixin.qq.com/"):
                    try:
                        s.page.goto(link, wait_until="domcontentloaded", timeout=30000)
                        s.pause(1200)
                        if (s.page.url or "").startswith("https://mp.weixin.qq.com/"):
                            h = dict(h)
                            h["link"] = s.page.url
                    except Exception:
                        pass
                out.append(h)
            return out

    # ---------------- 搜狗搜索（实验性：结果链接会话绑定，仅同进程可用） ----------------

    def search_articles(self, query: str, limit: int = 5) -> list:
        url = _SOGOU_SEARCH.format(q=urllib.parse.quote(query or ""))
        try:
            r = self.session.get(url, headers={"Referer": "https://weixin.sogou.com/"}, timeout=25)
            hits = parse_sogou_results(r.text, limit=limit)
            if hits:
                return hits
        except Exception:
            pass
        return self._search_browser(query, limit)

    def resolve_sogou_link(self, link: str) -> str:
        """搜狗跳转页 → 微信签名文章 URL；已是 mp.weixin 直链则直接返回"""
        if (link or "").startswith("https://mp.weixin.qq.com/"):
            return link
        r = self.session.get(link, headers={"Referer": "https://weixin.sogou.com/weixin"},
                             timeout=25, allow_redirects=False)
        parts = re.findall(r"url \+= '([^']+)'", r.text or "")
        signed = "".join(parts).replace("@", "").strip()
        if not signed.startswith("https://mp.weixin.qq.com/"):
            raise WeChatError("搜狗跳转解析失败（需与搜索同一会话；可改用文章原链接）")
        return signed

    def fetch_media_url(self, ref, media="audio", lowest=False):
        return None
