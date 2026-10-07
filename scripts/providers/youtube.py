"""YouTube 内容源适配器：yt-dlp 提取元信息/字幕/媒体直链（无需 API key、无需 ffmpeg）

尽调结论与实现要点见 docs/plans/2026-10-07-m1-youtube.md：
- 字幕：人工优先；自动字幕 json3 为滚动式，需去重清洗；
- 直链带时效：fetch_media_url 调用时即取。
"""
import re

from core.adapter import SearchNeeded  # noqa: F401  （名称搜索暂不支持，见计划"风险"）
from core.models import ContentResult, ItemMeta, ItemRef  # noqa: F401
from core.registry import register
from core.subtitles import _norm_text, parse_vtt  # noqa: F401  （parse_vtt 供适配器与测试引用）

_VID = re.compile(r"^[0-9A-Za-z_-]{11}$")
_URL_RE = re.compile(r"(youtube\.com/(watch\?|shorts/|playlist)|youtu\.be/)", re.I)
_WATCH = "https://www.youtube.com/watch?v={}"


class YtDlpMissing(Exception):
    """yt-dlp 未安装"""


_yt_dlp_module = None


def _load_ytdlp():
    """懒加载 yt-dlp（import 阶段不联网、不报错）"""
    global _yt_dlp_module
    if _yt_dlp_module is None:
        try:
            import yt_dlp
            _yt_dlp_module = yt_dlp
        except ImportError as e:
            raise YtDlpMissing("未安装 yt-dlp，请执行: pip install yt-dlp") from e
    return _yt_dlp_module


# ---------------- 字幕解析（json3；vtt/srt 在 core.subtitles） ----------------

def _overlap_len(prev: str, cur: str) -> int:
    """prev 的后缀 == cur 的前缀 的最长长度（≥2 字符才算滚动重复）"""
    for n in range(min(len(prev), len(cur)), 1, -1):
        if prev[-n:] == cur[:n]:
            return n
    return 0


def parse_json3(text: str) -> list:
    """解析 YouTube json3 字幕；自动字幕为滚动式 → 去掉与上一事件重复的前缀"""
    import json
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    prev = ""
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        cur = _norm_text("".join(s.get("utf8") or "" for s in segs))
        if not cur:
            continue
        n = _overlap_len(prev, cur)
        new = cur[n:].strip()
        prev = cur
        if not new:
            continue
        start = float(ev.get("tStartMs") or 0) / 1000.0
        dur = max(float(ev.get("dDurationMs") or 0) / 1000.0, 0.5)
        out.append({"start": round(start, 2), "end": round(start + dur, 2), "text": new})
    return out


def _pick_caption_entry(entries) -> dict:
    """优先 json3，其次 vtt，再次任意带 url 的条目"""
    for ext in ("json3", "vtt"):
        for e in entries or []:
            if (e.get("ext") or "").lower() == ext and e.get("url"):
                return e
    for e in entries or []:
        if e.get("url"):
            return e
    return {}


def pick_audio_url(formats) -> str:
    """音频直链：优先 m4a（Whisper 兼容），再取其他音频流的最高码率"""
    audios = [f for f in formats or [] if f.get("vcodec") == "none" and f.get("url")]
    m4a = [f for f in audios if (f.get("ext") or "") == "m4a"]
    pool = m4a or audios
    pool = sorted(pool, key=lambda f: f.get("abr") or 0, reverse=True)
    return pool[0]["url"] if pool else ""


def pick_video_url(formats, lowest: bool = True) -> str:
    """视频直链：仅取视频流（排除 storyboard mhtml 与含音轨的格式）"""
    vids = [f for f in formats or []
            if (f.get("acodec") in (None, "none")) and f.get("vcodec") not in (None, "none")
            and (f.get("ext") or "") in ("mp4", "webm") and f.get("url")]
    vids = sorted(vids, key=lambda f: f.get("height") or 0, reverse=not lowest)
    return vids[0]["url"] if vids else ""


