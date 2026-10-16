import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
from core.artifacts import (build_info, render_content_txt, write_item_artifacts,
                           write_recheck_report)
from core.models import ItemRef, ItemMeta, TimelineContent, DocumentContent


def test_render_timeline_lines():
    out = render_content_txt(TimelineContent(segments=[{"start": 0.0, "end": 1.5, "text": "你好"}]))
    assert out == "[0.0-1.5] 你好\n"


def test_render_document_markdown():
    out = render_content_txt(DocumentContent(blocks=[{"heading": "引言", "text": "正文"}]))
    assert out.startswith("## 引言") and "正文" in out


def test_write_item_artifacts_timeline(tmp_path):
    ref = ItemRef(platform="bilibili", item_id="BV1xx", sub_id="1", url="u", title="P1")
    meta = ItemMeta(title="标题", author="UP", duration=100, page="1", page_part="P1基础")
    info = build_info(meta, ref, kind="timeline", source="字幕")
    write_item_artifacts(str(tmp_path), info,
                         TimelineContent(segments=[{"start": 0.0, "end": 1.5, "text": "你好"}]))
    saved = json.load(open(os.path.join(str(tmp_path), "content_info.json"), encoding="utf-8"))
    assert saved["platform"] == "bilibili" and saved["kind"] == "timeline"
    assert saved["source"] == "字幕" and saved["item_id"] == "BV1xx"
    sub = (tmp_path / "content.txt").read_text(encoding="utf-8")
    assert sub.startswith("[0.0-1.5] 你好")
    tpl = (tmp_path / "report_template.md").read_text(encoding="utf-8")
    assert "## 知识详解" in tpl and "标题" in tpl


def test_write_item_artifacts_document(tmp_path):
    """document 骨架已实现（M3）：章节式结构 + 无时间轴措辞"""
    ref = ItemRef(platform="zhihu", item_id="article-1", url="u")
    meta = ItemMeta(title="文章标题", author="答主")
    info = build_info(meta, ref, kind="document", source="正文")
    write_item_artifacts(str(tmp_path), info, DocumentContent(blocks=[{"heading": "引言", "text": "正文"}]))
    tpl = (tmp_path / "report_template.md").read_text(encoding="utf-8")
    assert "知识地图" in tpl and "延伸阅读" in tpl and "原文摘录" in tpl
    assert "[00:00" not in tpl and "时间轴" not in tpl


def test_render_timeline_vision_source_annotations():
    """T15：vision 补充/校正行追加行尾标注（校正含 corrected_from）"""
    out = render_content_txt(TimelineContent(segments=[
        {"start": 0.0, "end": 1.5, "text": "你好"},
        {"start": 10.0, "end": 11.0, "text": "画面公式", "source": "vision补充"},
        {"start": 20.0, "end": 25.0, "text": "注意力机制", "source": "vision校正",
         "corrected_from": "注意利机制"},
    ]))
    assert out == ("[0.0-1.5] 你好\n"
                   "[10.0-11.0] 画面公式 【画面补充】\n"
                   "[20.0-25.0] 注意力机制 【画面校正：原「注意利机制」】\n")


def test_render_timeline_vision_source_attribute_object():
    """T15 评审：属性对象（非 dict）行的 source/corrected_from 也应被识别"""
    class Seg:
        start = 0.0
        end = 1.0
        text = "注意力机制"
        source = "vision校正"
        corrected_from = "注意利机制"

    out = render_content_txt(TimelineContent(segments=[Seg()]))
    assert out == "[0.0-1.0] 注意力机制 【画面校正：原「注意利机制」】\n"


def test_render_timeline_vision_correction_without_corrected_from():
    """T15 评审：corrected_from 为空 → 仅【画面校正】，不带「原「」」"""
    out = render_content_txt(TimelineContent(segments=[
        {"start": 0.0, "end": 1.0, "text": "注意力机制", "source": "vision校正"},
        {"start": 2.0, "end": 3.0, "text": "多头机制", "source": "vision校正",
         "corrected_from": ""},
    ]))
    assert out == ("[0.0-1.0] 注意力机制 【画面校正】\n"
                   "[2.0-3.0] 多头机制 【画面校正】\n")


def test_render_timeline_lines_without_vision_source_unchanged():
    """T15：无 source（或非 vision 来源）的行不追加任何标注"""
    out = render_content_txt(TimelineContent(segments=[
        {"start": 0.0, "end": 1.0, "text": "a"},
        {"start": 2.0, "end": 3.0, "text": "b", "source": "字幕"},
    ]))
    assert out == "[0.0-1.0] a\n[2.0-3.0] b\n"
    assert "【" not in out


def test_write_recheck_report_roundtrip(tmp_path):
    """T15：recheck_report.json 落盘（目录自动创建、中文不转义、可读回）"""
    data = {"tier": 1, "budget": {"user_choice": "B"},
            "same_source": {"checked": True, "passed": True},
            "uncertain": [{"range": [0.0, 30.0], "kind": "uncertain", "text": "画面过暗"}]}
    out_dir = tmp_path / "nested" / "item"
    write_recheck_report(str(out_dir), data)
    p = out_dir / "recheck_report.json"
    assert p.exists()
    raw = p.read_text(encoding="utf-8")
    assert "画面过暗" in raw and "\\u" not in raw  # ensure_ascii=False
    assert json.loads(raw) == data


