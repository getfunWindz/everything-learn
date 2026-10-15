"""通用流水线：缓存 → 内容获取（字幕/正文）→ 复检/转写 → 落盘 + 缓存。

平台差异全部由 SourceAdapter 承担；本模块不感知具体平台。
"""
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime

from core import pagespec
import report
from core import artifacts, cache, config, images, recheck
from core.models import DocumentContent, TimelineContent

_LOGGER = logging.getLogger("el")


class PipelineError(Exception):
    """流水线级错误（无法路由/无法定位等）"""


class UnsupportedUrlError(PipelineError):
    """名单外平台 URL（自进化入口）：需先尽调再搭建适配器，不得兜底成名称搜索"""


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
    recheck: str = None          # auto | off | 1 | 2 | 3（CLI 直传；等价 tier1/tier2 见 decide_tier）
    budget: float = None         # 本次运行金额上限（暂存）
    yes_recheck: bool = False    # 预授权完整档2


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


def _media_headers(adapter):
    """适配器可选钩子：媒体 CDN 所需请求头（如抖音拒绝裸 UA/bilibili Referer）；无则 None"""
    fn = getattr(adapter, "media_headers", None)
    if callable(fn):
        try:
            return fn()
        except Exception:
            return None
    return None


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
        tr.download_audio(url, vp, headers=_media_headers(adapter))  # 通用流式下载（任意二进制）
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
        tr.download_audio(url, vp, headers=_media_headers(adapter))
        result = vision.check_transcript(cfg_vision, vp, lines or [], ref.duration,
                                         max_frames=cfg_vision.get("max_frames", 30),
                                         max_rounds=cfg_vision.get("max_rounds", 2))
    sup = result.get("supplements") or []
    print(f"复检：{'发现 %d 条遗漏材料，已补充' % len(sup) if sup else '无遗漏'}", file=sys.stderr)
    return sup


# ---------------- 档位复检执行（T13/T14：预算闸门 + 窗口复检） ----------------

def _load_glossary_terms():
    """术语表（供知识点密度评分）：读取 references/glossary.json 的词条；失败返回 []"""
    try:
        import glossary
        return [k for k in (glossary.load_glossary() or {}) if k and not k.startswith("_")]
    except Exception:
        return []


def _budget_gate(est, cfg, opts, tier=None, duration_sec=None, info=None) -> str:
    """预算闸门 → 'A' 完整档2 / 'B' 帽内尽力 / 'C' 降档1

    tier/duration_sec：仅用于预算卡抬头（档位/时长）；info（可选 dict）：回填
    confirmed/reason —— 非 TTY 自动 B 属未获用户确认（confirmed=False,
    reason=non_tty_default）；交互选择/预授权为 confirmed=True。"""
    rc = cfg.get("recheck", {})
    symbol = est.get("currency", "")
    tier_txt = tier if tier is not None else 2
    dur_txt = f"{float(duration_sec) / 60:.1f} min" if duration_sec else "?"
    card = [f"\n⚠️ 复检预算预测（档{tier_txt}；免打扰阈值 "
            f"{rc.get('auto_approve_amount', 0.3)} {symbol}；时长 {dur_txt}）",
            f"   计划帧数：约 {est['frames']}（静帧去重前）",
            f"   预估金额：{est['low']} ~ {est['high']} {symbol}",
            f"   预计窗口：{est['windows']} 个"]
    if (est.get("whisper_secs") or 0) > 0:
        card.append(f"   Whisper 预计 {est['whisper_secs']:.0f}s")
    card.append("   选择：[A] 完整档2  [B] 帽内尽力（默认）  [C] 降档1")
    print("\n".join(card) + "\n", file=sys.stderr)
    if info is not None:  # 默认视为已确认；非 TTY/输入中断路径再降级为未确认
        info["confirmed"] = True
        info["reason"] = ""
    if getattr(opts, "yes_recheck", False):
        return "A"
    if getattr(opts, "recheck", None) == "1":
        return "C"
    if not sys.stdin.isatty():
        print("（非交互环境：按【帽内尽力】执行）", file=sys.stderr)
        if info is not None:
            info["confirmed"] = False
            info["reason"] = "non_tty_default"
        return "B"
    try:
        choice = input("请输入选择 [A/B/C]（默认 B）：").strip().upper() or "B"
    except (EOFError, KeyboardInterrupt):
        choice = "B"
        if info is not None:
            info["confirmed"] = False
            info["reason"] = "input_interrupted"
    return choice if choice in ("A", "B", "C") else "B"


