"""HTML → document 块转换（平台无关）：标题→分节，段落→文本，图片→URL，公式→LaTeX"""
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_TEXT_TAGS = {"p", "li", "blockquote", "figcaption", "pre", "div", "td"}
_SKIP_TAGS = {"script", "style", "noscript", "svg"}


def _fix_img_url(url: str) -> str:
    url = (url or "").strip()
    if not url or url.startswith("data:"):
        return ""
    if url.startswith("//"):
        url = "https:" + url
    url = url.split("?", 1)[0]
    for suf in ("_b.jpg", "_r.jpg", "_720w.jpg"):
        if url.endswith(suf):
            url = url[: -len(suf)] + ".jpg"
    return url


def _clean_text(s: str) -> str:
    lines = [re.sub(r"[ \t\r\u00a0]+", " ", ln).strip() for ln in (s or "").split("\n")]
    return "\n".join(ln for ln in lines if ln)


class _BlocksParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.images = [], []
        self._cur = {"heading": "", "paras": []}
        self._heading_buf = None
        self._buf = None
        self._text_depth = 0
        self._skip_depth = 0

    def _flush_section(self):
        if self._cur["heading"] or self._cur["paras"]:
            self.blocks.append({"heading": self._cur["heading"],
                                "text": "\n\n".join(self._cur["paras"]).strip()})
        self._cur = {"heading": "", "paras": []}

    def _flush_para(self):
        if self._buf is None:
            return
        text = _clean_text(self._buf)
        self._buf = None
        if text:
            self._cur["paras"].append(text)

    def _handle_img(self, attrs):
        src = attrs.get("data-original") or attrs.get("data-actualsrc") or attrs.get("src") or ""
        if "eeimg" in (attrs.get("class") or "") or "/equation" in src:
            tex = attrs.get("alt") or ""
            if not tex and "tex=" in src:
                tex = unquote(parse_qs(urlparse(src).query).get("tex", [""])[0])
            if tex and self._buf is not None:
                self._buf += f"${tex}$"
            return
        u = _fix_img_url(src)
        if u and u not in self.images:
            self.images.append(u)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _HEADING_TAGS:
            self._flush_para(); self._flush_section()
            self._heading_buf = ""
        elif tag == "br":
            if self._buf is not None:
                self._buf += "\n"
        elif tag == "img":
            self._handle_img(attrs)
        elif tag in _TEXT_TAGS:
            if self._text_depth == 0:
                self._flush_para()
                self._buf = ""
            self._text_depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _HEADING_TAGS:
            self._cur["heading"] = _clean_text(self._heading_buf or "")
            self._heading_buf = None
        elif tag in _TEXT_TAGS:
            self._text_depth = max(0, self._text_depth - 1)
            if self._text_depth == 0:
                self._flush_para()

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._heading_buf is not None:
            self._heading_buf += data
        if self._buf is not None:
            self._buf += data

    def close(self):
        super().close()
        self._flush_para()
        self._flush_section()


def html_to_blocks(html: str):
    """返回 (blocks, images)；blocks=[{"heading", "text"}]，images 为绝对 URL（去重保序）"""
    p = _BlocksParser()
    p.feed(html or "")
    p.close()
    return p.blocks, p.images