@register
class YouTubeAdapter:
    name = "youtube"
    kinds = {"video"}

    def __init__(self, client=None):
        self.lang = None
        self._meta_cache = {}      # item_id -> 完整 info（含字幕/格式），单次运行内复用

    # ---------------- 路由 ----------------

    def match(self, url) -> bool:
        if not url:
            return False
        return bool(_URL_RE.search(url))

    def resolve(self, q) -> ItemRef:
        q = (q or "").strip()
        if _VID.fullmatch(q):
            return ItemRef(platform="youtube", item_id=q, url=_WATCH.format(q))
        if not self.match(q):
            raise ValueError(f"不是 YouTube 链接或视频 ID：{q}")
        if "/playlist" in q:
            m = re.search(r"[?&]list=([0-9A-Za-z_-]+)", q)
            if not m:
                raise ValueError(f"无法解析播放列表 ID：{q}")
            return ItemRef(platform="youtube", item_id=m.group(1), url=q, extra={"playlist": True})
        for pat in (r"[?&]v=([0-9A-Za-z_-]{11})", r"youtu\.be/([0-9A-Za-z_-]{11})",
                    r"shorts/([0-9A-Za-z_-]{11})"):
            m = re.search(pat, q)
            if m:
                vid = m.group(1)
                return ItemRef(platform="youtube", item_id=vid, url=_WATCH.format(vid))
        raise ValueError(f"无法解析 YouTube 链接：{q}")

    # ---------------- yt-dlp 封装 ----------------

    def _extract(self, url, **opts):
        ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True, **opts}
        with _load_ytdlp().YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    def _info(self, ref):
        """完整 info（元信息+字幕+格式），按 item_id 缓存"""
        if ref.item_id not in self._meta_cache:
            self._meta_cache[ref.item_id] = self._extract(ref.url)
        return self._meta_cache[ref.item_id]

    # ---------------- 展开 / 元信息 ----------------

    def list_items(self, ref):
        if ref.extra.get("playlist"):
            info = self._extract(ref.url, extract_flat=True)
            items = []
            for i, e in enumerate(info.get("entries") or [], 1):
                if not e or not e.get("id"):
                    continue
                items.append(ItemRef(platform="youtube", item_id=e["id"],
                                     url=_WATCH.format(e["id"]), sub_id=str(i),
                                     title=e.get("title") or "",
                                     duration=int(e.get("duration") or 0)))
            return items or [ref]
        return [ItemRef(platform="youtube", item_id=ref.item_id, url=ref.url,
                        sub_id=ref.sub_id or "", title=ref.title, duration=ref.duration)]

    def fetch_meta(self, ref) -> ItemMeta:
        info = self._info(ref)
        pub = str(info.get("upload_date") or "")
        published = f"{pub[:4]}-{pub[4:6]}-{pub[6:8]}" if len(pub) == 8 else pub
        return ItemMeta(title=info.get("title") or ref.title, author=info.get("uploader") or "",
                        url=ref.url, published=published,
                        duration=int(info.get("duration") or ref.duration or 0),
                        desc=(info.get("description") or "")[:2000],
                        stats={"view": info.get("view_count") or 0,
                               "like": info.get("like_count") or 0},
                        page=str(ref.sub_id or ""), page_part=ref.title or "",
                        extra={"video_id": ref.item_id})

    # ---------------- 内容：字幕（人工优先；自动字幕滚动去重） ----------------

    _LANG_CHAIN = ["zh-Hans", "zh-CN", "zh", "en"]

    def fetch_content(self, ref) -> ContentResult:
        info = self._info(ref)
        subs = info.get("subtitles") or {}
        autos = info.get("automatic_captions") or {}
        chain = ([self.lang] if self.lang else []) + self._LANG_CHAIN
        entry, label = self._pick_caption(subs, autos, chain)
        if not entry:
            return ContentResult(kind="timeline", status="empty", reason="无字幕轨")
        try:
            text = self._download(entry["url"])
        except Exception as e:
            return ContentResult(kind="timeline", status="empty",
                                 reason=f"字幕下载失败：{str(e)[:80]}")
        ext = (entry.get("ext") or "").lower()
        if ext == "json3":
            segs = parse_json3(text)
        elif ext == "vtt":
            segs = parse_vtt(text)
        else:
            segs = parse_json3(text) or parse_vtt(text)
        duration = float(info.get("duration") or ref.duration or 0)
        if not segs:
            return ContentResult(kind="timeline", status="empty", reason="字幕解析为空", label=label)
        last = segs[-1]["end"]
        coverage = (last / duration) if duration else 1.0
        if len(segs) < 10 or coverage < 0.5:
            return ContentResult(kind="timeline", status="suspect", segments=segs, label=label,
                                 reason=f"覆盖不足({len(segs)}段 末条{last:.0f}s)")
        return ContentResult(kind="timeline", status="ok", segments=segs, label=label)

    def _pick_caption(self, subs, autos, chain):
        """人工字幕优先（按语言链）→ 自动字幕（按语言链）→ 任意人工 → 任意自动"""
        for lang in chain:
            if lang and subs.get(lang):
                e = _pick_caption_entry(subs[lang])
                if e:
                    return e, ""
        for lang in chain:
            if lang and autos.get(lang):
                e = _pick_caption_entry(autos[lang])
                if e:
                    return e, "字幕(自动)"
        for table, label in ((subs, ""), (autos, "字幕(自动)")):
            for entries in table.values():
                e = _pick_caption_entry(entries)
                if e:
                    return e, label
        return None, ""

    def _download(self, url: str) -> str:
        """字幕文件下载：429/5xx 有界退避重试（YouTube timedtext 限流时自动重试）"""
        import requests
        from core.retry import retry_with_backoff

        def _once():
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        return retry_with_backoff(_once, attempts=3, base_delay=2.0)

    # ---------------- 媒体直链（音频 Whisper / 视频抽帧） ----------------

    def fetch_media_url(self, ref, media: str = "audio", lowest: bool = False):
        """直链带时效：单次运行内复用 info（避免重复提取），不跨 run 持久化"""
        info = self._info(ref)
        fmts = info.get("formats") or []
        if media == "video":
            url = pick_video_url(fmts, lowest=lowest)
            if not url:  # 兜底：让 yt-dlp 直接选
                sel = "worst" if lowest else "best"
                try:
                    info2 = self._extract(ref.url, format=f"{sel}[ext=mp4]/{sel}")
                    url = info2.get("url") or ""
                except Exception:
                    url = ""
            return url or None
        url = pick_audio_url(fmts)
        if not url:
            try:
                info2 = self._extract(ref.url, format="bestaudio[ext=m4a]/bestaudio")
                url = info2.get("url") or ""
            except Exception:
                url = ""
        return url or None
