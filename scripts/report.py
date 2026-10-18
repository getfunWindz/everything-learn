"""报告骨架渲染（按内容形态分发；M0 仅 timeline）
"""

try:
    from version import VERSION
except Exception:
    VERSION = "0.4.23"

# 已实现报告骨架的内容形态（M0 timeline；M3 增补 document）
SUPPORTED_KINDS = {"timeline", "document"}


def _fmt_hms(sec) -> str:
    """秒 → HH:MM:SS（负值钳到 0）"""
    total = max(0, int(round(float(sec))))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_uncovered(raw) -> str:
    """未覆盖区间 [[start, end], ...] → 「00:01:00–00:02:00、…」；空/非法 → 「无」"""
    if not isinstance(raw, (list, tuple)):
        return str(raw) if raw else "无"
    parts = []
    for rng in raw:
        try:
            parts.append(f"{_fmt_hms(rng[0])}–{_fmt_hms(rng[1])}")
        except (TypeError, IndexError, ValueError):
            continue
    return "、".join(parts) if parts else "无"


def _quality_hint_line(info: dict) -> str:
    """质量提示行（内容过短/图片为主/低配转写等）；无则空串"""
    qh = info.get("quality_hint") or {}
    if isinstance(qh, str) and qh.strip():
        return "- ⚠️ 内容完整性：" + qh.strip() + chr(10)
    if isinstance(qh, dict) and qh:
        reasons_txt = "；".join(qh.get("reasons") or []) or "质量信号偏弱"
        return ("- ⚠️ 转写质量提示（低配模式）：" + reasons_txt + "；"
                "建议升级 medium 并补全依赖（详见 content_info.json 的 quality_hint）"
                + chr(10))
    tier_note = (info.get("quality") or {}).get("tier_note")
    if tier_note:
        return f"- ℹ️ 转写档位提示：{tier_note}\n"
    return ""


def _recheck_section(info: dict) -> str:
    """复检说明小节（T16）：recheck 缺省时也渲染，注明「未记录」"""
    rc = info.get("recheck") or {}
    if (info.get("kind") or "timeline") == "document" and not rc:
        # 图文/帖子没有画面/音频层：如实写 n/a，避免引用不存在的 recheck_report.json
        return ("## 复检说明\n\n"
                "- 图文内容：无画面/音频复检（n/a）。\n"
                + _quality_hint_line(info))
    tier = rc.get("effective_tier") or rc.get("tier") or "未记录"
    score = rc.get("score")
    score = "-" if score is None else score
    reasons = ", ".join(rc.get("reasons") or []) or "未记录"
    coverage = rc.get("coverage_pct")
    coverage = "-" if coverage is None else coverage
    uncovered = _fmt_uncovered(rc.get("uncovered"))
    same_source = (rc.get("same_source") or {}).get("avg")
    if same_source is None:
        same_source = "未执行"
    quality_line = _quality_hint_line(info)
    return ("## 复检说明\n\n"
            f"- 档位：{tier}（评分 {score}）\n"
            f"- 理由：{reasons}\n"
            f"- 覆盖：{coverage}%；未覆盖区间：{uncovered}\n"
            f"- 同源校验：{same_source}\n"
            f"{quality_line}"
            "- 复检补充/校正：正文中带【画面补充】【画面校正】标注，"
            "撰写报告时**必须保留**并解释；不确定项见 recheck_report.json")


