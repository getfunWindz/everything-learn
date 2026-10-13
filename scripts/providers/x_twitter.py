"""X（Twitter）适配器（document）：单推文（正文/图片/视频口播转写）。

尽调（2026-10-09）：公开推文无需凭据；数据源 syndication tweet-result（token 本地计算）；
媒体直链（pbs/video.twimg.com）可直下；时间线/搜索不做（曾 429/SSL EOF 限流）。
无需 COOKIE_EXPORT。
"""
import math
import re
import time
from datetime import datetime

import requests

from core import config
from core.adapter import SearchNeeded
from core.models import ContentResult, ItemMeta, ItemRef
from core.registry import register
from core.throttle import ThrottledSession

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_STATUS_RE = re.compile(
    r"https?://(?:(?:mobile|www)\.)?(?:x|twitter)\.com/(?:[A-Za-z0-9_]+/)*(?:web/)?status/(\d{6,})")
_TCO_RE = re.compile(r"https?://t\.co/([A-Za-z0-9]+)")
_TWEET_API = "https://cdn.syndication.twimg.com/tweet-result"


class XError(ValueError):
    """X 取数失败（限流/推文不存在，可读）"""


def _js_tostr36(x: float) -> str:
    """等价 JS Number.toString(36)（供 token 计算；浮点实现足够稳定）"""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    ip, fp, out = int(x), x - int(x), ""
    if ip == 0:
        out = "0"
    while ip > 0:
        out = digits[ip % 36] + out
        ip //= 36
    if fp > 0:
        out += "."
        for _ in range(20):
            fp *= 36
            d = int(fp)
            out += digits[d]
            fp -= d
            if fp <= 0:
                break
    return out


def tweet_token(tweet_id: str) -> str:
    """syndication 接口 token：((id / 1e15) * π).toString(36) 去掉 0 与 ."""
    return re.sub(r"(0+|\.)", "", _js_tostr36(int(tweet_id) / 1e15 * math.pi))


def _expand_urls(text: str, entities: dict) -> str:
    for u in ((entities or {}).get("urls") or []):
        short, full = u.get("url"), u.get("expanded_url")
        if short and full:
            text = text.replace(short, full)
    return text


def _pick_video_url(variants: list):
    """选 mp4：优先 ≤ 2176k 的最高码率；否则最低码率；无 mp4 → None"""
    mp4s = [v for v in (variants or [])
            if v.get("content_type") == "video/mp4" and v.get("url")]
    if not mp4s:
        return None
    under = [v for v in mp4s if (v.get("bitrate") or 0) <= 2_176_000]
    pick = max(under, key=lambda v: v.get("bitrate") or 0) if under else \
        min(mp4s, key=lambda v: v.get("bitrate") or 0)
    return pick["url"]


