"""通用流水线：缓存 → 内容获取（字幕/正文）→ 复检/转写 → 落盘 + 缓存。

平台差异全部由 SourceAdapter 承担；本模块不感知具体平台。
"""
import hashlib
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
    budget: float = None         # 本次运行金额上限（运行级；spec §11.2）
    _budget_spent: float = 0.0   # 运行级已消费估算（跨条目随同一 opts 累计）
    yes_recheck: bool = False    # 预授权完整档2
    max_seconds: float = None    # 运行时长上限（复检窗口级；超出剩余窗口标未复检）
    force_whisper: bool = False  # 自动字幕平台也强制走 Whisper（默认按适配器策略）


def _safe_name(title: str, maxlen: int = 60) -> str:
    from core.fsutil import safe_title
    return safe_title(title, maxlen)


def _progress_printer():
    def _progress(done, total):
        pct = int(done / total * 100) if total else 0
        if pct % 10 == 0:
            print(f"    转写进度 {pct}%", file=sys.stderr, end="\r")
    return _progress


def _unique_path(base: str) -> str:
    if not os.path.exists(base):
        return base
    for i in range(2, 1000):
        cand = f"{base} {i}"
        if not os.path.exists(cand):
            return cand
    return base


def _item_out_dir(meta, ref, out_root, single, opts) -> str:
    """单条：<作者>/<标题>[ P#]（标题严格净化、无日期前缀；重名加空格序号）"""
    if single:
        title = _safe_name(meta.title)
        if opts.page_count > 1 and ref.sub_id:
            title += f" P{ref.sub_id}"
        return _unique_path(os.path.join(out_root, _safe_name(meta.author), title))
    return os.path.join(out_root, f"P{int(ref.sub_id or 1):02d}")


# ---------------- 多模态复检（平台无关：经 adapter.fetch_media_url 取画面流） ----------------

def _media_headers(adapter):
    """适配器可选钩子：媒体 CDN 所需请求头（如抖音拒绝裸 UA/bilibili Referer）；无则 None"""
    fn = getattr(adapter, "media_headers", None)
    if callable(fn):
        try:
            return fn()
        except Exception:
            return None
    return None


def _media_candidates(adapter, ref, media="audio", lowest=False) -> list:
    """媒体候选列表（健壮性）：适配器 `fetch_media_urls` 钩子优先（多 URL 回退），
    缺省/异常时回退到单 URL 的 `fetch_media_url`（向后兼容）。"""
    fn = getattr(adapter, "fetch_media_urls", None)
    if callable(fn):
        try:
            urls = [u for u in (fn(ref, media=media, lowest=lowest) or []) if u]
            if urls:
                return urls
        except Exception:
            pass
    u = adapter.fetch_media_url(ref, media=media, lowest=lowest)
    return [u] if u else []


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


def _recheck_ckpt_path(opts, ref):
    root = getattr(opts, "out_root", "") or "."
    d = os.path.join(root, ".recheck_ckpt")
    name = f"{ref.platform}_{_safe_name(str(ref.item_id), 40)}_{_safe_name(str(ref.sub_id or '1'), 12)}"
    return os.path.join(d, name + ".json")


def _load_recheck_ckpt(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("version") == 1:
            return data
    except (OSError, ValueError):
        pass
    return {}


def _save_recheck_ckpt(path, entries):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "windows": entries}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


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


