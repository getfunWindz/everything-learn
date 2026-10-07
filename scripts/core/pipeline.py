"""通用流水线：缓存 → 内容获取（字幕/正文）→ 复检/转写 → 落盘 + 缓存。

平台差异全部由 SourceAdapter 承担；本模块不感知具体平台。
"""
import json
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime

from core import pagespec
import report
from core import artifacts, cache, config
from core.models import DocumentContent, TimelineContent

_LOGGER = logging.getLogger("el")


class PipelineError(Exception):
    """流水线级错误（无法路由/无法定位等）"""


@dataclass
class RunOptions:
    out_root: str = ""
    page: int = None
    pages: str = None
    all_: bool = False
    lang: str = None
    model: str = None
    no_cache: bool = False
    no_whisper: bool = False
    no_vision_check: bool = False
    vad: bool = None
    resume: bool = False
    interval: float = 1.5
    page_count: int = 1          # 由 run_input 填入（全部 item 数）


def _safe_name(title: str, maxlen: int = 60) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")[:maxlen] or "untitled"


def _progress_printer():
    def _progress(done, total):
        pct = int(done / total * 100) if total else 0
        if pct % 10 == 0:
            print(f"    转写进度 {pct}%", file=sys.stderr, end="\r")
    return _progress


def _item_out_dir(meta, ref, out_root, single, opts) -> str:
    if single:
        return os.path.join(out_root, _safe_name(meta.author),
                            f"{datetime.now():%Y-%m-%d}_{_safe_name(meta.title)}"
                            + (f"_P{ref.sub_id}" if opts.page_count > 1 and ref.sub_id else ""))
    return os.path.join(out_root, f"P{int(ref.sub_id or 1):02d}")


# ---------------- 多模态复检（平台无关：经 adapter.fetch_media_url 取画面流） ----------------

def _want_subtitle_check(cfg_vision: dict, status: str) -> bool:
    """是否执行字幕归属复检：always/true 恒查；off/false 关闭；auto（默认）仅可疑字幕"""
    if not cfg_vision.get("enabled") or not cfg_vision.get("api_key"):
        return False
    mode = cfg_vision.get("subtitle_check", "auto")
    if mode in (False, "off", "false", None):
        return False
    if mode in (True, "always", "true"):
        return True
    return status == "suspect"


def _vision_verify_subtitle(adapter, ref, cfg_vision: dict, lines: list):
    """字幕归属复检：最低码率画面流 → 首/中/尾 3 帧 → 多模态判定。
    返回 True=一致（采信）/ False=不一致（强制 Whisper）/ None=未执行"""
    import transcriber as tr
    import vision
    if not lines:
        return None
    print("  · 字幕可疑：多模态归属复检（开头/中间/结尾 3 帧）……", file=sys.stderr)
    url = adapter.fetch_media_url(ref, media="video", lowest=True)
    if not url:
        return None
    with tempfile.TemporaryDirectory() as td:
        vp = os.path.join(td, "video.m4s")
        tr.download_audio(url, vp)  # 通用流式下载（任意二进制）
        return vision.check_subtitle_match(cfg_vision, vp, lines, ref.duration)


def _vision_check(adapter, ref, cfg_vision: dict, lines: list):
    """内容完整性复检：画面流 → 多模态找遗漏材料 → 补充行（失败返回 None，不阻塞）"""
    import transcriber as tr
    import vision
    if not cfg_vision.get("enabled") or not cfg_vision.get("api_key"):
        return None
    url = adapter.fetch_media_url(ref, media="video")
    if not url:
        return None
    print("复检：下载视频画面流，抽帧核对转写遗漏……", file=sys.stderr)
    with tempfile.TemporaryDirectory() as td:
        vp = os.path.join(td, "video.m4s")
        tr.download_audio(url, vp)
        result = vision.check_transcript(cfg_vision, vp, lines or [], ref.duration,
                                         max_frames=cfg_vision.get("max_frames", 30),
                                         max_rounds=cfg_vision.get("max_rounds", 2))
    sup = result.get("supplements") or []
    print(f"复检：{'发现 %d 条遗漏材料，已补充' % len(sup) if sup else '无遗漏'}", file=sys.stderr)
    return sup