def _correction_target(lines, ts, orig):
    """校正目标行（Q7）：优先 |start-ts|≤2s 邻域内首个含 orig 的行；
    否则全局首个含 orig 的行；均无 → None（不改，转 correction_unmatched）。"""
    if ts is not None:
        for l in lines:
            try:
                near = abs(float(l.get("start") or 0) - float(ts)) <= 2.0
            except (TypeError, ValueError):
                near = False
            if near and orig in (l.get("text") or ""):
                return l
    for l in lines:
        if orig in (l.get("text") or ""):
            return l
    return None


def _run_window_recheck(adapter, ref, lines, cfg, decision, opts):
    """执行档位复检：区域/全量 → 预算闸门 → 下载画面流 → 逐区逐窗抽帧复检
    → 合并补充/校正（+不确定/异常记录）→ 覆盖统计。

    契约：
    - 闸门 A 且判决档1 → 真正升级完整档2（全量区域，effective_tier=2）；
      C → 降档1 重选区域（effective_tier=1）；B/未过闸门 → 保持判决档。
    - coverage_pct 分母统一 recheck._effective_duration(执行前 lines, ref.duration)；
      只有 check_window 成功返回的窗口计入 covered，异常窗口进 window_errors。
    - 每窗抽帧/复检独立 try（异常记录并继续）；外层异常回滚 lines 快照，不留半应用。

    任何异常/画面流不可用 → {"skipped": ...}，不阻塞主流程。"""
    import transcriber
    import vision
    rc = cfg.get("recheck", {})
    conf = cfg.get("vision", {})
    fps = rc.get("frame_fps", 3)
    win = max(float(rc.get("window_sec", 30) or 30), 1.0)
    max_frames = rc.get("max_check_frames", 20000)
    dur = float(ref.duration or 0)

    def _plan(regions):
        p = recheck.plan_frames(regions, fps=fps, max_frames=max_frames)
        w = sum(math.ceil((float(r["end"]) - float(r["start"])) / win) for r in regions)
        return p, recheck.estimate_budget(p["frames"], w, conf.get("pricing", {}) or {})

    original_lines = [dict(l) for l in lines]  # Q3：执行前快照（外层异常时回滚）
    eff_dur = recheck._effective_duration(original_lines, ref.duration)  # Q2：统一分母
    try:
        tier = decision.get("tier")
        regions = ([{"start": 0.0, "end": dur, "reason": "全量档2", "score": 1.0}]
                   if tier == 2
                   else recheck.select_regions(lines, dur, cfg, disputed=None))
        plan, est = _plan(regions)
        choice = "A"
        effective_tier = tier
        budget_cap = getattr(opts, "budget", None)
        budget_reason = ""
        budget_info = {"confirmed": None, "reason": ""}  # Q5：未过闸门=None；过闸门由 _budget_gate 回填
        if recheck.needs_gate(est, cfg, dur):
            choice = _budget_gate(est, cfg, opts, tier=tier, duration_sec=dur, info=budget_info)
            if choice == "A" and tier == 1:  # Q1：闸门 A → 档1 真正升级完整档2
                regions = [{"start": 0.0, "end": dur, "reason": "用户选择完整档2", "score": 1.0}]
                plan, est = _plan(regions)
                effective_tier = 2
            elif choice == "C":  # 降档1：重选定向区域并重算预算（B 保持已裁剪 plan 不变）
                regions = recheck.select_regions(lines, dur, cfg, disputed=None)
                plan, est = _plan(regions)
                effective_tier = 1
        if budget_cap is not None and est["amount"] > float(budget_cap):
            choice = "B"                      # 超用户预算上限 → 直接帽内尽力（不询问）
            budget_reason = "budget_cap"
            pricing = conf.get("pricing", {})
            per_frame_price = (pricing.get("tokens_per_frame", 600) / 1e6) * pricing.get("input_per_1m", 0.3)
            if per_frame_price > 0:
                allowed = int(max(0.0, float(budget_cap)) / per_frame_price)
                cap = min(rc.get("max_check_frames", 20000), max(0, allowed))
                plan = recheck.plan_frames(regions, fps=fps, max_frames=cap)
        # Q8：预先展平 planned windows；准入按理论帧数（去重前 ceil(时长×fps)）裁剪，
        # 统计另记实际去重后帧数（used_frames / frames_used）。
        planned_windows = []
        for region in regions:
            cursor, region_end = float(region["start"]), float(region["end"])
            while cursor < region_end:
                seg_end = min(cursor + win, region_end)
                planned_windows.append((cursor, seg_end))
                cursor = seg_end
        url = adapter.fetch_media_url(ref, media="video", lowest=True)
        if not url:
            return {"skipped": "无可下载画面流"}
        print("  · 复检：下载画面流……", file=sys.stderr)
        tmpdir = tempfile.mkdtemp(prefix="el_rc_")
        covered = 0.0
        used_frames = 0
        reports = []
        window_errors = []  # Q2/Q3：抽帧/复检异常窗口（不计入 covered）
        uncovered = []      # Q8：因帧帽未处理的 planned windows
        added = []
        try:
            vp = os.path.join(tmpdir, "video.m4s")
            transcriber.download_audio(url, vp, headers=_media_headers(adapter))
            admitted_theoretical = 0
            for cursor, seg_end in planned_windows:
                window_frames = max(1, int(math.ceil((seg_end - cursor) * fps)))
                if admitted_theoretical + window_frames > plan["frames"]:
                    # 硬帽保护（spec §8.1）：A/B 均不得超 max_check_frames，超出部分标未复检
                    uncovered.append((cursor, seg_end))
                    reports.append({"range": [cursor, seg_end], "kind": "uncovered",
                                    "text": "超出复检帧预算（max_check_frames）"})
                    continue
                admitted_theoretical += window_frames
                try:
                    frames = vision.sample_window_frames(vp, cursor, seg_end, fps=fps,
                                                         dedup=rc.get("dedup", True))
                except Exception as ex:  # Q3：单窗抽帧异常 → 记录并继续，不整体中断
                    print(f"    窗口抽帧失败（跳过）：{str(ex)[:80]}", file=sys.stderr)
                    err = {"range": [cursor, seg_end], "kind": "error", "text": str(ex)[:100]}
                    reports.append(err)
                    window_errors.append(err)
                    continue
                if not frames:
                    continue
                used_frames += len(frames)
                ctx = " ".join(l["text"] for l in lines
                               if cursor - 1 <= float(l["start"]) < seg_end)
                try:
                    items = vision.check_window(conf, frames, transcript_text=ctx)
                except Exception as ex:  # Q3：单窗复检异常 → 记录并继续
                    print(f"    窗口复检失败（跳过）：{str(ex)[:80]}", file=sys.stderr)
                    err = {"range": [cursor, seg_end], "kind": "error", "text": str(ex)[:100]}
                    reports.append(err)
                    window_errors.append(err)
                    continue
                covered += seg_end - cursor  # Q2：仅成功返回的窗口计入覆盖
                for it in items or []:
                    kind = it.get("kind")
                    if kind == "supplement":
                        added.append({"start": float(it["ts"]), "end": float(it["ts"]) + 1,
                                      "text": it.get("text", ""), "source": "vision补充"})
                    elif kind == "correction":
                        orig = it.get("original") or ""
                        target = _correction_target(lines, it.get("ts"), orig) if orig else None
                        if target is not None:  # Q7：邻域优先 + 行内仅改首次出现
                            target["text"] = target["text"].replace(orig, it.get("text", ""), 1)
                            target["source"] = "vision校正"
                            target["corrected_from"] = orig
                        else:  # 找不到原句：转待人工关注，不误改
                            reports.append({"range": [cursor, seg_end],
                                            "kind": "correction_unmatched",
                                            "text": it.get("text", ""), "original": orig})
                    else:
                        reports.append({"range": [cursor, seg_end], "kind": "uncertain",
                                        "text": it.get("text", "")})
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        lines.extend(added)
        lines.sort(key=lambda x: float(x["start"]))
        return {"coverage_pct": round(covered / eff_dur * 100, 1) if eff_dur > 0 else 0.0,
                "windows": reports, "window_errors": window_errors,
                "budget": {"estimate": est, "user_choice": choice,
                           "confirmed": budget_info.get("confirmed"),
                           "reason": budget_reason or budget_info.get("reason", ""),
                           "cap": budget_cap},
                "effective_tier": effective_tier, "capped": bool(uncovered),
                "frames_used": used_frames}
    except Exception as e:
        lines[:] = original_lines  # Q3：外层异常 → 回滚，不留半应用修改
        return {"skipped": str(e)[:100]}


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
            content = (DocumentContent(blocks=payload.get("items") or [],
                                        images=payload.get("images") or [])
                       if payload.get("kind") == "document"
                       else TimelineContent(segments=payload.get("items") or []))
            assets = []
            if getattr(content, "images", None):
                try:
                    assets = images.download_images(content.images, out_dir, platform=ref.platform)
                except Exception as e:
                    print(f"  P{label} 图片下载失败（不影响正文）：{str(e)[:80]}", file=sys.stderr)
            info = artifacts.build_info(meta, ref, kind=payload.get("kind", "timeline"),
                                        source="cache", page_count=opts.page_count, assets=assets)
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
        assets = []
        if res.images:
            try:
                assets = images.download_images(res.images, out_dir, platform=ref.platform)
            except Exception as e:
                print(f"  P{label} 图片下载失败（不影响正文）：{str(e)[:80]}", file=sys.stderr)
        info = artifacts.build_info(meta, ref, kind="document", source=src,
                                    page_count=opts.page_count, assets=assets)
        artifacts.write_item_artifacts(out_dir, info, DocumentContent(blocks=res.blocks, images=res.images))
        if not opts.no_cache:
            cache.set_cached(ref.platform, ref.item_id, ref.sub_id,
                             {"kind": "document", "items": res.blocks, "images": res.images})
        return "ok", src, len(res.blocks), out_dir, ""
    # 3) timeline：来源策略（spec §5.2：auto→Whisper；human/official/unknown→同源门槛）
    provenance = getattr(res, "provenance", "unknown") or "unknown"
    language = (opts.lang or getattr(adapter, "language_hint", None)
                or None)  # None = faster-whisper 自动检测
    subtitle_candidate = list(res.segments) if res.segments else []
    lines = []
    ss = {}
    if subtitle_candidate and provenance in ("human", "official", "unknown"):
        print(f"  P{label} 字幕来源={provenance} → 同源校验……", file=sys.stderr)
        ss = _same_source_verify(adapter, ref, subtitle_candidate, config.load_config())
        accepted = bool(ss.get("passed"))
        if not accepted and ss.get("reason_code") == "no_audio" and not opts.no_vision_check:
            fb = _attribution_fallback(adapter, ref, subtitle_candidate, config.load_config())
            ss = dict(ss, attribution_fallback=fb)
            accepted = bool(fb.get("passed"))
        if accepted:
            lines = subtitle_candidate
            src = res.label or "字幕"
            print(f"  P{label} 字幕采信（同源校验通过）", file=sys.stderr)
        else:
            why = ss.get("reason") or ss.get("reason_code")
            fb = ss.get("attribution_fallback") or {}
            if fb:
                why = f"{why}；归属兜底未通过：{fb.get('reason') or fb.get('reason_code')}"
            print(f"  P{label} 未通过同源校验（{why}）→ 转 Whisper", file=sys.stderr)
    elif provenance == "auto":
        print(f"  P{label} 来源=AI/自动字幕 → Whisper 主选", file=sys.stderr)
    # 4) Whisper（无 lines 时）；--no-whisper：绝不转写，尽力给内容（T12 评审）
    if not lines:
        if opts.no_whisper:
            # 已判定不同源（checked=True 且 passed=False）→ 丢弃；其余（校验没跑成/auto）→ 采信
            verified_mismatch = ss.get("checked") is True and not ss.get("passed")
            if subtitle_candidate and not verified_mismatch:
                lines = subtitle_candidate
                src = ("字幕(AI)（--no-whisper，未经同源校验）" if provenance == "auto"
                       else "字幕（--no-whisper，未经同源校验）")
                print(f"  P{label} --no-whisper：采信未同源校验的字幕候选", file=sys.stderr)
            if not lines:
                return "no_subtitle", src, 0, "", "无内容且 --no-whisper 跳过转写"
        else:
            try:
                import transcriber
                media = adapter.fetch_media_url(ref, media="audio")
                if media:
                    print(f"  P{label} 启用 Whisper 转写……", file=sys.stderr)
                    cfg = config.load_config()
                    mode = cfg.get("whisper_vad", "auto")
                    vad_mode = opts.vad if opts.vad is not None else (None if mode == "auto" else bool(mode))
                    lines = transcriber.transcribe_url(
                        media,
                        model_size=opts.model or cfg.get("whisper_model") or "medium",
                        progress_callback=_progress_printer(),
                        vad=vad_mode, duration_sec=ref.duration,
                        vad_long_sec=cfg.get("vad_long_sec", 1800),
                        headers=_media_headers(adapter),
                        language=language)
                    print("", file=sys.stderr)
                    src = "whisper"
            except transcriber.WhisperNotInstalled:
                return "whisper_missing", src, 0, "", "未安装 faster-whisper"
            except Exception as e:
                print(f"  P{label} Whisper 失败：{str(e)[:80]}", file=sys.stderr)
            if not lines and subtitle_candidate:
                lines = subtitle_candidate
                src = "whisper失败→字幕（未经同源校验）"
                print(f"  P{label} 回退使用字幕候选", file=sys.stderr)
    if not lines:
        return "failed", src, 0, "", "转写结果为空"
    # 5) 档位评分 + 复检执行（T13/T14：预算闸门 → 窗口复检 → 补充/校正合并）
    cfg_full = config.load_config()
    has_video = getattr(adapter, "has_video", True)
    provenance_for_risk = "whisper" if (src or "").startswith("whisper") else provenance
    glossary_terms = _load_glossary_terms()
    signals = recheck.gather_signals(lines, ref.duration, provenance_for_risk, glossary_terms)
    decision = recheck.decide_tier(signals, cfg_full, provenance_for_risk,
                                   has_video=has_video, policy=opts.recheck)
    recheck_meta = {"tier": decision["tier"], "score": decision["score"],
                    "reasons": decision["reasons"],
                    "signals": {k: signals[k] for k in ("dur_min", "kp_density", "kp_spread",
                                                        "src_risk", "vis_ref", "gap_ratio", "max_gap")}}
    if decision["tier"] in (1, 2):
        if cfg_full.get("vision", {}).get("enabled") and not opts.no_vision_check:
            result = _run_window_recheck(adapter, ref, lines, cfg_full, decision, opts) or {}
            recheck_meta.update(result)
            # Q1：实际执行档（闸门 A 可从档1 升级档2）；result 未携带时回退判决档
            recheck_meta["effective_tier"] = result.get("effective_tier", decision["tier"])
        else:
            recheck_meta["skipped"] = ("vision 未配置：档位复检未执行"
                                       if not cfg_full.get("vision", {}).get("enabled")
                                       else "用户关闭 vision（--no-vision-check）")
    elif decision["tier"] == "n/a":  # Q4：纯音频来源与档3 的跳过文案要区分
        recheck_meta["skipped"] = "纯音频来源：多模态复检不适用"
    else:
        recheck_meta["skipped"] = "档位=不检（用户指定或白名单）"
    # 6) 落盘 + 缓存（recheck_report：复检元数据全量 + 同源校验 + 不确定项留痕）
    recheck_report = dict(recheck_meta)
    recheck_report["same_source"] = ss or {}
    recheck_report["uncertain"] = [w for w in (recheck_meta.get("windows") or [])
                                   if w.get("kind") == "uncertain"]
    out_dir = _item_out_dir(meta, ref, out_root, single, opts)
    info = artifacts.build_info(meta, ref, kind="timeline", source=src, page_count=opts.page_count,
                                recheck=recheck_meta, provenance=provenance, language=language)
    artifacts.write_item_artifacts(out_dir, info, TimelineContent(segments=lines))
    artifacts.write_recheck_report(out_dir, recheck_report)
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
        a = registry.sniff_url(input_str)
    if a is None and (input_str or "").startswith(("http://", "https://")):
        raise UnsupportedUrlError(f"未识别的平台链接：{input_str}\n{registry.SELF_EVOLUTION_HINT}")
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
    try:
        refs = a.list_items(ref)
    except PipelineError:
        raise
    except Exception as e:
        raise PipelineError(f"获取条目列表失败：{str(e)[:120]}")
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
    try:
        hook = getattr(a, "fetch_root_meta", None)
        root_meta = hook(ref, refs) if callable(hook) else a.fetch_meta(refs[0])
    except PipelineError:
        raise
    except Exception as e:
        raise PipelineError(f"获取元信息失败：{str(e)[:120]}")
    if len(refs) == 1 and not opts.pages and not opts.all_:
        return process_item(a, refs[0], os.path.abspath(opts.out_root), opts, single=True)
    return run_batch(a, refs, root_meta, opts)


