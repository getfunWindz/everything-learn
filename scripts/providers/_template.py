"""适配器模板：复制本文件为 <platform>.py 后按注释实现。

- 文件名不要以 _ 开头，否则不会被 core.registry.load_providers() 自动发现。
- 实现前先完成 docs/ADAPTER_SPEC.md §6 可行性尽调。
- 生成契约测试：python scripts/gen_contract_test.py <name>
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
#         """统一多模态复检通道（必接）：media=audio → Whisper 用；media=video → core 抽帧复检用。
#         有画面平台返回可抽帧视频流（lowest=True 取最低码率）；无画面返回 None（core 自动跳过）。
#         复检逻辑一律复用 core，不要在适配器内自建。"""
#         return None
#
#     # ---- 可选钩子（按需）----
#     def sniff(self, url: str) -> bool:
#         """match 全未命中时的网络探测兜底（仅 http(s)；仅看 content-type/首段字节，别做重活）"""
#         return False
#
#     def reload_auth(self):
#         """登录态自动恢复后重新注入 cookie（pipeline 自动重试用；如 load_cookie(self.session)）"""
#         ...
#
#     def fetch_media_urls(self, ref, media="audio", lowest=False):
#         """多候选媒体直链（跨主机容灾；默认回退 fetch_media_url 单条）"""
#         return [self.fetch_media_url(ref, media=media, lowest=lowest)]
#
#     def media_headers(self):
#         """媒体下载（Whisper/抽帧）请求头；平台 CDN 校验 UA/Referer 时必须"""
#         return None
#
#     def fetch_root_meta(self, ref, refs):
#         """合集根 meta（收藏夹/列表/频道等账号级列表用列表自身 meta；默认取 refs[0]）"""
#         return self.fetch_meta(refs[0])
#
# # 平台声明（可选）：language_hint="zh"、has_video=True、accept_auto_subtitle=False、
# # media_budget_sec=45；ContentResult 可带 quality_hint（会员墙/截断等，报告优先展示）
#
# # 需 cookie 的平台声明导出规格（见 docs/ADAPTER_SPEC.md §4）：
# # COOKIE_EXPORT = {"url": "...", "read_url": "...", "required": [...], "ready": [...],
# #                  "wanted": [...], "file": ".<platform>_cookie",
# #                  "verify": {"url": "...", "expect": "..."}}   # 必要时加登录校验
#
# # 并指定平台缓速（越易触发风控越慢）：core.throttle 默认间隔 + config.rate_limits.<platform>