def render_template(info: dict) -> str:
    return f"""# {info['title']}

> 视频信息

| 项目 | 内容 |
|------|------|
| 标题 | {info['title']} |
| 作者/UP主 | {info['author']} |
| 内容ID | {info['item_id']} |
| 链接 | {info['url']} |
| 总时长 | {info['duration_seconds']} 秒 |
| 发布日期 | {info['published']} |
| 分P | {info['page']}/{info['page_count']} |
| 播放量 | {info['stats'].get('view', 0)} |
| 点赞 | {info['stats'].get('like', 0)} |
| 收藏 | {info['stats'].get('favorite', 0)} |
| 内容来源 | {info['source']} |

> 简介：{info['desc'] or '（无）'}

{_recheck_section(info)}

## 一句话总结

<!-- 用 1-3 句话概括视频核心内容 -->

## 学习地图（知识点全览）

<!-- 学习地图：按出现顺序列出本视频全部知识点（编号），如：
1. PyCharm 安装与 Python 环境配置
2. 第一行代码与两种运行方式
3. 四种基本数据类型（字符串/整型/浮点/布尔）
...（覆盖视频全部内容，不遗漏） -->

## 知识详解

<!-- 按时间轴切段（每段 3~10 分钟，段标题标注时间戳起止），逐段展开：
### 段1｜[00:00-05:12] 段主题

#### 知识点1：知识点名称（[02:30] 首次出现时间）
- **定义**：官方/标准表述（正式定义或教科书式描述）
- **通俗**：一句话人话解释（新手视角）
- **细节**：2~4 条浓缩要点（只留知识本身，删除口播废话/寒暄/重复解释）
- **例子**：视频中的代码/案例（如有）
- **彩蛋**：默认省略（不加）；仅当用户明确要求「幽默点/彩蛋/有趣的点」时，才从字幕摘录趣味原话并添加
- **延伸**：相关概念的深入拓展（主流/高级知识点 2~4 条，每条 = 概念 + 使用场景/一句话解释，
  如：列表→切片/列表推导式/排序 sort；字典→视图对象/合并 update/JSON 序列化；
  函数→默认参数/lambda/装饰器；类→继承/多态/魔术方法；简单知识点 1 条或省略）
- **名词注释（R11，必填）**：本知识点中出现的、前置未提及的专业名词逐条注释，
  先查 references/glossary.json 术语库（命中直接引用），未命中新写并自动沉淀入库；格式：
  ```
  **名词注释**：
  - teacher forcing：训练时用真实标签而非模型自身输出作为下一步输入
  - KV Cache：键值缓存，推理时缓存注意力 K/V 矩阵避免重复计算
  ```
  交付前运行 `python scripts/glossary.py check <报告.md> <content.txt>` 校验到无遗漏

#### 知识点2：...
（该时间段内原作者讲到的每个知识点都必须列出，不省略）

### 段2｜[05:12-10:00] 段主题
... -->

## 关键概念 / 术语解释表

| 概念 | 通俗解释 |
|------|---------|
|  |  |

## 金句与重要观点

## 原话摘录

<!-- 从字幕中挑选 5~10 条有教学价值/启发性的原话，逐条附上时间点，如：
- [12:34] “原话内容”
-->

## 自测（每 3~5 个知识点一题）

<!-- 学习收获（按知识点维度）+ 自测问题清单（每 3~5 个知识点出一道自测题，检验是否真的学会） -->

## 元信息

- 生成时间：{info['fetched_at']}
- 工具：{info['tool']}
"""


