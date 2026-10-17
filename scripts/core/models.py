"""核心数据模型：平台无关的内容契约"""
from dataclasses import dataclass, field


@dataclass
class ItemRef:
    """定位到可处理的最小单元（B站分P / YouTube 列表项 / 播客单集）"""
    platform: str
    item_id: str
    url: str = ""
    sub_id: str = ""
    title: str = ""
    duration: int = 0
    extra: dict = field(default_factory=dict)   # 平台私有（如 bvid/cid）


@dataclass
class ItemMeta:
    title: str
    author: str = ""
    url: str = ""
    published: str = ""
    duration: int = 0
    desc: str = ""
    stats: dict = field(default_factory=dict)
    page: str = ""
    page_part: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Block:
    text: str
    heading: str = ""


@dataclass
class TimelineContent:
    segments: list = field(default_factory=list)   # [Segment]
    kind: str = "timeline"


@dataclass
class DocumentContent:
    blocks: list = field(default_factory=list)     # [Block]
    images: list = field(default_factory=list)
    kind: str = "document"


@dataclass
class ContentResult:
    """适配器返回的内容抓取结果（供流水线分级/降级）"""
    kind: str                       # timeline / document
    status: str                     # ok / suspect / invalid / empty
    segments: list = field(default_factory=list)   # timeline：dict {start,end,text}
    blocks: list = field(default_factory=list)     # document：dict {heading,text}
    images: list = field(default_factory=list)     # document：正文图片 URL（流水线下载到 assets/）
    reason: str = ""
    label: str = ""                 # 内容来源标注（如 “字幕(自动)”），透传到 content_info.source
    provenance: str = "unknown"     # human | official | auto | unknown | none（M13 来源分级）
    language: str = ""              # 字幕/转写语言（如 zh / en；空=未知）
    video_lines: list = field(default_factory=list)   # document（视频帖）：口播转写片段 {start,end,text}（B2）
    video_duration: float = 0.0                       # 视频帖时长（秒；B2 档位评估）
    quality_hint: str = ""                            # 适配器级质量提示（付费墙/截断等）