# ---------------- 同源校验（音频抽查 + difflib 判定） ----------------

def _same_source_verify(adapter, ref, lines, cfg):
    """同源校验：音频抽查 → Whisper small → 相似度判定；
    任何异常/不可用 → checked=False（reason_code: no_audio|insufficient|error），
    降级不阻塞主流程"""
    import transcriber as tr
    tmpdir = tempfile.mkdtemp(prefix="el_ss_")
    try:
        url = adapter.fetch_media_url(ref, media="audio")
        if not url:
            return {"checked": False, "reason": "无可下载音频流", "reason_code": "no_audio"}
        spots = recheck.pick_spot_times(ref.duration)
        media_path = os.path.join(tmpdir, "audio.m4s")
        tr.download_audio(url, media_path, headers=_media_headers(adapter))
        clips = []
        for s, e in spots:
            samples, sr = tr.extract_audio_clip(media_path, s, e)
            if len(samples) == 0:
                continue
            text = tr.transcribe_samples(
                samples, sr, model_size=cfg.get("recheck", {}).get("same_source_model", "small"))
            clips.append((s, e, text))
        result = recheck.same_source_check(clips, lines, cfg)
        if not result["checked"] and not result.get("reason_code"):
            result["reason_code"] = "insufficient"
        return result
    except Exception as e:
        return {"checked": False, "reason": str(e)[:100], "reason_code": "error"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _attribution_fallback(adapter, ref, lines, cfg):
    """无音频流时的视觉归属兜底（spec §8.2 降级路径）；任何异常降级为 error，不外泄"""
    conf = cfg.get("vision", {})
    if not conf.get("enabled") or not conf.get("api_key"):
        return {"passed": False, "reason": "vision 未配置", "reason_code": "no_vision"}
    tmpdir = tempfile.mkdtemp(prefix="el_attr_")
    try:
        import vision
        url = adapter.fetch_media_url(ref, media="video", lowest=True)
        if not url:
            return {"passed": False, "reason": "无画面流", "reason_code": "no_video"}
        vp = os.path.join(tmpdir, "video.m4s")
        import transcriber as tr
        tr.download_audio(url, vp, headers=_media_headers(adapter))
        return vision.check_attribution_aligned(conf, vp, lines, ref.duration)
    except Exception as e:
        return {"passed": False, "reason": str(e)[:100], "reason_code": "error"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