def render_document_template(info: dict) -> str:
    return f"""# {info['title']}

> 图文信息

| 项目 | 内容 |
|------|------|
| 标题 | {info['title']} |
| 作者 | {info['author']} |
| 内容ID | {info['item_id']} |
| 链接 | {info['url']} |
| 发布日期 | {info['published']} |
| 阅读/赞同/评论 | {info['stats'].get('view', 0)} / {info['stats'].get('voteup', 0)} / {info['stats'].get('comment', 0)} |
| 内容来源 | {info['source']} |

> 简介：{info['desc'] or '（无）'}

{_recheck_section(info)}
## 一句话总结

<!-- 用 1-3 句话概括文章核心内容 -->

## 学习地图（知识点全览）

<!-- 按原文章节列出全部知识点（编号），如：
1. 引言：问题背景
2. 核心方法 A（含子点 a/b/c）
...（覆盖全文，不遗漏） -->

## 知识详解

<!-- 按原文章节切段，逐段展开：
### 段1｜章节名

#### 知识点1：知识点名称（见「小节标题」）
- **定义**：官方/标准表述（正式定义或教科书式描述）
- **通俗**：一句话人话解释（新手视角）
- **细节**：2~4 条浓缩要点（只留知识本身，删除口水话/重复）
- **例子**：文中的示例/数据（如有）
- **延伸**：主流/高级知识点深入 2~4 条（概念 + 使用场景）；简单知识点 1 条或省略
- **名词注释（R11，必填）**：本知识点中出现的、前置未提及的专业名词逐条注释，
  先查 references/glossary.json 术语库（命中直接引用），未命中新写并
  `python scripts/glossary.py add` 沉淀；交付前运行
  `python scripts/glossary.py check <报告.md> <content.txt>` 校验到 0 遗漏

（该章节内每个知识点都必须列出，不省略） -->

## 关键概念 / 术语解释表

| 概念 | 通俗解释 |
|------|---------|
|  |  |

## 金句与重要观点

## 原文摘录

<!-- 从正文挑选 5~10 条有教学价值/启发性的原文，逐条附章节定位，如：
- （见「小节标题」）“原文内容”
-->

## 自测（每 3~5 个知识点一题）

<!-- 学习收获（按知识点维度）+ 自测问题清单（每 3~5 个知识点出一道自测题，附参考答案位置提示） -->

## 延伸阅读

<!-- 文中推荐的相关资料/概念链接（如有） -->

## 元信息

- 生成时间：{info['fetched_at']}
- 工具：{info['tool']}
"""


def render_template_for(kind: str, info: dict) -> str:
    """按内容形态选择报告骨架（timeline/document；collection 未来里程碑实现）"""
    if kind == "timeline":
        return render_template(info)
    if kind == "document":
        return render_document_template(info)
    raise NotImplementedError(
        f"内容形态 {kind} 的报告骨架尚未实现；合集请用 run --all，单条请按 SKILL 报告规则撰写")


def render_batch_template(summary: dict) -> str:
    """批量总报告骨架：总览 + 按P分章 + 失败标注"""
    lines = [f"# {summary['title']}", "", "> 批量学习报告（按分P分章）", "",
             "| 项目 | 内容 |", "|------|------|",
             f"| 作者/UP主 | {summary['author']} |",
             f"| 内容ID | {summary['item_id']} |",
             f"| 链接 | {summary['url']} |",
             f"| 分P | {summary['page_count']} |", "",
             "## 课程总览", "", "<!-- 读完各 P 字幕后的整体概述 -->", ""]
    for r in summary.get("pages") or []:
        lines += [f"## P{r['page']}：{r['part']}", ""]
        if r["status"] != "ok":
            lines += [f"> ⚠️ 该 P 获取失败（{r['status']}：{r.get('error', '未知')}），建议手动观看", ""]
        else:
            lines += [f"> 内容来源：{r['source']}（{r['line_count']} 行）", "",
                      "<!-- 要点 + 详细解释 + 例子 -->", ""]
    lines += ["## 总术语表", "", "| 概念 | 解释 |", "|------|------|", "",
              "## 学习路径与建议", "", "## 元信息", "",
              f"- 生成时间：{summary.get('batch_fetched_at', '')}",
              f"- 工具：{summary.get('tool', '')}", ""]
    return "\n".join(lines)


# ---------------- 自动基线报告（2026-10-17） ----------------
# 背景：CLI 只产出 report_template.md（骨架），报告正文依赖 agent 撰写；用户侧
# 输出目录因此“没有报告本体”。这里提供确定性基线：机械整理 content.txt（不压缩、
# 不臆造），保证每次 run 都有可读的 <标题>学习报告.md；agent 可在此基础上深化。

import os
import re

_LINE_RE = re.compile(r"^\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]\s*(.*)$")


def baseline_report_path(out_dir: str, info: dict) -> str:
    from core.fsutil import safe_title
    name = safe_title(info.get("title") or "未命名", 80)
    return os.path.join(out_dir, f"{name}学习报告.md")