# ---------------- 单 item 处理 ----------------

def process_item(adapter, ref, out_root, opts: RunOptions, single: bool = False):
    """处理单个 item → (status, source, line_count, out_dir, error)"""
    label = str(ref.sub_id or "1")
    # 1) 缓存命中直接复用
    if not opts.no_cache:
        cached = cache.get_cached(ref.platform, ref.item_id, ref.sub_id)
        if cached:
            payload = cached if isinstance(cached, dict) else {"kind": "timeline", "items": cached}
            try:
                meta = adapter.fetch_meta(ref)
            except Exception as e:
                return "failed", "", 0, "", str(e)[:100]
            out_dir = _item_out_dir(meta, ref, out_root, single, opts)
            if payload.get("kind", "timeline") not in report.SUPPORTED_KINDS:
                return "failed", "cache", 0, "", f"内容形态 {payload.get('kind')} 的报告骨架尚未实现"
            info = artifacts.build_info(meta, ref, kind=payload.get("kind", "timeline"),
                                        source="cache", page_count=opts.page_count)
            content = (DocumentContent(blocks=payload.get("items") or [])
                       if payload.get("kind") == "document"
                       else TimelineContent(segments=payload.get("items") or []))
            artifacts.write_item_artifacts(out_dir, info, content)
            return "ok", "cache", len(payload.get("items") or []), out_dir, ""
    # 2) 取内容（字幕/正文）
    try:
        res = adapter.fetch_content(ref)
        meta = adapter.fetch_meta(ref)
    except Exception as e:
        err = str(e)[:100]
        print(f"  P{label} 内容获取失败：{err}", file=sys.stderr)
        return "failed", "", 0, "", err
    src = res.label or ("document" if res.kind == "document" else "字幕")
    if res.kind == "document":
        if res.kind not in report.SUPPORTED_KINDS:
            return "failed", src, 0, "", f"内容形态 {res.kind} 的报告骨架尚未实现（M3）"
        if res.status not in ("ok", "suspect") or not res.blocks:
            print(f"  P{label} 正文不可用（{res.reason or res.status}）", file=sys.stderr)
            return "failed", src, 0, "", res.reason or "正文提取为空"
        out_dir = _item_out_dir(meta, ref, out_root, single, opts)
        info = artifacts.build_info(meta, ref, kind="document", source=src,
                                    page_count=opts.page_count)
        artifacts.write_item_artifacts(out_dir, info, DocumentContent(blocks=res.blocks))
        if not opts.no_cache:
            cache.set_cached(ref.platform, ref.item_id, ref.sub_id,
                             {"kind": "document", "items": res.blocks})
        return "ok", src, len(res.blocks), out_dir, ""
    # 3) timeline：分级 + 归属复检
    lines = list(res.segments) if res.status in ("ok", "suspect") else []
    if res.status == "suspect":
        cfgv0 = config.load_config().get("vision", {})
        verdict = None
        if not opts.no_vision_check and _want_subtitle_check(cfgv0, res.status):
            try:
                verdict = _vision_verify_subtitle(adapter, ref, cfgv0, lines)
            except Exception as e:
                print(f"  P{label} 字幕归属复检失败（保守降级）：{str(e)[:80]}", file=sys.stderr)
        if verdict is True:
            src = "字幕+vision复核"
            print(f"  P{label} 字幕归属复检通过（原判可疑：{res.reason}）", file=sys.stderr)
        else:
            why = "归属复检不一致" if verdict is False else f"可疑（{res.reason}）"
            print(f"  P{label} 字幕{why} → 强制 Whisper", file=sys.stderr)
            lines = []
    elif res.status == "invalid":
        print(f"  P{label} 字幕不可信（{res.reason}）→ 强制 Whisper", file=sys.stderr)
        lines = []
    # 4) Whisper 兜底
    if not lines:
        if opts.no_whisper:
            return "no_subtitle", src, 0, "", "无内容且 --no-whisper 跳过转写"
        try:
            import transcriber
            media = adapter.fetch_media_url(ref, media="audio")
            if not media:
                return "failed", src, 0, "", "该来源无媒体流可转写"
            print(f"  P{label} 无内容，启用 Whisper 转写……", file=sys.stderr)
            cfg = config.load_config()
            mode = cfg.get("whisper_vad", "auto")
            vad_mode = opts.vad if opts.vad is not None else (None if mode == "auto" else bool(mode))
            lines = transcriber.transcribe_url(
                media,
                model_size=opts.model or cfg.get("whisper_model") or "medium",
                progress_callback=_progress_printer(),
                vad=vad_mode, duration_sec=ref.duration,
                vad_long_sec=cfg.get("vad_long_sec", 1800))
            print("", file=sys.stderr)
            src = "whisper"
        except transcriber.WhisperNotInstalled:
            return "whisper_missing", src, 0, "", "未安装 faster-whisper"
        except Exception as e:
            return "failed", src, 0, "", str(e)[:100]
    # 5) 内容完整性复检（配置 vision 时固定路径）
    had_lines = bool(lines)
    cfgv = config.load_config().get("vision", {})
    if not opts.no_vision_check and cfgv.get("enabled"):
        try:
            sup = _vision_check(adapter, ref, cfgv, lines)
            if sup:
                lines = sorted(lines + sup, key=lambda x: x["start"])
                src = (src + "+vision补充") if had_lines else "vision"
        except Exception as e:
            print(f"  P{label} 复检失败（不影响原流程）：{str(e)[:100]}", file=sys.stderr)
    if not lines:
        return "failed", src, 0, "", "转写结果为空"
    # 6) 落盘 + 缓存
    out_dir = _item_out_dir(meta, ref, out_root, single, opts)
    info = artifacts.build_info(meta, ref, kind="timeline", source=src, page_count=opts.page_count)
    artifacts.write_item_artifacts(out_dir, info, TimelineContent(segments=lines))
    if not opts.no_cache:
        cache.set_cached(ref.platform, ref.item_id, ref.sub_id,
                         {"kind": "timeline", "items": lines})
    return "ok", src, len(lines), out_dir, ""