def _run_window_recheck(adapter, ref, lines, cfg, decision, opts, disputed=None):
    """执行档位复检：区域/全量 → 预算闸门 → 下载画面流 → 逐区逐窗抽帧复检
    → 合并补充/校正（+不确定/异常记录）→ 覆盖统计。

    disputed：T14.5 双 ASR 分歧区间，档1 select_regions 作为重点复检候选（档2 全量不适用）。

    契约：
    - 闸门 A 且判决档1 → 真正升级完整档2（全量区域，effective_tier=2）；
      C → 降档1 重选区域（effective_tier=1）；B/未过闸门 → 保持判决档。
    - coverage_pct 分母统一 recheck._effective_duration(执行前 lines, ref.duration)；
      只有 check_window 成功返回的窗口计入 covered，异常窗口进 window_errors。
    - 每窗抽帧/复检独立 try（异常记录并继续）；外层异常回滚 lines 快照，不留半应用。

    任何异常/画面流不可用 → {"skipped": ...}，不阻塞主流程。"""
    import transcriber
    import vision
    if not vision.deps_available():                      # 低配环境：提前跳过（不逐窗报错）
        return {"skipped": "缺少画面复检依赖（Pillow/PyAV）：pip install pillow av"}
    rc = cfg.get("recheck", {})
    conf = cfg.get("vision", {})
    fps = rc.get("frame_fps", 3)
    win = max(float(rc.get("window_sec", 30) or 30), 1.0)
    max_frames = rc.get("max_check_frames", 20000)
    dur = recheck._effective_duration(lines, ref.duration)  # duration=0 适配器 → 回退末条字幕 end

    def _window_count(regions):
        return sum(math.ceil((float(r["end"]) - float(r["start"])) / win) for r in regions)

    def _plan(regions):
        p = recheck.plan_frames(regions, fps=fps, max_frames=max_frames)
        return p, recheck.estimate_budget(p["frames"], _window_count(regions),
                                          conf.get("pricing", {}) or {})

    original_lines = [dict(l) for l in lines]  # Q3：执行前快照（外层异常时回滚）
    eff_dur = dur  # Q2：统一分母（regions/plan/coverage 同源，含 duration=0 回退值）
    try:
        tier = decision.get("tier")
        regions = ([{"start": 0.0, "end": dur, "reason": "全量档2", "score": 1.0}]
                   if tier == 2
                   else recheck.select_regions(lines, dur, cfg, disputed=disputed))
        plan, est = _plan(regions)
        choice = "A"
        effective_tier = tier
        est_used = est             # Q3：执行实际估算（cap 裁剪后回写 budget.estimate）
        est_pre_cap = None         # Q3：金额裁剪前原估算（裁剪时留痕）
        runtime_cap = max_frames   # M13：运行准入帽——非预算路径 = 帧硬帽
        budget_cap = getattr(opts, "budget", None)
        budget_remaining = None
        if budget_cap is not None:  # spec §11.2：运行级上限 = 上限 - 已累计消费
            budget_remaining = max(0.0, float(budget_cap)
                                   - float(getattr(opts, "_budget_spent", 0.0)))
        budget_reason = ""
        budget_info = {"confirmed": None, "reason": ""}  # 未过闸门=None；闸门/金额裁剪回填
        if budget_remaining is not None and (budget_remaining <= 0 or est["amount"] > budget_remaining):
            # Q2：cap 检查前移——超运行剩余预算直接帽内尽力，不弹假闸门
            choice = "B"
            budget_reason = "budget_cap"
            budget_info["confirmed"] = False  # 自动约束，未获用户确认
            budget_info["reason"] = "budget_cap"
            est_pre_cap = est
            pricing = conf.get("pricing", {}) or {}
            per_frame_price = ((pricing.get("tokens_per_frame", 600) / 1e6)
                               * pricing.get("input_per_1m", 0.3))
            if per_frame_price > 0:
                allowed = int(budget_remaining / per_frame_price)
                cap = min(rc.get("max_check_frames", 20000), max(0, allowed))
            else:  # Q1：单价 <=0 无法换算帧数 → 帧预算置 0（全部窗口 uncovered）
                cap = 0
            runtime_cap = cap      # M13：预算路径运行帽 = 金额裁剪后的 cap
            plan = recheck.plan_frames(regions, fps=fps, max_frames=cap)
            est_used = recheck.estimate_budget(plan["frames"], _window_count(regions),
                                               pricing)  # Q3：按裁剪后 plan 回算
        elif recheck.needs_gate(est, cfg, dur):
            choice = _budget_gate(est, cfg, opts, tier=tier, duration_sec=dur, info=budget_info)
            if choice == "A" and tier == 1:  # Q1：闸门 A → 档1 真正升级完整档2
                regions = [{"start": 0.0, "end": dur, "reason": "用户选择完整档2", "score": 1.0}]
                plan, est = _plan(regions)
                est_used = est
                effective_tier = 2
            elif choice == "C":  # 降档1：重选定向区域并重算预算（B 保持已裁剪 plan 不变）
                regions = recheck.select_regions(lines, dur, cfg, disputed=disputed)
                plan, est = _plan(regions)
                est_used = est
                effective_tier = 1
        # Q8：预先展平 planned windows；准入按理论帧数（去重前 ceil(时长×fps)）裁剪，
        # 统计另记实际去重后帧数（used_frames / frames_used）。
        # M13：准入上限用 runtime_cap（预算路径=金额 cap，否则=帧硬帽）；
        # plan["frames"] 含 int 截断，仅作估算/展示，不作准入上限。
        planned_windows = []
        for region in regions:
            cursor, region_end = float(region["start"]), float(region["end"])
            while cursor < region_end:
                seg_end = min(cursor + win, region_end)
                planned_windows.append((cursor, seg_end))
                cursor = seg_end
        media_urls = _media_candidates(adapter, ref, media="video", lowest=True)
        if not media_urls:
            return {"skipped": "无可下载画面流"}
        print("  · 复检：下载画面流……", file=sys.stderr)
        tmpdir = tempfile.mkdtemp(prefix="el_rc_")
        covered = 0.0
        used_frames = 0
        checked = 0            # 成功完成 vision 检查的窗口数（区别于 uncovered/error）
        total_windows = len(planned_windows)
        reports = []
        window_errors = []  # Q2/Q3：抽帧/复检异常窗口（不计入 covered）
        uncovered = []      # Q8：因帧帽未处理的 planned windows
        added = []
        try:
            vp = os.path.join(tmpdir, "video.m4s")
            transcriber.download_audio(media_urls, vp, headers=_media_headers(adapter))
            admitted_theoretical = 0
            # Q5：未覆盖原因文案——金额裁剪 vs 帧硬帽
            uncovered_text = ("超出复检金额预算（--budget）"
                              if budget_reason == "budget_cap"
                              else "超出复检帧预算（max_check_frames）")
            print(f"  · 复检计划：{total_windows} 窗 / 预估 {est_used['amount']:.3f} "
                  f"{est_used.get('currency', 'USD')}（上界，按帧去重后通常更低） / 帧帽 {runtime_cap}",
                  file=sys.stderr)
            ckpt_path = _recheck_ckpt_path(opts, ref)
            prior = _load_recheck_ckpt(ckpt_path) if getattr(opts, "resume", False) else {}
            ckpt_by_key = {tuple(e.get("range") or []): e
                           for e in (prior.get("windows") or [])}
            ckpt_entries = []

            stats = {"supplements": 0, "corrections": 0,
                     "low_confidence": 0, "unmatched": 0, "uncertain": 0}

            def _apply_items(items, cursor, seg_end):
                for it in items or []:
                    kind = it.get("kind")
                    if kind == "supplement":
                        added.append({"start": float(it["ts"]), "end": float(it["ts"]) + 1,
                                      "text": it.get("text", ""), "source": "vision补充"})
                        stats["supplements"] += 1
                    elif kind == "correction":
                        if it.get("confidence") == "低":
                            # 置信度分级（B11）：低置信只留痕，不改正文
                            stats["low_confidence"] += 1
                            reports.append({"range": [cursor, seg_end],
                                            "kind": "low_confidence",
                                            "text": it.get("text", ""),
                                            "original": it.get("original", ""),
                                            "ts": it.get("ts")})
                            continue
                        orig = it.get("original") or ""
                        target = _correction_target(lines, it.get("ts"), orig) if orig else None
                        if target is not None:
                            target["text"] = target["text"].replace(orig, it.get("text", ""), 1)
                            target["source"] = "vision校正"
                            target["corrected_from"] = orig
                            target["corrected_to"] = it.get("text", "")
                            stats["corrections"] += 1
                        else:
                            stats["unmatched"] += 1
                            reports.append({"range": [cursor, seg_end],
                                            "kind": "correction_unmatched",
                                            "text": it.get("text", ""), "original": orig})
                    else:
                        stats["uncertain"] += 1
                        reports.append({"range": [cursor, seg_end], "kind": "uncertain",
                                        "text": it.get("text", "")})

            started = time.monotonic()
            max_seconds = getattr(opts, "max_seconds", None)
            for wi, (cursor, seg_end) in enumerate(planned_windows, 1):
                elapsed = time.monotonic() - started
                if max_seconds is not None and elapsed >= max_seconds:
                    for (c, e) in planned_windows[wi - 1:]:
                        uncovered.append((c, e))
                        reports.append({"range": [c, e], "kind": "uncovered",
                                        "text": "超出运行时长上限（--max-seconds）"})
                    print(f"  · 已达 --max-seconds={max_seconds:g}s，剩余 "
                          f"{total_windows - wi + 1} 窗标未复检", file=sys.stderr)
                    break
                eta = (elapsed / (wi - 1)) * (total_windows - wi + 1) if wi > 1 else None
                print(f"  · 复检窗口 {wi}/{total_windows} [{cursor:.0f}-{seg_end:.0f}s]"
                      + (f"（已用 {elapsed:.0f}s，预计剩余 {eta:.0f}s）" if eta else ""),
                      file=sys.stderr)
                ctx = " ".join(l["text"] for l in lines
                               if cursor - 1 <= float(l["start"]) < seg_end)
                ctx_hash = hashlib.sha1(ctx.encode("utf-8")).hexdigest()
                ck = (round(cursor, 1), round(seg_end, 1))
                entry = ckpt_by_key.get(ck)
                if entry and entry.get("hash") == ctx_hash:
                    covered += seg_end - cursor
                    checked += 1
                    _apply_items(entry.get("items"), cursor, seg_end)
                    ckpt_entries.append(entry)
                    print("    复用断点结果（未调用 vision）", file=sys.stderr)
                    continue
                window_frames = max(1, int(math.ceil((seg_end - cursor) * fps)))
                if admitted_theoretical + window_frames > runtime_cap:
                    # 运行帽保护（spec §8.1）：A/B 均不得超 runtime_cap，超出部分标未复检
                    uncovered.append((cursor, seg_end))
                    reports.append({"range": [cursor, seg_end], "kind": "uncovered",
                                    "text": uncovered_text})
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
                try:
                    items = vision.check_window(conf, frames, transcript_text=ctx)
                except Exception as ex:  # Q3：单窗复检异常 → 记录并继续
                    print(f"    窗口复检失败（跳过）：{str(ex)[:80]}", file=sys.stderr)
                    err = {"range": [cursor, seg_end], "kind": "error", "text": str(ex)[:100]}
                    reports.append(err)
                    window_errors.append(err)
                    continue
                covered += seg_end - cursor  # Q2：仅成功返回的窗口计入覆盖
                checked += 1
                _apply_items(items, cursor, seg_end)
                ckpt_entries.append({"range": [cursor, seg_end], "hash": ctx_hash,
                                     "items": items or []})
                _save_recheck_ckpt(ckpt_path, ckpt_entries)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        if not uncovered and not window_errors:
            try:
                os.remove(ckpt_path)
            except OSError:
                pass
        # actual：按「去重后帧数」重算的实际费用参考（estimate 是闸门上界，常高估 5~10 倍）；
        # spent_total 仍按 estimate 保守累计（与 cap 的“计划帧”口径一致，避免超支）。
        actual_est = recheck.estimate_budget(used_frames, checked, conf.get("pricing", {}) or {})
        if budget_remaining is not None:
            opts._budget_spent = (getattr(opts, "_budget_spent", 0.0)
                                  + min(est_used["amount"], budget_remaining))
        lines.extend(added)
        lines.sort(key=lambda x: float(x["start"]))
        budget_meta = {"estimate": est_used, "actual": actual_est,
                       "frames_planned": est_used.get("frames"), "frames_used": used_frames,
                       "user_choice": choice,
                       "confirmed": budget_info.get("confirmed"),
                       "reason": budget_reason or budget_info.get("reason", ""),
                       "cap": (max(0.0, float(budget_cap)) if budget_cap is not None else None),
                       "spent_total": round(getattr(opts, "_budget_spent", 0.0), 6)}
        if est_pre_cap is not None:  # Q3：金额裁剪前原估算（信息保留）
            budget_meta["estimate_pre_cap"] = est_pre_cap
        return {"coverage_pct": round(covered / eff_dur * 100, 1) if eff_dur > 0 else 0.0,
                "windows": reports, "window_errors": window_errors,
                "uncovered": [[float(s), float(e)] for (s, e) in uncovered],  # T16：骨架未覆盖区间
                "budget": budget_meta,
                "effective_tier": effective_tier, "capped": bool(uncovered),
                "checked_windows": checked, "frames_used": used_frames,
                "summary": stats}
    except Exception as e:
        lines[:] = original_lines  # Q3：外层异常 → 回滚，不留半应用修改
        return {"skipped": str(e)[:100]}