def _parse_timeline(content_text: str):
    out = []
    for raw in (content_text or "").splitlines():
        m = _LINE_RE.match(raw.strip())
        if m:
            out.append({"start": float(m.group(1)), "end": float(m.group(2)),
                        "text": (m.group(3) or "").strip()})
        elif raw.strip():
            out.append({"start": None, "end": None, "text": raw.strip()})
    return out


def _segments(items, span_sec=300):
    """按时间桶切段（默认 5 分钟）；返回 [{start,end,items}]"""
    segs = []
    for it in items:
        if it["start"] is None:
            if not segs:
                segs.append({"start": 0.0, "end": 0.0, "items": []})
            segs[-1]["items"].append(it)
            continue
        idx = int(it["start"] // span_sec)
        while len(segs) <= idx:
            segs.append({"start": len(segs) * span_sec,
                         "end": (len(segs) + 1) * span_sec, "items": []})
        segs[idx]["items"].append(it)
    return [s for s in segs if s["items"]]


def _summary(items) -> str:
    texts = [i["text"] for i in items if i["text"] and not i["text"].startswith("#")][:4]
    s = " ".join(texts)
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:300] + "…") if len(s) > 300 else (s or "（无正文）")


def _quality_line(info) -> str:
    qh = info.get("quality_hint")
    if isinstance(qh, dict):
        reasons = "；".join(qh.get("reasons") or []) or "质量信号偏弱"
        return f"- ⚠️ 质量提示：{reasons}\n"
    if isinstance(qh, str) and qh.strip():
        return f"- ⚠️ 内容完整性：{qh.strip()}\n"
    return ""


def render_baseline_report(info: dict, content_text: str) -> str:
    kind = info.get("kind") or "timeline"
    items = _parse_timeline(content_text)
    is_timeline = kind == "timeline"
    segs = _segments(items) if is_timeline else []
    out = [f"# {info.get('title') or '未命名'}", "",
           "> 自动基线报告：由 content.txt 机械整理（不压缩、不臆造）；"
           "可按 SKILL 的 R1-R13 继续深化。", "",
           "| 项目 | 内容 |", "|------|------|",
           f"| 作者/来源 | {info.get('author') or ''} |",
           f"| 内容ID | {info.get('item_id') or ''} |",
           f"| 链接 | {info.get('url') or ''} |",
           f"| 时长 | {info.get('duration_seconds') or ''} 秒 |",
           f"| 发布日期 | {info.get('published') or ''} |",
           f"| 内容来源 | {info.get('source') or ''} |", "",
           _recheck_section(info), "",
           "## 一句话总结", "", _summary(items), "",
           "## 学习地图（知识点全览）", ""]
    if is_timeline:
        for n, seg in enumerate(segs, 1):
            first = next((i["text"] for i in seg["items"] if i["text"]), "（无内容）")
            out.append(f"{n}. [{_fmt_hms(seg['start'])}–{_fmt_hms(seg['end'])}] {first[:50]}")
    else:
        headings = [l.strip() for l in (content_text or "").splitlines()
                    if l.strip().startswith("#")]
        for n, line in enumerate(headings[:80], 1):
            out.append(f"{n}. {line.lstrip('# ').strip()[:50]}")
    out += ["", "## 知识详解", ""]
    if is_timeline:
        for n, seg in enumerate(segs, 1):
            first = next((i["text"] for i in seg["items"] if i["text"]), "段主题")
            out += [f"### 段{n}｜[{_fmt_hms(seg['start'])}–{_fmt_hms(seg['end'])}] {first[:30]}", ""]
            for it in seg["items"]:
                if it["text"]:
                    out.append(f"- [{_fmt_hms(it['start'])}] {it['text']}")
            out.append("")
    else:
        block = 0
        for raw in (content_text or "").splitlines():
            line = raw.rstrip()
            if not line:
                continue
            if line.startswith("#"):
                block += 1
                out += [f"### 段{block}｜{line.lstrip('# ').strip()}", ""]
            else:
                out.append(f"- {line}")
        out.append("")
    quotes = [i for i in items
              if i["text"] and not i["text"].startswith("#") and len(i["text"]) >= 20]
    quotes = sorted(quotes, key=lambda x: -len(x["text"]))[:5]
    quotes.sort(key=lambda x: (x["start"] is None, x["start"] or 0))
    out += ["## 关键概念 / 术语解释表", "",
            "| 概念 | 通俗解释 |", "|------|---------|",
            "| （自动基线未抽取；请按 R11 补充术语库） |  |", "",
            "## 金句与重要观点", ""]
    for q in quotes:
        if is_timeline and q["start"] is not None:
            out.append(f"- [{_fmt_hms(q['start'])}] {q['text']}")
        else:
            out.append(f"- {q['text']}")
    out += ["", "## 原话摘录", ""]
    out += [f"- {q['text']}" for q in quotes[:3]] or ["（无）"]
    out += ["", "## 自测（每 3~5 个知识点一题）", "",
            "1. 按「知识详解」的分段顺序复述每段主题。",
            "2. 选 3 个你最有兴趣的知识点，各写一句自己的解释。",
            "3. 对照「金句与重要观点」，检查是否有遗漏的结论。", "",
            "## 元信息", "",
            f"- 生成时间：{info.get('fetched_at') or ''}",
            f"- 工具：{info.get('tool') or ''}",
            "- 报告类型：自动基线（如需 R1-R13 详解版，请由 agent 继续撰写）", ""]
    return "\n".join(out)


