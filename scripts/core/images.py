"""document 正文图片下载（assets/）：失败跳过、已存在复用"""
import hashlib
import os

import requests

from core.retry import retry_with_backoff

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_EXT_OK = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _ext(url: str) -> str:
    ext = os.path.splitext(url.split("?", 1)[0])[1].lower()
    return ext if ext in _EXT_OK else ".jpg"


def download_images(urls, out_dir: str, timeout: int = 30, session=None,
                    attempts: int = 2, base_delay: float = 1.0) -> list:
    """下载到 <out_dir>/assets/，返回相对文件名列表（如 assets/img_01_ab12cd34.jpg）"""
    if not urls:
        return []
    assets = os.path.join(out_dir, "assets")
    os.makedirs(assets, exist_ok=True)
    s = session or requests.Session()
    if "User-Agent" not in getattr(s, "headers", {}):
        s.headers["User-Agent"] = _UA
    saved = []
    for i, url in enumerate(urls, 1):
        name = f"img_{i:02d}_{hashlib.md5(url.encode()).hexdigest()[:8]}{_ext(url)}"
        dest = os.path.join(assets, name)
        if os.path.exists(dest):
            saved.append(f"assets/{name}")
            continue
        try:
            r = retry_with_backoff(lambda u=url: s.get(u, timeout=timeout,
                                                       headers={"Referer": "https://www.zhihu.com/"}),
                                   attempts=attempts, base_delay=base_delay)
            r.raise_for_status()
            with open(dest, "wb") as f:
                f.write(r.content)
            saved.append(f"assets/{name}")
        except Exception:
            continue
    return saved
