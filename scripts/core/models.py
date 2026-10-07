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
    reason: str = ""