def write_baseline_report(out_dir: str, info: dict, force: bool = False):
    """写 <标题>学习报告.md（默认不覆盖已有报告）；返回路径或 None"""
    src = os.path.join(out_dir, "content.txt")
    if not os.path.exists(src):
        return None
    path = baseline_report_path(out_dir, info)
    if os.path.exists(path) and not force:
        return path
    text = open(src, encoding="utf-8").read()
    try:
        from core.fsutil import atomic_write_text
        atomic_write_text(path, render_baseline_report(info, text))
    except Exception:
        return None
    return path


def write_batch_baseline(base: str, summary: dict) -> str:
    """合集总报告（基线）：把各 P 的基线报告拼成一份；不覆盖已有文件"""
    import json
    from core.fsutil import safe_title
    from core.fsutil import atomic_write_text
    path = os.path.join(base, safe_title(summary.get("title") or "合集", 80) + " 合集学习报告.md")
    if os.path.exists(path):
        return path
    parts = [f"# {summary.get('title') or '合集'}", "",
             "> 合集自动基线报告：由各 P 的 content.txt 机械整理（不压缩、不臆造）。", "",
             "| 项目 | 内容 |", "|------|------|",
             f"| 作者/UP主 | {summary.get('author') or ''} |",
             f"| 内容ID | {summary.get('item_id') or ''} |",
             f"| 分P | {summary.get('page_count') or ''} |", ""]
    for r in summary.get("pages") or []:
        page = str(r.get("page") or "")
        pdir = os.path.join(base, f"P{int(page or 1):02d}")
        info_p = os.path.join(pdir, "content_info.json")
        content_p = os.path.join(pdir, "content.txt")
        parts += [f"## P{page}｜{r.get('part') or ''}", ""]
        if r.get("status") != "ok" or not os.path.exists(info_p) or not os.path.exists(content_p):
            parts += [f"> ⚠️ 该 P 未成功（{r.get('status')}：{r.get('error') or ''}）", ""]
            continue
        try:
            info = json.load(open(info_p, encoding="utf-8"))
            text = open(content_p, encoding="utf-8").read()
            body = render_baseline_report(info, text)
            body = re.sub(r"^# .*\n\n", "", body, count=1)   # 去掉重复 H1
            parts += [body, ""]
        except Exception as e:
            parts += [f"> ⚠️ 该 P 报告生成失败：{str(e)[:80]}", ""]
    parts += ["## 元信息", "",
              f"- 生成时间：{summary.get('batch_fetched_at') or ''}",
              f"- 工具：{summary.get('tool') or ''}",
              "- 报告类型：合集自动基线", ""]
    atomic_write_text(path, "\n".join(parts))
    return path
