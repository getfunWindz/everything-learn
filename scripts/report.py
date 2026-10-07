"""报告骨架渲染（按内容形态分发；M0 仅 timeline）
"""

VERSION = "0.1.0"

# 已实现报告骨架的内容形态（M0 仅 timeline；document/collection 随里程碑加入）
SUPPORTED_KINDS = {"timeline"}


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

## 一句话总结

<!-- 用 1-3 句话概括视频核心内容 -->

## 知识点全览

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
  交付前运行 `python scripts/glossary.py check <报告.md> <subtitle.txt>` 校验到无遗漏

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

## 学习路径与自测

<!-- 学习收获（按知识点维度）+ 自测问题清单（每 3~5 个知识点出一道自测题，检验是否真的学会） -->

## 元信息

- 生成时间：{info['fetched_at']}
- 工具：{info['tool']}
"""


def render_template_for(kind: str, info: dict) -> str:
    """按内容形态选择报告骨架（M0 仅 timeline；document/collection 在对应里程碑实现）"""
    if kind == "timeline":
        return render_template(info)
    raise NotImplementedError(f"内容形态 {kind} 的报告骨架尚未实现（见 docs/specs 里程碑）")


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