# ---------------- 批量 ----------------

def _load_resume_done(out_root: str, platform: str, item_id: str) -> set:
    """扫描 out_root 下同 item 的合集目录，返回 status=ok 的页码集合"""
    root = os.path.abspath(out_root)
    done = set()
    if not os.path.isdir(root):
        return done
    for d in os.listdir(root):
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            continue
        candidates = [os.path.join(p, x) for x in os.listdir(p)] if os.path.isdir(p) else []
        dirs = [d] if d.endswith("_合集") else [x for x in candidates if os.path.isdir(x) and x.endswith("_合集")]
        for vp in dirs:
            if not vp.endswith("_合集"):
                continue
            f = os.path.join(vp, "content_info.json")
            if not os.path.exists(f):
                continue
            try:
                info = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            if info.get("platform") != platform or info.get("item_id") != item_id:
                continue
            for r in info.get("pages") or []:
                if r.get("status") == "ok":
                    done.add(r["page"])
    return done


def run_batch(adapter, refs, root_meta, opts: RunOptions) -> dict:
    base = os.path.join(os.path.abspath(opts.out_root), _safe_name(root_meta.author),
                        f"{datetime.now():%Y-%m-%d}_{_safe_name(root_meta.title)}_合集")
    os.makedirs(base, exist_ok=True)
    results = []
    t_start = time.time()
    elapsed_list = []

    def _write_summary():
        summary = {
            "kind": "batch", "platform": refs[0].platform, "item_id": refs[0].item_id,
            "title": root_meta.title, "author": root_meta.author, "url": refs[0].url,
            "page_count": len(refs),
            "batch_fetched_at": datetime.now().isoformat(timespec="seconds"),
            "pages": results,
            "tool": "everything-learn v0.1",
        }
        with open(os.path.join(base, "content_info.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        return summary

    for i, ref in enumerate(refs, 1):
        label = str(ref.sub_id or "1")
        print(f"[{i}/{len(refs)}] 处理 P{label}（{ref.title[:20]}）……")
        t0 = time.time()
        status, src, n, _, err = process_item(adapter, ref, base, opts, single=False)
        elapsed = time.time() - t0
        if i < len(refs):
            time.sleep(opts.interval)  # 请求间隔限流
        elapsed_list.append(elapsed)
        avg = sum(elapsed_list) / len(elapsed_list)
        remain_min = avg * (len(refs) - i) / 60
        results.append({"page": ref.sub_id, "part": ref.title, "status": status,
                        "source": src, "line_count": n, "error": err})
        _write_summary()  # 增量写入，中断可续跑
        _LOGGER.info("batch %s P%s %s %s %d行", refs[0].item_id, label, status, src, n)
        print(f"  → {status}" + (f"（{src}，{n} 行）" if n else "")
              + f"｜耗时 {elapsed:.0f}s，预计剩余 {remain_min:.1f} 分钟")
    summary = _write_summary()
    with open(os.path.join(base, "report_template.md"), "w", encoding="utf-8") as f:
        f.write(report.render_batch_template(summary))
    _LOGGER.info("batch %s 完成 总耗时%.0fs", refs[0].item_id, time.time() - t_start)
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\n批量完成：{ok}/{len(refs)} P 成功")
    print(f"输出目录：{base}")
    return summary


# ---------------- 入口 ----------------

def _select_refs(refs, opts: RunOptions, default_page: int = 1):
    if opts.all_:
        return refs
    if opts.pages:
        wanted = pagespec.parse_pages(opts.pages)
        valid = {int(r.sub_id or 1) for r in refs}
        skipped = [w for w in wanted if w not in valid]
        if skipped:
            print(f"跳过超出范围的页码：{skipped}（共 {len(refs)} P）", file=sys.stderr)
        selected = [r for r in refs if int(r.sub_id or 1) in wanted]
        if not selected:
            raise PipelineError("所选页码均超出范围")
        return selected
    page = opts.page if opts.page is not None else default_page
    return [r for r in refs if int(r.sub_id or 1) == page]


def run_input(input_str: str, opts: RunOptions, adapter=None, client=None):
    """定位 → 展开 → 选择 → 单条/批量处理。
    - 单条：返回 (status, source, n, out_dir, error) 元组
    - 批量：返回汇总 dict（kind="batch"）
    SearchNeeded 向上抛（由 CLI 走平台搜索后重试）。"""
    from core import registry
    registry.load_providers()
    a = adapter or registry.match_url(input_str)
    if a is None:
        a = registry.default()
    if a is None:
        raise PipelineError(f"没有适配器能处理该输入：{input_str}")
    if client is not None and a.name == "bilibili":
        from providers.bilibili import BilibiliAdapter
        a = BilibiliAdapter(client=client)
    if getattr(a, "lang", None) is None:
        a.lang = opts.lang
    ref = a.resolve(input_str)            # SearchNeeded 向外抛
    refs = a.list_items(ref)
    if not refs:
        raise PipelineError("没有可处理的条目")
    opts.page_count = len(refs)
    default_page = int(ref.sub_id) if ref.sub_id and str(ref.sub_id).isdigit() else 1
    refs = _select_refs(refs, opts, default_page)
    if opts.resume and len(refs) > 1:
        done = _load_resume_done(opts.out_root, refs[0].platform, refs[0].item_id)
        if done:
            refs = [r for r in refs if int(r.sub_id or 1) not in done]
            print(f"续跑：跳过已完成 {len(done)} 个 P，剩余 {len(refs)} 个", file=sys.stderr)
            if not refs:
                print("续跑：全部 P 均已完成，无需处理", file=sys.stderr)
                return {"kind": "skip", "pages": []}
    root_meta = a.fetch_meta(refs[0])
    if len(refs) == 1 and not opts.pages and not opts.all_:
        return process_item(a, refs[0], os.path.abspath(opts.out_root), opts, single=True)
    return run_batch(a, refs, root_meta, opts)