def test_render_document_template_sections():
    from report import render_template_for
    ref = ItemRef(platform="zhihu", item_id="a1", url="u")
    meta = ItemMeta(title="文章", author="答主", published="2024-01-01")
    info = build_info(meta, ref, kind="document", source="正文")
    tpl = render_template_for("document", info)
    for kw in ("一句话总结", "知识地图", "知识详解", "关键概念", "金句", "学习路径", "延伸阅读", "元信息"):
        assert kw in tpl


def test_render_document_template_recheck_section_when_present():
    """B2：document 带复检 meta 时渲染「复检说明」小节"""
    from report import render_template_for
    ref = ItemRef(platform="x_twitter", item_id="t1", url="u")
    meta = ItemMeta(title="推文", author="NASA")
    info = build_info(meta, ref, kind="document", source="正文",
                      recheck={"tier": 1, "score": 0.2, "reasons": ["density=1.0/min"],
                               "lite": True, "skipped": "文档形态：仅档位评估（未执行窗口复检）"})
    tpl = render_template_for("document", info)
    assert "## 复检说明" in tpl
    assert "档位：1" in tpl


def test_render_document_template_no_recheck_no_section():
    """B2：无复检 meta 的 document 不渲染「复检说明」"""
    from report import render_template_for
    ref = ItemRef(platform="zhihu", item_id="a1", url="u")
    meta = ItemMeta(title="文章", author="答主")
    info = build_info(meta, ref, kind="document", source="正文")
    assert "## 复检说明" not in render_template_for("document", info)


def test_render_timeline_template_recheck_section():
    """T16：timeline 骨架在正文前渲染「复检说明」（档位/评分/理由/覆盖/同源校验）"""
    from report import render_template_for
    ref = ItemRef(platform="bilibili", item_id="BV1r", url="u")
    meta = ItemMeta(title="视频", author="UP", duration=600, published="2024-01-01")
    info = build_info(meta, ref, kind="timeline", source="whisper", recheck={
        "tier": 1, "effective_tier": 2, "score": 0.72,
        "reasons": ["关键点密度高", "whisper 来源"],
        "coverage_pct": 90.0, "uncovered": [[60.0, 120.0]],
        "same_source": {"checked": True, "passed": True, "avg": 0.9},
    })
    tpl = render_template_for("timeline", info)
    assert "## 复检说明" in tpl
    assert "档位：2（评分 0.72）" in tpl          # effective_tier 优先于 tier
    assert "理由：关键点密度高, whisper 来源" in tpl
    assert "覆盖：90.0%；未覆盖区间：" in tpl
    assert "00:01:00–00:02:00" in tpl               # 未覆盖区间转时间戳（T16 配套）
    assert "同源校验：0.9" in tpl
    assert "【画面补充】" in tpl and "【画面校正】" in tpl  # R13：标注必须保留
    assert "recheck_report.json" in tpl
    assert tpl.index("## 复检说明") < tpl.index("## 一句话总结")  # 正文之前


def test_render_timeline_template_recheck_uncovered_timestamped():
    """T16 配套：未覆盖区间列表转时间戳（多处用「、」分隔；空列表仍为「无」）"""
    from report import render_template_for
    ref = ItemRef(platform="bilibili", item_id="BV4", url="u")
    meta = ItemMeta(title="视频4", author="UP", duration=4000, published="2024-01-01")
    info = build_info(meta, ref, kind="timeline", source="whisper", recheck={
        "tier": 1, "uncovered": [[0, 5], [3661, 3725.4]]})
    tpl = render_template_for("timeline", info)
    assert "未覆盖区间：00:00:00–00:00:05、01:01:01–01:02:05" in tpl
    info2 = build_info(meta, ref, kind="timeline", source="whisper",
                       recheck={"tier": 1, "uncovered": []})
    assert "未覆盖区间：无" in render_template_for("timeline", info2)


def test_render_timeline_template_recheck_missing():
    """T16：recheck 缺省时仍渲染「复检说明」并注明未记录"""
    from report import render_template_for
    ref = ItemRef(platform="bilibili", item_id="BV2", url="u")
    meta = ItemMeta(title="视频2", author="UP", duration=60, published="2024-01-01")
    info = build_info(meta, ref, kind="timeline", source="字幕")
    assert "recheck" not in info
    tpl = render_template_for("timeline", info)
    assert "## 复检说明" in tpl
    assert "档位：未记录" in tpl
    assert "理由：未记录" in tpl
    assert "同源校验：未执行" in tpl
    assert "未覆盖区间：无" in tpl


def test_render_timeline_template_recheck_tier_fallback():
    """T16：无 effective_tier 时回退判决档；无 reasons 时注明未记录"""
    from report import render_template_for
    ref = ItemRef(platform="bilibili", item_id="BV3", url="u")
    meta = ItemMeta(title="视频3", author="UP", duration=60, published="2024-01-01")
    info = build_info(meta, ref, kind="timeline", source="whisper",
                      recheck={"tier": 2})
    tpl = render_template_for("timeline", info)
    assert "档位：2（评分 -）" in tpl
    assert "理由：未记录" in tpl