def _with_auth_retry(platform: str, fn, adapter=None):
    """认证类错误 → 自动恢复登录态后重试一次；其他异常原样抛出"""
    try:
        return fn()
    except Exception as e:
        from core import auth
        if auth.looks_like_auth_error(e) and auth.ensure(platform):
            reload_auth = getattr(adapter, "reload_auth", None)
            if callable(reload_auth):
                try:
                    reload_auth()
                except Exception:
                    pass
            return fn()
        raise


def _kb_index(out_dir: str) -> None:
    """成功后按配置索引到内建知识库；任何异常不影响主流程"""
    try:
        from core import config as _cfg
        if not (_cfg.load_config().get("kb") or {}).get("enabled"):
            return
        import kb
        kb.index_dir(out_dir)
    except Exception:
        pass


# ---------------- 单 item 处理 ----------------


def _document_lite_recheck(adapter, res, opts):
    """B2：视频帖（document）口播转写 → lite 版档位评估（不执行窗口复检）

    与 timeline 的 T13/T14 使用同一套确定性评分（gather_signals/decide_tier）；
    产物落 recheck_report.json 并在 content_info.recheck 留痕。窗口级抽帧复检
    暂不在文档形态实现（无时间轴合并语义），skipped 字段如实标注（R13）。
    """
    video_lines = list(getattr(res, "video_lines", None) or [])
    video_dur = float(getattr(res, "video_duration", 0) or 0)
    if not video_lines or video_dur <= 0 or not getattr(adapter, "has_video", True):
        return None
    cfg_full = config.load_config()
    signals = recheck.gather_signals(video_lines, video_dur, "whisper", _load_glossary_terms())
    decision = recheck.decide_tier(signals, cfg_full, "whisper", has_video=True,
                                   policy=getattr(opts, "recheck", None))
    return {"tier": decision["tier"], "score": decision["score"],
            "reasons": decision["reasons"],
            "signals": {k: signals[k] for k in ("dur_min", "kp_density", "kp_spread",
                                                "src_risk", "vis_ref", "gap_ratio", "max_gap")},
            "lite": True, "skipped": "文档形态：仅档位评估（未执行窗口复检）",
            "same_source": {}, "uncertain": []}


