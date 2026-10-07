"""YouTube 内容源适配器：yt-dlp 提取元信息/字幕/媒体直链（无需 API key、无需 ffmpeg）

尽调结论与实现要点见 docs/plans/2026-10-07-m1-youtube.md：
- 字幕：人工优先；自动字幕 json3 为滚动式，需去重清洗；
- 直链带时效：fetch_media_url 调用时即取。
"""
import re

from core.adapter import SearchNeeded  # noqa: F401  （名称搜索暂不支持，见计划"风险"）
from core.models import ContentResult, ItemMeta, ItemRef  # noqa: F401
from core.registry import register

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

    # ---------------- 内容 / 媒体（Task 3/4 实现） ----------------

    def fetch_content(self, ref) -> ContentResult:
        raise NotImplementedError("YouTube 字幕提取将在 M1 Task 3 实现")

    def fetch_media_url(self, ref, media: str = "audio", lowest: bool = False):
        raise NotImplementedError("YouTube 媒体直链将在 M1 Task 4 实现")
