"""适配器模板：复制本文件为 <platform>.py 后按注释实现。

- 文件名不要以 _ 开头，否则不会被 core.registry.load_providers() 自动发现。
- 实现前先完成 docs/ADAPTER_SPEC.md §6 可行性尽调。
"""
# from core.adapter import SearchNeeded
# from core.models import ContentResult, ItemMeta, ItemRef
# from core.registry import register


# @register
# class ExampleAdapter:
#     name = "example"       # 平台标识（config.auth 的键名同此）
#     kinds = {"video"}      # {"video"} / {"audio"} / {"article"} / {"thread"}
#
#     def __init__(self, client=None):
#         self._client = client      # 惰性初始化：import/注册阶段不要联网
#
#     @property
#     def client(self):
#         if self._client is None:
#             self._client = ...     # 构造平台客户端（带超时/重试）
#         return self._client
#
#     def match(self, url: str) -> bool:
#         """纯字符串判断，不联网"""
#         return "example.com" in (url or "")
#
#     def resolve(self, q: str):
#         """链接/名称 → ItemRef；名称类输入抛 SearchNeeded"""
#         ...
#         # raise SearchNeeded(q)
#
#     def list_items(self, ref):
#         """多集/列表展开；单项返回 [ref]（带上 sub_id/title/duration）"""
#         return [ref]
#
#     def fetch_meta(self, ref) -> "ItemMeta":
#         """内容级标题放 title（用于目录命名）；分集名放 page_part"""
#         ...
#
#     def fetch_content(self, ref) -> "ContentResult":
#         """timeline: segments=[{start,end,text}]；document: blocks=[{heading,text}]
#         status: ok / suspect / invalid / empty（见 ADAPTER_SPEC §2）"""
#         ...
#
#     def fetch_media_url(self, ref, media: str = "audio", lowest: bool = False):
#         """media=audio → Whisper 用；media=video → 抽帧复检用；无媒体返回 None"""
#         return None