def parse_tweet(raw: dict) -> dict:
    """tweet-result JSON → 归一化 dict（文本/作者/时间/图片/视频）"""
    text = _expand_urls(raw.get("text") or "", raw.get("entities") or {})
    user = raw.get("user") or {}
    created = raw.get("created_at") or ""
    published = ""
    try:
        published = datetime.fromisoformat(created.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        pass
    images, video_url, video_ms = [], None, 0
    for m in (raw.get("mediaDetails") or []):
        if m.get("type") == "photo" and m.get("media_url_https"):
            images.append(m["media_url_https"])
        elif m.get("type") == "video":
            video_ms = int(((m.get("video_info") or {}).get("duration_millis")) or 0)
            video_url = _pick_video_url((m.get("video_info") or {}).get("variants"))
    for p in (raw.get("photos") or []):
        u = p.get("url")
        if u and u not in images:
            images.append(u)
    first_line = (text.split("\n") or [""])[0].strip()
    title = (first_line[:60] + "…") if len(first_line) > 60 else (first_line or "推文")
    return {"title": title, "text": text, "author": user.get("name") or user.get("screen_name") or "",
            "screen_name": user.get("screen_name") or "", "published": published,
            "images": images, "video_url": video_url, "video_ms": video_ms,
            "stats": {"like": raw.get("favorite_count"), "reply": raw.get("conversation_count")}}


@register
class XTwitterAdapter:
    name = "x_twitter"
    kinds = {"article"}

    def __init__(self, session: requests.Session = None):
        self._session = session
        self._tweets = {}          # item_id -> parsed dict（同一运行内一次请求）

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = ThrottledSession("x_twitter")
            self._session.headers.update({"User-Agent": _UA,
                                          "Accept-Language": "en-US,en;q=0.9"})
        return self._session

    # ---------------- 路由 ----------------

    def match(self, url: str) -> bool:
        url = url or ""
        return bool(_STATUS_RE.search(url) or _TCO_RE.match(url))

    def resolve(self, q: str) -> ItemRef:
        q = (q or "").strip()
        m = _STATUS_RE.search(q)
        if not m and _TCO_RE.match(q):
            try:
                r = self.session.get(q, allow_redirects=True, timeout=20, stream=True)
                r.close()
                m = _STATUS_RE.search(getattr(r, "url", "") or "")
            except requests.RequestException as e:
                raise XError(f"短链展开失败：{str(e)[:80]}")
        if m:
            tid = m.group(1)
            return ItemRef(platform="x_twitter", item_id=f"tw-{tid}",
                           url=f"https://x.com/i/status/{tid}", sub_id="1",
                           extra={"kind": "tweet", "tid": tid})
        raise SearchNeeded(q)

    def list_items(self, ref):
        return [ref]

    # ---------------- 数据获取（一次请求 + 实例缓存） ----------------

    def _get_tweet_json(self, ref) -> dict:
        url = _TWEET_API
        params = {"id": ref.extra["tid"], "lang": "en", "token": tweet_token(ref.extra["tid"])}

        def once():
            r = self.session.get(url, params=params, timeout=25)
            if r.status_code == 404:
                raise XError("推文不存在或已删除")
            if r.status_code == 429:
                raise XError("X 限流（429）：请稍后重试（缓速 10s 已开启）")
            r.raise_for_status()
            return r.json()

        try:
            return once()
        except XError:
            raise                      # 404/429 不重试（避免无谓请求）
        except Exception:
            time.sleep(4)              # 瞬时网络错误（含 SSL EOF）：退避一次
            try:
                return once()
            except XError:
                raise
            except Exception as e:
                raise XError(f"推文获取失败：{str(e)[:80]}")

    def _data(self, ref) -> dict:
        if ref.item_id in self._tweets:
            return self._tweets[ref.item_id]
        try:
            raw = self._get_tweet_json(ref)
        except XError:
            raise
        except Exception as e:
            raise XError(f"推文获取失败：{str(e)[:80]}")
        data = parse_tweet(raw)
        self._tweets[ref.item_id] = data
        return data

    def fetch_meta(self, ref) -> ItemMeta:
        d = self._data(ref)
        return ItemMeta(title=d["title"], author=d["author"], url=ref.url,
                        published=d["published"], desc=d["text"][:200],
                        stats=d["stats"], page=ref.sub_id or "1")

    def fetch_content(self, ref) -> ContentResult:
        try:
            d = self._data(ref)
        except XError as e:
            return ContentResult(kind="document", status="empty", reason=str(e)[:120], label="正文")
        blocks = [{"heading": "", "text": d["text"]}]
        if d.get("video_url") and d.get("video_ms"):
            blocks.append(self._transcribe_block(d))
        text_len = sum(len(b["text"] or "") for b in blocks)
        status = "ok" if (text_len >= 20 or d["images"]) else "suspect"
        reason = "" if status == "ok" else f"正文过短（{text_len} 字）"
        return ContentResult(kind="document", status=status, blocks=blocks,
                             images=d["images"], reason=reason, label="正文")

    def _transcribe_block(self, d: dict) -> dict:
        try:
            import transcriber
            cfg = config.load_config()
            mode = cfg.get("whisper_vad", "auto")
            vad = None if mode == "auto" else bool(mode)
            lines = transcriber.transcribe_url(
                d["video_url"], model_size=cfg.get("whisper_model") or "medium",
                vad=vad, duration_sec=d["video_ms"] / 1000,
                vad_long_sec=cfg.get("vad_long_sec", 1800),
                headers={"User-Agent": _UA, "Referer": "https://x.com/"})
            text = " ".join((l.get("text") or "").strip() for l in lines if (l.get("text") or "").strip())
            return {"heading": "视频口播内容", "text": text or "（视频无口播内容）"}
        except Exception as e:
            return {"heading": "视频口播内容", "text": f"（视频转写失败：{str(e)[:80]}）"}

    def fetch_media_url(self, ref, media="audio", lowest=False):
        try:
            return self._data(ref).get("video_url")
        except XError:
            return None
