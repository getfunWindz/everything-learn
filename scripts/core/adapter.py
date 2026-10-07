"""内容源适配器协议（所有 providers 必须实现；契约测试见 tests/contract）"""
from typing import Protocol, runtime_checkable


class SearchNeeded(Exception):
    """输入是关键词而非链接：由 CLI 调 adapter 搜索后再试"""


@runtime_checkable
class SourceAdapter(Protocol):
    name: str                 # "bilibili" / "youtube" / "podcast" ...
    kinds: set                # {"video"} / {"audio"} / {"article"} / {"thread"}

    def match(self, url: str) -> bool:
        """URL 路由：是否属于本平台"""

    def resolve(self, q: str):
        """链接/名称 → ItemRef；无法直接定位时抛 SearchNeeded"""

    def list_items(self, ref):
        """ItemRef → [ItemRef]（多P/列表展开；单项返回 [ref]）"""

    def fetch_meta(self, ref):
        """ItemRef → ItemMeta"""

    def fetch_content(self, ref):
        """ItemRef → ContentResult（kind: timeline/document；status: ok/suspect/invalid/empty）"""

    def fetch_media_url(self, ref, media: str = "audio", lowest: bool = False):
        """音/视频直链；无媒体返回 None。media: audio（转写）/ video（复检抽帧）；lowest=True 取最低码率"""