def process_item(adapter, ref, out_root, opts: RunOptions, single: bool = False):
    """处理单个 item → (status, source, line_count, out_dir, error)"""
    label = str(ref.sub_id or "1")
    model_used = None          # 本次实际使用的 whisper 档（质量检测用）
    asr_stats = None           # 转写置信度（质量检测用）
    quality_meta = None        # 产物字段：转写质量原始指标
    quality_hint = None        # 产物字段：低配质量强制推荐（弱质量/缺依赖）
    # 1) 缓存命中直接复用
    if not opts.no_cache:
        cached = cache.get_cached(ref.platform, ref.item_id, ref.sub_id)
        if cached:
            payload = cached if isinstance(cached, dict) else {"kind": "timeline", "items": cached}
            try:
                meta = _with_auth_retry(ref.platform, lambda: adapter.fetch_meta(ref), adapter)
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
                                        source="cache", page_count=opts.page_count, assets=assets,
                                        recheck=payload.get("recheck"),
                                        provenance=payload.get("provenance"),
                                        language=payload.get("language"),
                                        glossary=payload.get("glossary"))
            artifacts.write_item_artifacts(out_dir, info, content)
            if payload.get("recheck_report"):
                artifacts.write_recheck_report(out_dir, payload["recheck_report"])
            return "ok", "cache", len(payload.get("items") or []), out_dir, ""
    # 2) 取内容（字幕/正文）
    try:
        res = _with_auth_retry(ref.platform, lambda: adapter.fetch_content(ref), adapter)
        meta = _with_auth_retry(ref.platform, lambda: adapter.fetch_meta(ref), adapter)
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
        recheck_meta = _document_lite_recheck(adapter, res, opts)
        text_len = sum(len((b or {}).get("text") or "") for b in res.blocks)
        from core import quality as _quality
        doc_hint = _quality.document_quality_hint(
            text_len, images=len(res.images or []),
            video_lines=len(getattr(res, "video_lines", None) or []),
            adapter_hint=getattr(res, "quality_hint", ""),
            text=" ".join((b or {}).get("text") or "" for b in res.blocks)) or None
        if doc_hint:
            print(f"  ⚠️ {doc_hint}", file=sys.stderr)
        info = artifacts.build_info(meta, ref, kind="document", source=src,
                                    page_count=opts.page_count, assets=assets,
                                    recheck=recheck_meta,
                                    provenance=("whisper" if recheck_meta else None),
                                    language=(res.language or None),
                                    quality_hint=doc_hint)
        artifacts.write_item_artifacts(out_dir, info, DocumentContent(blocks=res.blocks, images=res.images))
        if recheck_meta:
            artifacts.write_recheck_report(out_dir, recheck_meta)
        if not opts.no_cache:
            cache.set_cached(ref.platform, ref.item_id, ref.sub_id,
                             {"kind": "document", "items": res.blocks, "images": res.images,
                              "provenance": ("whisper" if recheck_meta else None),
                              "language": res.language or None,
                              "recheck": recheck_meta, "recheck_report": recheck_meta})
        return "ok", src, len(res.blocks), out_dir, ""
    # 3) timeline：来源策略（spec §5.2：auto→Whisper；human/official/unknown→同源门槛）
    provenance = getattr(res, "provenance", "unknown") or "unknown"
    language = (opts.lang or getattr(adapter, "language_hint", None)
                or None)  # None = faster-whisper 自动检测
    language_used = None  # D3：产物 language —— 实际采信路径使用的语言（Whisper 路径回填）
    subtitle_candidate = list(res.segments) if res.segments else []
    secondary = list(subtitle_candidate) if provenance == "auto" else []  # T14.5：auto 字幕作第二 ASR 意见
    lines = []
    ss = {}
    if subtitle_candidate and provenance in ("human", "official", "unknown"):
        print(f"  P{label} 字幕来源={provenance} → 同源校验……", file=sys.stderr)
        ss = _same_source_verify(adapter, ref, subtitle_candidate, config.load_config(),
                                 subtitle_language=res.language or "")
        accepted = bool(ss.get("passed"))
        lang_mismatch = ss.get("reason_code") == "lang_mismatch"
        if (not accepted and ss.get("reason_code") in ("no_audio", "lang_mismatch")
                and not opts.no_vision_check):
            fb = _attribution_fallback(adapter, ref, subtitle_candidate, config.load_config())
            ss = dict(ss, attribution_fallback=fb)
            accepted = bool(fb.get("passed"))
        # 翻译轨保护（M13 修复）：lang_mismatch 且视觉归属未给出明确否决（未执行/不可用/
        # 无裁决）→ 采信人工翻译，不因语言不同而丢弃（翻译轨是合法内容，丢弃损失翻译质量）
        fb_meta = ss.get("attribution_fallback") or {}
        translation_kept = lang_mismatch and not accepted and not fb_meta.get("checked")
        if accepted or translation_kept:
            lines = subtitle_candidate
            src = res.label or "字幕"
            if translation_kept:
                src = f"{src}（语言不匹配，未同源校验）"
            # D3：字幕采信 → 产物语言取字幕声明 > 适配器主语言 > --lang
            language_used = (res.language or getattr(adapter, "language_hint", None)
                             or opts.lang or None)
            why_ok = "同源校验通过" if accepted else "语言不匹配，视觉归属未否决"
            print(f"  P{label} 字幕采信（{why_ok}）", file=sys.stderr)
        else:
            why = ss.get("reason") or ss.get("reason_code")
            fb = ss.get("attribution_fallback") or {}
            if fb:
                why = f"{why}；归属兜底未通过：{fb.get('reason') or fb.get('reason_code')}"
            print(f"  P{label} 未通过同源校验（{why}）→ 转 Whisper", file=sys.stderr)
    elif provenance == "auto":
        if getattr(adapter, "accept_auto_subtitle", False) and not opts.force_whisper:
            # 平台策略放宽（YouTube）：自动字幕直接采信，明确标注未做 Whisper 交叉校验
            lines = subtitle_candidate
            src = "字幕(自动)（未交叉校验）"
            language_used = (res.language or getattr(adapter, "language_hint", None)
                             or opts.lang or None)
            print(f"  P{label} 来源=AI/自动字幕 → 平台策略：直接采信（未交叉校验）",
                  file=sys.stderr)
        else:
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
                # D3：--no-whisper 采信字幕 → 语言同字幕采信路径
                language_used = (res.language or getattr(adapter, "language_hint", None)
                                 or opts.lang or None)
                print(f"  P{label} --no-whisper：采信未同源校验的字幕候选", file=sys.stderr)
            if not lines:
                return "no_subtitle", src, 0, "", "无内容且 --no-whisper 跳过转写"
        else:
            try:
                import transcriber
                if not transcriber.whisper_available():      # 低配前置：避免白下载媒体
                    raise transcriber.WhisperNotInstalled("未安装 faster-whisper")
                media_urls = _media_candidates(adapter, ref, media="audio")
                if media_urls:
                    print(f"  P{label} 启用 Whisper 转写……", file=sys.stderr)
                    cfg = config.load_config()
                    mode = cfg.get("whisper_vad", "auto")
                    vad_mode = opts.vad if opts.vad is not None else (None if mode == "auto" else bool(mode))
                    model_used = opts.model or cfg.get("whisper_model") or "medium"
                    asr_stats = {}
                    lines = transcriber.transcribe_url(
                        media_urls,
                        model_size=model_used,
                        progress_callback=_progress_printer(),
                        vad=vad_mode, duration_sec=ref.duration,
                        vad_long_sec=cfg.get("vad_long_sec", 1800),
                        headers=_media_headers(adapter),
                        language=language, stats=asr_stats,
                        max_seconds=getattr(adapter, "media_budget_sec", None))
                    print("", file=sys.stderr)
                    src = "whisper"
                    # D3：Whisper 实际语言；自动检测时回填 asr_stats.language
                    language_used = language or asr_stats.get("language")
            except transcriber.WhisperNotInstalled:
                if subtitle_candidate:
                    # 低配降级（2026-10-17）：无 faster-whisper → 采信字幕候选 + 标注 + 强制推荐
                    prov_tag = {"auto": "AI 字幕", "human": "人工字幕",
                                "official": "官方字幕"}.get(provenance, "字幕")
                    lines = subtitle_candidate
                    src = f"{prov_tag}（低配：缺 faster-whisper，未经同源校验）"
                    language_used = (res.language or getattr(adapter, "language_hint", None)
                                     or opts.lang or None)
                    from core import quality as _quality
                    quality_hint = {
                        "level": "lowdep",
                        "reasons": ["缺少 faster-whisper：同源校验与 Whisper 转写不可用，"
                                    "已降级采信字幕（可信度低于校验通过）"],
                        "missing_deps": _quality.missing_dependencies(),
                        "advice": "安装 faster-whisper（并建议升级 medium）以获得同源校验与最佳转写质量。"}
                    print(f"  P{label} 未安装 faster-whisper → 低配降级：采信字幕候选（已标注）",
                          file=sys.stderr)
                else:
                    return "whisper_missing", src, 0, "", "未安装 faster-whisper"
            except Exception as e:
                print(f"  P{label} Whisper 失败：{str(e)[:80]}", file=sys.stderr)
            if not lines and subtitle_candidate:
                lines = subtitle_candidate
                src = "whisper失败→字幕（未经同源校验）"
                # D3：回退字幕 → 语言同字幕采信路径
                language_used = (res.language or getattr(adapter, "language_hint", None)
                                 or opts.lang or None)
                print(f"  P{label} 回退使用字幕候选", file=sys.stderr)
    if not lines:
        return "failed", src, 0, "", "转写结果为空"
    # 5) 档位评分 + 复检执行（T13/T14：预算闸门 → 窗口复检 → 补充/校正合并）
    cfg_full = config.load_config()
    has_video = getattr(adapter, "has_video", True)
    provenance_for_risk = "whisper" if (src or "").startswith("whisper") else provenance
    glossary_terms = _load_glossary_terms()
    signals = recheck.gather_signals(lines, ref.duration, provenance_for_risk, glossary_terms)
    rc_cfg = cfg_full.get("recheck") or {}
    vision_cfg = cfg_full.get("vision") or {}
    policy = opts.recheck
    if (policy is None and rc_cfg.get("tier_policy", "auto") == "auto"
            and vision_cfg.get("subtitle_check", "auto") in ("off", False)):
        policy = "off"  # D2：旧键 vision.subtitle_check=off 平滑降级到新策略（不复检）
    decision = recheck.decide_tier(signals, cfg_full, provenance_for_risk,
                                   has_video=has_video, policy=policy)
    recheck_meta = {"tier": decision["tier"], "score": decision["score"],
                    "reasons": decision["reasons"],
                    "signals": {k: signals[k] for k in ("dur_min", "kp_density", "kp_spread",
                                                        "src_risk", "vis_ref", "gap_ratio", "max_gap")}}
    # T14.5：Whisper 成功后，auto 字幕作第二 ASR 意见按时间窗交叉比对；分歧区间进入
    # 档1 重点复检区域（由画面仲裁）。未跑 Whisper（采信字幕）不比对、保持现状不落盘。
    # cross_window_sec / cross_threshold 在 config.recheck 有默认（15/0.65）；
    # rc.get 兼容直调 core 或旧配置缺键。
    disputed = []
    if src == "whisper" and secondary and cfg_full.get("recheck", {}).get("cross_check", True):
        rc = cfg_full.get("recheck", {})
        cc = recheck.cross_check(lines, secondary, rc.get("cross_window_sec", 15),
                                 rc.get("cross_threshold", 0.65))
        recheck_meta["cross_check"] = cc
        disputed = cc.get("disputed") or []
    window_result = {}
    if decision["tier"] in (1, 2):
        if cfg_full.get("vision", {}).get("enabled") and not opts.no_vision_check:
            window_result = _run_window_recheck(adapter, ref, lines, cfg_full, decision, opts,
                                                disputed=disputed) or {}
            recheck_meta.update(window_result)
            # Q1：实际执行档（闸门 A 可从档1 升级档2）；result 未携带时回退判决档
            recheck_meta["effective_tier"] = window_result.get("effective_tier", decision["tier"])
        else:
            recheck_meta["skipped"] = ("vision 未配置：档位复检未执行"
                                       if not cfg_full.get("vision", {}).get("enabled")
                                       else "用户关闭 vision（--no-vision-check）")
    elif decision["tier"] == "n/a":  # Q4：纯音频来源与档3 的跳过文案要区分
        recheck_meta["skipped"] = "纯音频来源：多模态复检不适用"
    else:
        recheck_meta["skipped"] = "档位=不检（用户指定或白名单）"
    # 6) 落盘 + 缓存（recheck_report：复检元数据全量 + 同源校验 + 不确定项留痕）
    # T16 配套：骨架「复检说明」读取 recheck.same_source/uncovered，须在 build_info 前补齐
    # 顶层键（未执行时显式落 {} / []，避免真实产物显示「无 / 未执行」失真）；
    # recheck_report 保持既有结构：未覆盖区间已逐条在 windows 明细留痕。
    recheck_meta.setdefault("same_source", ss or {})
    recheck_meta.setdefault("uncovered", (window_result or {}).get("uncovered", []))
    recheck_report = dict(recheck_meta)
    recheck_report.pop("uncovered", None)
    recheck_report["same_source"] = ss or {}
    recheck_report["uncertain"] = [w for w in (recheck_meta.get("windows") or [])
                                   if w.get("kind") in ("uncertain", "low_confidence")]
    # 6.5) 低配质量检测（2026-10-17）：低档 + 质量弱 → 强制推荐（补依赖 + 升 medium）
    if src == "whisper" and asr_stats:
        from core import quality as _quality
        _assessment = _quality.assess(asr_stats, model_used or "", ref.duration, lines)
        if _assessment is not None:
            quality_meta = {"model": model_used, **_assessment, **asr_stats}
            if 0 <= _quality.tier_index(model_used or "") < _quality.tier_index("medium"):
                quality_meta["tier_note"] = ("当前转写档位低于推荐的 medium（实机验证 medium 效果最佳）；"
                                             "建议升级以获得最佳效果。")
            if _quality.needs_upgrade(_assessment, model_used or ""):
                _deps = _quality.missing_dependencies()
                quality_hint = {"level": "weak", "model": model_used,
                                "reasons": _assessment["reasons"],
                                "missing_deps": _deps,
                                "advice": "当前档位低于 medium（实机验证 medium 效果最佳）：建议 config.whisper_model=medium "
                                          "或 --model medium；并补全依赖以获得完整体验。"}
                print("", file=sys.stderr)
                print("  ⚠️ 转写质量提示（低配模式）：" + "；".join(_assessment["reasons"]),
                      file=sys.stderr)
                print(f"     · 当前档位：{model_used} → 强烈建议 medium（--model medium）", file=sys.stderr)
                if _deps:
                    print("     · 建议补全依赖：" + "；".join(d["name"] for d in _deps),
                          file=sys.stderr)
            elif 0 <= _quality.tier_index(model_used or "") < _quality.tier_index("medium"):
                print(f"  ℹ️ 转写档位 {model_used}（fast）；如需最佳效果可升级 medium（--model medium）",
                      file=sys.stderr)
    # 6.6) 术语库筛验 + 自动反哺（2026-10-17；每次转写必经）
    glossary_meta = None
    if lines:
        try:
            import glossary as _glossary
            _pairs = [(l.get("corrected_from"), l.get("corrected_to"))
                      for l in lines
                      if getattr(l, "get", None) and l.get("corrected_from")]
            _absorbed = _glossary.absorb_corrections(_pairs) if _pairs else {}
            lines, _counts, _uncertain = _glossary.clean_lines(lines)
            if _counts or _uncertain or _absorbed:
                _aliases = _glossary.get_aliases()
                glossary_meta = {
                    "corrected": {raw: {"correct": (_aliases.get(raw) or {}).get("correct"),
                                        "count": n} for raw, n in _counts.items()},
                    "uncertain": _uncertain, "absorbed": _absorbed}
                _msg = f"  P{label} 术语筛验：校正 {sum(_counts.values())} 处（{len(_counts)} 类）"
                if _absorbed:
                    _msg += f"；反哺别名 {len(_absorbed)} 条"
                if _uncertain:
                    _msg += f"；存疑 {len(_uncertain)} 个"
                print(_msg, file=sys.stderr)
        except Exception as e:
            print(f"  P{label} 术语筛验跳过：{str(e)[:60]}", file=sys.stderr)
    out_dir = _item_out_dir(meta, ref, out_root, single, opts)
    info = artifacts.build_info(meta, ref, kind="timeline", source=src, page_count=opts.page_count,
                                recheck=recheck_meta, provenance=provenance,
                                language=language_used or None,
                                quality=quality_meta, quality_hint=quality_hint,
                                glossary=glossary_meta)
    artifacts.write_item_artifacts(out_dir, info, TimelineContent(segments=lines))
    artifacts.write_recheck_report(out_dir, recheck_report)
    if not opts.no_cache:
        cache.set_cached(ref.platform, ref.item_id, ref.sub_id,
                         {"kind": "timeline", "items": lines, "provenance": provenance,
                          "language": language_used, "recheck": recheck_meta,
                          "recheck_report": recheck_report, "glossary": glossary_meta})
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
        def _is_coll(name):
            return name.endswith(" 合集") or name.endswith("_合集")
        dirs = [p] if _is_coll(d) else [x for x in candidates
                                        if os.path.isdir(x) and _is_coll(os.path.basename(x))]
        for vp in dirs:
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
                        f"{_safe_name(root_meta.title)} 合集")
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
            "out_dir": base,
            "tool": "everything-learn v0.1",
        }
        with open(os.path.join(base, "content_info.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        return summary

    for i, ref in enumerate(refs, 1):
        label = str(ref.sub_id or "1")
        print(f"[{i}/{len(refs)}] 处理 P{label}（{ref.title[:20]}）……")
        t0 = time.time()
        status, src, n, _out, err = process_item(adapter, ref, base, opts, single=False)
        if status == "ok":
            _kb_index(_out)
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
    try:
        report.write_batch_baseline(base, summary)
    except Exception:
        pass
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
        refs = _with_auth_retry(ref.platform, lambda: a.list_items(ref), a)
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
        if callable(hook):
            root_meta = _with_auth_retry(ref.platform, lambda: hook(ref, refs), a)
        else:
            root_meta = _with_auth_retry(ref.platform, lambda: a.fetch_meta(refs[0]), a)
    except PipelineError:
        raise
    except Exception as e:
        raise PipelineError(f"获取元信息失败：{str(e)[:120]}")
    if len(refs) == 1 and not opts.pages and not opts.all_:
        res = process_item(a, refs[0], os.path.abspath(opts.out_root), opts, single=True)
        if res and res[0] == "ok":
            _kb_index(res[3])
        return res
    return run_batch(a, refs, root_meta, opts)


# ---------------- 同源校验（音频抽查 + difflib 判定） ----------------

def _lang_base(code) -> str:
    """语言主代码归一：zh-Hans/zh-cn/zh_CN → zh；en-US → en；空/None → ''"""
    return re.split(r"[-_]", (code or "").strip().lower(), 1)[0]


def _same_source_verify(adapter, ref, lines, cfg, subtitle_language=""):
    """同源校验：音频抽查 → Whisper small → 相似度判定；
    任何异常/不可用 → checked=False（reason_code: no_audio|lang_mismatch|insufficient|error），
    降级不阻塞主流程。

    时长回退：ref.duration 缺失/为 0（适配器未回填）→ 用字幕末条 end 近似，
    避免 pick_spot_times(0) 只剩单点导致 insufficient 系统性误杀。
    语言门：字幕语言与音轨 ASR 语言不同（疑似翻译轨）→ lang_mismatch，
    不判同源，交视觉归属复检/翻译轨保护处理。"""
    import transcriber as tr
    tmpdir = tempfile.mkdtemp(prefix="el_ss_")
    try:
        media_urls = _media_candidates(adapter, ref, media="audio")
        if not media_urls:
            return {"checked": False, "reason": "无可下载音频流", "reason_code": "no_audio"}
        dur = float(ref.duration or 0) or (float(lines[-1]["end"]) if lines else 0)
        spots = recheck.pick_spot_times(dur)
        media_path = os.path.join(tmpdir, "audio.m4s")
        tr.download_audio(media_urls, media_path, headers=_media_headers(adapter))
        clips = []
        for s, e in spots:
            samples, sr = tr.extract_audio_clip(media_path, s, e)
            if len(samples) == 0:
                continue
            text, asr_lang = tr.transcribe_samples_info(
                samples, sr, model_size=cfg.get("recheck", {}).get("same_source_model", "small"))
            clips.append((s, e, text, asr_lang))
        asr_langs = {lang for (_s, _e, text, lang) in clips if text}
        if (subtitle_language and asr_langs
                and all(_lang_base(l) != _lang_base(subtitle_language) for l in asr_langs)):
            return {"checked": False, "passed": False, "reason_code": "lang_mismatch",
                    "subtitle_language": subtitle_language,
                    "asr_languages": sorted(asr_langs),
                    "reason": "字幕语言与音轨语言不同（疑似翻译轨），改走视觉归属复检"}
        result = recheck.same_source_check(
            [(s, e, text) for (s, e, text, _lang) in clips], lines, cfg)
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
        media_urls = _media_candidates(adapter, ref, media="video", lowest=True)
        if not media_urls:
            return {"passed": False, "reason": "无画面流", "reason_code": "no_video"}
        vp = os.path.join(tmpdir, "video.m4s")
        import transcriber as tr
        tr.download_audio(media_urls, vp, headers=_media_headers(adapter))
        from core import recheck as _rc
        dur = _rc._effective_duration(lines, ref.duration)
        return vision.check_attribution_aligned(conf, vp, lines, dur)
    except Exception as e:
        return {"passed": False, "reason": str(e)[:100], "reason_code": "error"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
