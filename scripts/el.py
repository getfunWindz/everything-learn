"""everything-learn CLI：多平台学习内容获取与报告骨架生成"""
import argparse
import glob
import json
import logging
import os
import sys
import time
from datetime import datetime

from core import config, pipeline
from core.adapter import SearchNeeded
from core.pipeline import RunOptions, PipelineError, UnsupportedUrlError
from providers.bilibili import ApiClient, BiliError, resolve_input

VERSION = "0.1.0"
_LOGGER = logging.getLogger("el")


def setup_logging(logs_dir: str = None) -> str:
    """初始化日志：logs/el.log（append）。返回日志文件路径"""
    logs_dir = logs_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    path = os.path.join(logs_dir, "el.log")
    _LOGGER.handlers.clear()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    return path


def _fmt_ts(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def cmd_resolve(args):
    try:
        spec = resolve_input(args.input)
        print(json.dumps({"kind": "video", "bvid": spec.bvid, "aid": spec.aid,
                          "page": spec.page}, ensure_ascii=False))
    except SearchNeeded:
        text = str(args.input or "").strip()
        if text.lower().startswith(("http://", "https://")):
            print(json.dumps({"kind": "unsupported", "url": text}, ensure_ascii=False))
            print("未知平台 URL：没有匹配的适配器。按 SKILL「自进化」流程：\n"
                  "  1) 可行性尽调（形态/获取方式/鉴权/风控/可得性/结论）\n"
                  "  2) 确认可自动化后按 docs/ADAPTER_TEMPLATE.md 实现\n"
                  "  3) 通过 fixture 单测 + 契约测试 + 真实链接冒烟后再合入",
                  file=sys.stderr)
            return
        print(json.dumps({"kind": "search", "keyword": args.input}, ensure_ascii=False))


def _wechat_adapter():
    """公众号适配器（注册表单例；测试可替换）"""
    from core import registry
    registry.load_providers()
    return registry.get("wechat_mp")


def _search_wechat(args):
    adapter = _wechat_adapter()
    if adapter is None:
        print("未找到 wechat_mp 适配器（检查 scripts/providers/wechat_mp.py）", file=sys.stderr)
        sys.exit(2)
    try:
        hits = adapter.search_articles(args.keyword, limit=getattr(args, "limit", 5))
    except Exception as e:
        print(f"搜索失败：{e}", file=sys.stderr)
        print("提示：搜狗微信搜索可能有验证码/限流；也可直接提供文章链接。", file=sys.stderr)
        sys.exit(2)
    if not hits:
        print("无搜索结果", file=sys.stderr)
        sys.exit(1)
    for i, h in enumerate(hits, 1):
        print(f"{i}. {h['title']} — {h['account']}（{h['date']}）")
    print("\n提示：搜狗结果链接会话绑定，不能跨进程复用；直接抓取请运行：", file=sys.stderr)
    print(f'  python scripts/el.py run --platform wechat "{args.keyword}" --pick N', file=sys.stderr)


def cmd_search(args):
    platform = getattr(args, "platform", "bilibili")
    if platform in ("wechat", "wechat_mp"):
        return _search_wechat(args)
    client = ApiClient()
    try:
        results = client.search(args.keyword, limit=args.limit)
    except BiliError as e:
        print(f"搜索失败：{e}", file=sys.stderr)
        print("提示：未登录的 bilibili 搜索会被风控。可选：", file=sys.stderr)
        print("  1. 直接提供视频链接或 BV 号（推荐）", file=sys.stderr)
        print("  2. 配置登录 cookie：把 SESSDATA=xxx 写入 scripts/.bili_cookie 或设置环境变量 BILI_COOKIE", file=sys.stderr)
        sys.exit(2)
    if not results:
        print("无搜索结果", file=sys.stderr)
        sys.exit(1)
    for i, r in enumerate(results, 1):
        print(f"{i}. [{r['bvid']}] {r['title']} — UP主: {r['author']}（时长 {r['duration']}）")


def _find_report_md(target: str) -> str:
    """查找目录中的报告文件：report.md → <标题>_学习报告.md → report_template.md"""
    for name in ("report.md",):
        p = os.path.join(target, name)
        if os.path.exists(p):
            return p
    hits = glob.glob(os.path.join(target, "*_学习报告.md"))
    if hits:
        return hits[0]
    p = os.path.join(target, "report_template.md")
    if os.path.exists(p):
        return p
    return ""


def cmd_report(args):
    """从已有目录（单条/合集）重新生成报告骨架，无需重新抓取"""
    target = args.target
    vp = os.path.join(target, "content_info.json")
    if not os.path.exists(vp):
        print(f"目录中没有 content_info.json：{target}", file=sys.stderr)
        sys.exit(1)
    info = json.load(open(vp, encoding="utf-8"))
    import report
    out = os.path.join(target, "report_template.md")
    if info.get("kind") == "batch":
        open(out, "w", encoding="utf-8").write(report.render_batch_template(info))
        print(f"已重新生成总报告骨架：{out}")
    else:
        open(out, "w", encoding="utf-8").write(
            report.render_template_for(info.get("kind", "timeline"), info))
        print(f"已重新生成报告骨架：{out}")


def cmd_export(args):
    """从报告目录导出 HTML/DOCX"""
    target = args.target
    md_path = _find_report_md(target)
    if not md_path:
        print(f"目录中没有报告文件（report.md / <标题>_学习报告.md / report_template.md）：{target}", file=sys.stderr)
        sys.exit(1)
    md = open(md_path, encoding="utf-8").read()
    from export import md_to_html, export_docx
    name = os.path.splitext(os.path.basename(md_path))[0]
    if args.format == "html":
        out = args.out or os.path.join(target, name + ".html")
        open(out, "w", encoding="utf-8").write(md_to_html(md))
    else:
        out = args.out or os.path.join(target, name + ".docx")
        export_docx(md, out)
    print(f"已导出：{out}")


def _scan_adapter(input_str):
    """scan 用适配器解析（URL → 适配器；测试可替换）"""
    from core import registry
    registry.load_providers()
    return registry.match_url(input_str) or registry.sniff_url(input_str)


def cmd_onboard(args, client: ApiClient = None):
    """首次引导门：状态/完成/重置（SKILL.md 标签位 + config.onboarded，2026-10-17）"""
    import onboard as ob
    if getattr(args, "complete", False):
        try:
            r = ob.complete()
        except ob.OnboardError as e:
            print(f"引导未完成：{e}", file=sys.stderr)
            sys.exit(2)
        note = "标签位已同步置 0" if r.get("flag_written") else "未找到 SKILL.md 标记（已跳过文件改写）"
        print(f"✅ 引导已完成（onboarded=true；{note}）。今后将直接进入正常使用状态。")
        return
    if getattr(args, "reset", False):
        r = ob.reset()
        note = "标签位已同步置 1" if r.get("flag_written") else "未找到 SKILL.md 标记（已跳过文件改写）"
        print(f"↺ 引导已重置（onboarded=false；{note}）。下次运行将重新引导。")
        return
    st = ob.read_status()
    if getattr(args, "json", False):
        print(json.dumps(st, ensure_ascii=False))
        return
    flag = st["flag"]
    print("=== everything-learn 首次引导（onboarding） ===")
    print(f"状态：ONBOARDING = {flag if flag is not None else '（未解析到标签位）'}"
          "（1=未完成，必须完成下方四项确认；0=已完成，可跳过文末引导）")
    print()
    print("① 产出目录（硬性，必须由用户自选）")
    print(f"   当前：{st['out_dir'] or '未配置'}")
    print("   建议：新建「学习笔记」总文件夹；确认后写入 config.out_dir（或用 --out 临时覆盖）。")
    print()
    print("② Whisper 档位（转写质量/速度取舍）")
    print("   fast = small（出厂默认，速度快、资源小）")
    print("   medium（强烈推荐：实机验证效果最佳）｜ large-v3（最高，资源要求高）")
    print(f"   当前：{st['whisper_model'] or '未配置'}")
    print("   建议：硬件允许时升级 medium（config.whisper_model 或 --model medium）。")
    print()
    print("③ 依赖清单（推荐全部安装以获得完整体验）")
    import doctor
    missing = doctor.missing_optional()
    if not missing:
        print("   ✅ 可选依赖已全部就绪")
    else:
        for d in missing:
            print(f"   ❌ {d['name']}：{d['hint']}")
    print()
    print("④ 使用说明（一图流）")
    print("   用途：把视频/音频/图文/帖子总结为中文详解学习报告（11 平台）。")
    print("   常用：el.py run <链接> [--all/--pages/--model/--budget] ｜ el.py scan <列表链接>")
    print("   产物：content_info.json / content.txt / report_template.md / recheck_report.json（+assets/）")
    print("   报告规则见 SKILL.md「正常调用工作流」。")
    print()
    print("⑤ 术语库（可选：填塞初始术语；使用中自动反哺）")
    try:
        import glossary as _gl
        _g = _gl.load_glossary()
        _n_terms = len([k for k in _g if not k.startswith("_")])
        _n_alias = len(_gl.get_aliases(_g))
        print(f"   当前：{_n_terms} 条术语 / {_n_alias} 条勘误别名")
    except Exception:
        print("   当前：术语库不可读（可稍后用 scripts/glossary.py 检查）")
    print("   填塞（可选）：python scripts/glossary.py add \"<术语>\" \"<解释>\"（用户提供领域名词即可）")
    print("   自动反哺：每次转写按勘误库筛验术语名；画面校正自动沉淀别名；报告写作沉淀新术语；")
    print("             知识库吸收：python scripts/glossary.py absorb <wiki目录>")
    print("   推荐：配套 skill bilibili-to-wiki（个人知识库填充接口，已适配本技能）")
    print()
    print("完成后执行：python scripts/el.py onboard --complete")


def cmd_update(args, client: ApiClient = None):
    """更新技能仓库（git pull + 自动恢复引导标签位，2026-10-17）"""
    import update as upd_mod
    return upd_mod.main(["--dry-run"] if getattr(args, "dry_run", False) else [])


def cmd_scan(args, client: ApiClient = None):
    """账号级扫描：枚举列表 → 过滤/优先级/快照（不抓内容）"""
    if not config.load_config().get("onboarded"):
        print("ℹ️ 引导未完成：建议先完成首次引导（python scripts/el.py onboard 查看清单；"
              "完成后 --complete 置位；说明见 SKILL.md 文末）。", file=sys.stderr)
    from core import scan as scan_core
    if not str(args.input).startswith(("http://", "https://")):
        print("scan 需要 URL（名称类请先 search，或直接 run）", file=sys.stderr)
        sys.exit(2)
    adapter = _scan_adapter(args.input)
    if adapter is None:
        from core import registry
        print(f"没有适配器能处理：{args.input}", file=sys.stderr)
        print(registry.SELF_EVOLUTION_HINT, file=sys.stderr)
        sys.exit(2)
    try:
        ref = adapter.resolve(args.input)
        refs = adapter.list_items(ref)
    except SearchNeeded:
        print("该输入需要平台搜索，scan 暂不支持名称类", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"枚举失败：{str(e)[:120]}", file=sys.stderr)
        sys.exit(4)
    if args.limit:
        refs = refs[:args.limit]
    items = scan_core.refs_to_items(refs)
    keywords = ([k.strip() for k in args.filter.split(",") if k.strip()]
                if args.filter else scan_core.AI_KEYWORDS)
    hit = scan_core.filter_items(items, keywords)
    ranked = scan_core.sort_by_priority(hit) if args.priority else hit
    print(f"[{adapter.name}] 共 {len(items)} 条，命中 {len(hit)} 条：")
    for i, it in enumerate(ranked, 1):
        score = f"（{it['view']}）" if it.get("view") else ""
        print(f"{i}. {it['title'][:60]}{score}")
    print("\n批量抓取：python scripts/el.py run \"<原链接>\" --all（或 --pages \"1-10\"）")
    if args.snapshot:
        path = scan_core.make_snapshot(items, out_dir=args.out or "", prefix=adapter.name)
        print(f"快照已保存：{path}")


def cmd_platforms(args, client: ApiClient = None):
    """列出已注册平台适配器（名单）+ 自进化入口"""
    from core import registry
    registry.load_providers()
    for a in sorted(registry.all(), key=lambda x: x.name):
        print(f"{a.name:12s} kinds={','.join(sorted(a.kinds))}")
    print("\n名单外平台走自进化流程（SKILL §3）：")
    print(registry.SELF_EVOLUTION_HINT)


def cmd_toview(args, client: ApiClient = None):
    """列出稍后再看（需登录 cookie）"""
    client = client or ApiClient()
    try:
        items = client.get_toview()
    except BiliError as e:
        print(f"获取稍后再看失败：{e}（需要有效登录 cookie：el.py cookie bilibili）", file=sys.stderr)
        sys.exit(2)
    if not items:
        print("稍后再看为空", file=sys.stderr)
        sys.exit(1)
    for i, it in enumerate(items, 1):
        print(f"{i}. [{it['bvid']}] {it['title']}（{it.get('owner', '')}）")


def cmd_history(args, client: ApiClient = None):
    """列出观看历史（需登录 cookie）"""
    client = client or ApiClient()
    try:
        items = client.get_history(limit=getattr(args, "limit", 30))
    except BiliError as e:
        print(f"获取历史失败：{e}（需要有效登录 cookie）", file=sys.stderr)
        sys.exit(2)
    for i, it in enumerate(items, 1):
        print(f"{i}. [{it['bvid']}] {it['title']}")


def cmd_following(args, client: ApiClient = None):
    """列出关注（需登录 cookie）"""
    client = client or ApiClient()
    try:
        items = client.get_followings(limit=getattr(args, "limit", 50))
    except BiliError as e:
        print(f"获取关注失败：{e}（需要有效登录 cookie）", file=sys.stderr)
        sys.exit(2)
    for i, it in enumerate(items, 1):
        print(f"{i}. [{it['mid']}] {it['uname']} — {it.get('sign', '')}")


def cmd_favs(args, client: ApiClient = None):
    """列出当前账号的收藏夹"""
    client = client or ApiClient()
    try:
        folders = client.get_fav_folders()
    except BiliError as e:
        print(f"获取收藏夹失败：{e}（需要有效登录 cookie）", file=sys.stderr)
        sys.exit(2)
    if not folders:
        print("没有收藏夹", file=sys.stderr)
        sys.exit(1)
    for i, f in enumerate(folders, 1):
        print(f"{i}. [{f['id']}] {f['title']}（{f['media_count']}个视频）")


def _resolve_fav(client: ApiClient, fav: str, pick: int):
    """解析 --fav：按 id 或名称匹配收藏夹，返回 (bvid, title)"""
    folders = client.get_fav_folders()
    folder = None
    if fav.isdigit():
        folder = next((f for f in folders if str(f["id"]) == fav), None)
    if folder is None:
        folder = next((f for f in folders if fav in f["title"]), None)
    if folder is None:
        raise BiliError(-400, "未找到收藏夹：" + fav + "（现有：" + '，'.join(f['title'] for f in folders) + "）")
    medias = client.get_fav_medias(folder["id"], pn=1, ps=20)
    if pick > 20:  # 超出第一页：拉后续页
        medias += client.get_fav_medias(folder["id"], pn=2, ps=20)
    if pick > 40:
        medias += client.get_fav_medias(folder["id"], pn=3, ps=20)
    if not medias:
        raise BiliError(-400, f"收藏夹「{folder['title']}」为空")
    idx = pick - 1
    if idx < 0 or idx >= len(medias):
        raise BiliError(-400, f"收藏夹第 {pick} 个视频不存在（共 {len(medias)} 个）")
    m = medias[idx]
    print(f"收藏夹「{folder['title']}」第{pick}个：{m['title']}（{m['bvid']}）", file=sys.stderr)
    return m["bvid"], m["title"]


def cmd_frames(args, client: ApiClient = None):
    """抽帧保存 JPEG 文件——供多模态 agent 用 read 工具读图执行复检"""
    import tempfile
    import transcriber as tr
    import vision
    client = client or ApiClient()
    spec = resolve_input(args.input)
    info = client.get_video_info(bvid=spec.bvid, aid=spec.aid)
    page_obj = info.page_by_index(spec.page if spec.page else 1)
    print(f"下载视频流并抽帧：{info.title[:30]} P{page_obj.page}", file=sys.stderr)
    url = client.get_video_url(info.bvid, page_obj.cid)
    with tempfile.TemporaryDirectory() as td:
        vp = os.path.join(td, "video.m4s")
        tr.download_audio(url, vp)
        if getattr(args, "range", None):
            s, e = (float(x) for x in args.range.split("-"))
            frames = vision.extract_frames_range(vp, s, e, fps=args.fps, max_frames=args.max)
        else:
            frames = vision.extract_frames(vp, interval_sec=10, max_frames=args.max)
    os.makedirs(args.out, exist_ok=True)
    for ts, jpeg in frames:
        with open(os.path.join(args.out, f"frame_{ts}s.jpg"), "wb") as f:
            f.write(jpeg)
    print(f"已保存 {len(frames)} 帧 → {args.out}")
    if not args.range:
        print("提示：无人声视频建议加 --range \"起-止\" 指定区间、--fps 1 每秒一帧", file=sys.stderr)


def _confirm_candidate(args, items, label: str, fmt) -> int:
    """名称搜索候选闸门：始终列出候选；未显式 --pick/--yes 时拒绝静默取第一条"""
    for i, it in enumerate(items, 1):
        print(fmt(i, it))
    pick = getattr(args, "pick", None)
    if pick is None and not getattr(args, "yes", False):
        print(f"请用 --pick N 指定要抓取的{label}（上方共 {len(items)} 条候选）；"
              "确定取第一条可加 --yes", file=sys.stderr)
        sys.exit(2)
    pick = pick or 1
    if pick < 1 or pick > len(items):
        print(f"--pick {pick} 超出候选范围 1..{len(items)}", file=sys.stderr)
        sys.exit(2)
    return pick


def cmd_run(args, client: ApiClient = None):
    cfg = config.load_config()
    config.apply_hf_env(cfg)
    if not cfg.get("onboarded"):
        print("ℹ️ 引导未完成：建议先完成首次引导（python scripts/el.py onboard 查看清单；"
              "完成后 --complete 置位；说明见 SKILL.md 文末）。", file=sys.stderr)
    client = client or ApiClient(max_retries=cfg.get("api_retries", 4),
                                 retry_base_delay=cfg.get("api_retry_base", 1.5))
    platform = getattr(args, "platform", None)
    forced_adapter = None
    try:
        out_root = config.resolve_out_dir(getattr(args, "out", None))
    except config.ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        sys.exit(2)
    def _skip_bili_check():
        """非 B 站平台运行（wechat 指定 / 已匹配非 bilibili 适配器的 URL）跳过 B 站登录检查"""
        if platform in ("wechat", "wechat_mp"):
            return True
        if getattr(args, "fav", None):
            return False
        s = str(getattr(args, "input", "") or "")
        if not s.startswith(("http://", "https://")):
            return False
        from core import registry
        registry.load_providers()
        a = registry.match_url(s)
        return a is not None and a.name != "bilibili"

    if not _skip_bili_check():
        try:
            if client.check_login() is False:
                print("⚠ 登录 cookie 已过期或未配置：搜索与 AI 字幕将不可用，仅可获取无字幕视频并走 Whisper 转写。", file=sys.stderr)
                print("  请更新 scripts/.bili_cookie（SESSDATA=xxx）或设置环境变量 BILI_COOKIE", file=sys.stderr)
        except Exception:
            pass  # 检测失败不阻断主流程
    if getattr(args, "fav", None):
        try:
            bvid, _t = _resolve_fav(client, args.fav, args.pick or 1)
        except BiliError as e:
            print(f"收藏夹解析失败：{e}", file=sys.stderr)
            sys.exit(2)
        input_str = bvid
    else:
        input_str = args.input
    if platform in ("wechat", "wechat_mp") and not str(input_str).startswith(("http://", "https://")):
        adapter = _wechat_adapter()
        if adapter is None:
            print("未找到 wechat_mp 适配器（检查 scripts/providers/wechat_mp.py）", file=sys.stderr)
            sys.exit(2)
        try:
            hits = adapter.search_articles(input_str, limit=max(args.pick or 5, 5))
        except Exception as e:
            print(f"搜索失败：{e}（搜狗可能触发验证码，建议改用文章链接）", file=sys.stderr)
            sys.exit(2)
        if not hits:
            print(f"未找到与「{input_str}」相关的文章", file=sys.stderr)
            sys.exit(1)
        pick = _confirm_candidate(
            args, hits, "文章",
            lambda i, h: f"{i}. {h['title']} — {h['account']}（{h['date']}）")
        hit = hits[pick - 1]
        print(f"名称搜索命中（{pick}/{len(hits)}）：{hit['title']}（{hit['account']}）")
        try:
            input_str = adapter.resolve_sogou_link(hit["link"])
        except Exception as e:
            print(f"搜狗跳转解析失败：{e}", file=sys.stderr)
            sys.exit(4)
        forced_adapter = adapter
    opts = RunOptions(
        out_root=out_root,
        page=getattr(args, "page", None),
        pages=getattr(args, "pages", None),
        all_=getattr(args, "all_", False),
        lang=getattr(args, "lang", None),
        model=getattr(args, "model", None),
        no_cache=getattr(args, "no_cache", False),
        no_whisper=getattr(args, "no_whisper", False),
        no_vision_check=getattr(args, "no_vision_check", False),
        vad=getattr(args, "vad", None),
        resume=getattr(args, "resume", False),
        interval=getattr(args, "interval", 1.5),
        recheck=("off" if getattr(args, "no_recheck", False) else getattr(args, "recheck", None)),
        budget=getattr(args, "budget", None),
        yes_recheck=getattr(args, "yes_recheck", False),
    )

    def _run(inp):
        return pipeline.run_input(inp, opts, adapter=forced_adapter, client=client)

    try:
        result = _run(input_str)
    except SearchNeeded:
        if platform in ("wechat", "wechat_mp"):
            print(f"无法定位内容：{input_str}（公众号输入需可解析的文章链接）", file=sys.stderr)
            sys.exit(4)
        if str(input_str or "").lower().startswith(("http://", "https://")):
            print(f"未知平台 URL：{input_str}\n"
                  "没有匹配的适配器，不会回落成关键词搜索。按 SKILL「自进化」流程：\n"
                  "  1) 可行性尽调（形态/获取方式/鉴权/风控/可得性/结论）\n"
                  "  2) 确认可自动化后按 docs/ADAPTER_TEMPLATE.md 实现\n"
                  "  3) fixture 单测 + 契约测试 + 真实链接冒烟通过后再合入",
                  file=sys.stderr)
            sys.exit(2)
        try:
            results = client.search(input_str, limit=max(args.pick or 5, 5))
        except BiliError as e:
            print(f"搜索失败：{e}（可能是风控，建议改用链接）", file=sys.stderr)
            sys.exit(2)
        if not results:
            print(f"未找到与「{input_str}」相关的视频", file=sys.stderr)
            sys.exit(1)
        pick = _confirm_candidate(
            args, results, "视频",
            lambda i, h: f"{i}. {h['title']}（{h['bvid']}）")
        hit = results[pick - 1]
        print(f"名称搜索命中（{pick}/{len(results)}）：{hit['title']}（{hit['bvid']}）")
        try:
            result = _run(hit["bvid"])
        except PipelineError as e:
            print(f"获取失败：{e}", file=sys.stderr)
            sys.exit(4)
    except UnsupportedUrlError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    except ValueError as e:
        print(f"参数错误：{e}", file=sys.stderr)
        sys.exit(2)
    except PipelineError as e:
        print(f"获取失败：{e}", file=sys.stderr)
        sys.exit(4)
    except BiliError as e:
        print(f"获取失败：{e}", file=sys.stderr)
        sys.exit(4)
    if isinstance(result, tuple):
        status, src, n, out_dir, err = result
        if status != "ok":
            msgs = {"no_subtitle": "该视频无字幕（--no-whisper 已跳过转写）",
                    "failed": "获取失败",
                    "whisper_missing": "未安装 faster-whisper"}
            print(msgs.get(status, status), file=sys.stderr)
            if status == "whisper_missing":
                print("可选：1) pip install faster-whisper 后重试；2) 换一个带字幕的视频；3) --no-whisper 跳过", file=sys.stderr)
            _LOGGER.info("single %s %s %s", input_str, status, err)
            sys.exit({"no_subtitle": 3, "failed": 4, "whisper_missing": 5}.get(status, 4))
        _LOGGER.info("single %s ok %s %d行", input_str, src, n)
        print(f"\n完成！输出目录：{out_dir}")
        print("已生成：content_info.json / content.txt / report_template.md")


def main(argv=None):
    setup_logging()
    config.ensure_config()
    p = argparse.ArgumentParser(prog="el", description="everything-learn 学习内容获取（多平台）")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="解析输入为平台标识")
    r.add_argument("input"); r.set_defaults(func=cmd_resolve)
    s = sub.add_parser("search", help="搜索视频候选（bilibili/wechat）")
    s.add_argument("keyword"); s.add_argument("--limit", type=int, default=5)
    s.add_argument("--platform", default="bilibili", choices=["bilibili", "wechat"])
    s.set_defaults(func=cmd_search)
    fv = sub.add_parser("favs", help="列出账号收藏夹")
    fv.set_defaults(func=cmd_favs)
    fr = sub.add_parser("frames", help="抽帧保存 JPEG（供多模态 agent 读图复检）")
    fr.add_argument("input", help="视频链接/BV/av")
    fr.add_argument("--range", default=None, help='区间 "起-止"（秒），如 "10-60"；缺省全视频每 10s 抽帧')
    fr.add_argument("--fps", type=int, default=1, help="区间内每秒抽帧数（默认 1）")
    fr.add_argument("--max", type=int, default=30, help="最大帧数（默认 30）")
    fr.add_argument("--out", default="frames", help="输出目录")
    fr.set_defaults(func=cmd_frames)
    tv = sub.add_parser("toview", help="列出稍后再看（B站，需登录 cookie）")
    tv.set_defaults(func=cmd_toview)
    hi = sub.add_parser("history", help="列出观看历史（B站，需登录 cookie）")
    hi.add_argument("--limit", type=int, default=30)
    hi.set_defaults(func=cmd_history)
    fo = sub.add_parser("following", help="列出关注（B站，需登录 cookie）")
    fo.add_argument("--limit", type=int, default=50)
    fo.set_defaults(func=cmd_following)
    sc = sub.add_parser("scan", help="账号级扫描：枚举列表→过滤/优先级/快照（不抓内容）")
    sc.add_argument("input", help="列表链接（用户主页/收藏夹/播放列表/频道）")
    sc.add_argument("--filter", default=None, help="关键词（逗号分隔；默认内置 AI 关键词）")
    sc.add_argument("--priority", action="store_true", help="按热度降序")
    sc.add_argument("--limit", type=int, default=0, help="最多枚举条数（0=全部）")
    sc.add_argument("--snapshot", action="store_true", help="保存 JSON 快照")
    sc.add_argument("--out", default=None, help="快照目录")
    sc.set_defaults(func=cmd_scan)
    fs = sub.add_parser("favs-scan", help="扫描收藏夹：拉全量+主题过滤+优先级排序+快照")
    fs.add_argument("fav", help="收藏夹 id 或名称")
    fs.add_argument("--filter", default=None, help="关键词列表（逗号分隔，默认内置 AI 关键词）")
    fs.add_argument("--priority", action="store_true", help="按播放量降序输出建议处理顺序")
    fs.add_argument("--out", default=None, help="快照输出目录")
    fs.add_argument("--snapshot", action="store_true", help="保存快照 JSON（供增量对比）")
    fs.set_defaults(func=lambda a: _cmd_favs_scan(a))
    def _cmd_favs_scan(args):
        import favs
        return favs.cmd_favs_scan(args)
    def _cmd_doctor(args):
        import doctor
        return doctor.main()
    pf = sub.add_parser("platforms", help="列出已注册平台适配器（名单）+ 自进化入口")
    pf.set_defaults(func=cmd_platforms)
    doc = sub.add_parser("doctor", help="环境自检（cookie/GPU/whisper/模型缓存/vision）")
    doc.set_defaults(func=_cmd_doctor)
    obp = sub.add_parser("onboard", help="首次引导门：状态/完成/重置（SKILL.md 标签位 + config.onboarded）")
    obp.add_argument("--complete", action="store_true", help="完成引导（校验 out_dir；标签位置 0）")
    obp.add_argument("--reset", action="store_true", help="重开引导（标签位置 1）")
    obp.add_argument("--json", action="store_true", help="输出 JSON 状态")
    obp.set_defaults(func=cmd_onboard)
    upd = sub.add_parser("update", help="更新技能仓库（git pull + 自动恢复引导标签位）")
    upd.add_argument("--dry-run", action="store_true", help="只显示计划，不做修改")
    upd.set_defaults(func=cmd_update)
    run = sub.add_parser("run", help="获取内容（字幕/转写）并落盘")
    run.add_argument("input", nargs="?", default=None, help="内容链接/名称（--fav 模式可省略）")
    g = run.add_mutually_exclusive_group()
    g.add_argument("--page", type=int, default=None, help="单分P页码（默认 1；与 --pages/--all 互斥）")
    g.add_argument("--pages", default=None, help='批量分P范围，如 "1-10" 或 "1,3,5-8"')
    g.add_argument("--all", dest="all_", action="store_true", help="批量处理全部分P")
    run.add_argument("--fav", default=None, help='从收藏夹选视频（收藏夹id或名称，配合 --pick 选第N个）')
    run.add_argument("--platform", default=None,
                     help="指定平台（wechat=公众号搜索后立即抓取；默认自动路由）")
    run.add_argument("--resume", action="store_true", help="跳过已成功处理的P（断点续跑）")
    run.add_argument("--lang", default=None, help='字幕语言（zh/ja/en等，默认自动中文优先）')
    run.add_argument("--model", default=None, help="whisper 模型（tiny/small/medium/large-v3，默认 config.whisper_model）")
    gv = run.add_mutually_exclusive_group()
    gv.add_argument("--vad", dest="vad", action="store_true",
                    help="强制开启 Whisper VAD（长视频默认关闭，可手动打开）")
    gv.add_argument("--no-vad", dest="vad", action="store_false", help="强制关闭 Whisper VAD")
    run.set_defaults(vad=None)
    run.add_argument("--no-vision-check", action="store_true", help="关闭多模态复检（默认开启：配置 vision 后固定执行）")
    run.add_argument("--recheck", default=None, choices=["auto", "1", "2", "3"],
                     help="复检档位：auto 评分 / 1 定向 / 2 全量 / 3 不复检（默认 config.recheck.tier_policy）")
    run.add_argument("--no-recheck", dest="no_recheck", action="store_true",
                     help="= --recheck 3（显式豁免，产物标注）")
    run.add_argument("--budget", type=float, default=None, help="本次运行复检预算上限（金额）")
    run.add_argument("--yes-recheck", dest="yes_recheck", action="store_true",
                     help="预授权超阈值按完整档2 执行")
    run.add_argument("--out", default=None, help="报告输出根目录（未配置时必传：首次使用需用户自选）")
    run.add_argument("--no-whisper", action="store_true", help="无字幕时不转写直接失败")
    run.add_argument("--no-cache", dest="no_cache", action="store_true", help="不使用字幕/转写缓存（重新获取）")
    run.add_argument("--interval", type=float, default=1.5, help="批量分P之间的请求间隔秒数（默认 1.5，防接口限流）")
    run.add_argument("--pick", type=int, default=None,
                     help="名称搜索时选第 N 个候选（不传则只列候选并退出）")
    run.add_argument("--yes", action="store_true",
                     help="名称搜索时直接取第一条候选（非交互；默认要求 --pick）")
    run.set_defaults(func=cmd_run)
    def _cmd_merge(args):
        import mergeutil
        return mergeutil.cmd_merge(args)
    mg = sub.add_parser("merge", help="合并两个合集目录（复制缺失的 P 子目录，不覆盖已有）")
    mg.add_argument("src", help="源合集目录")
    mg.add_argument("dst", help="目标合集目录")
    mg.set_defaults(func=_cmd_merge)
    rep = sub.add_parser("report", help="从已有目录（单条/合集）重新生成报告骨架")
    rep.add_argument("target", help="单条或合集目录")
    rep.set_defaults(func=cmd_report)
    ex = sub.add_parser("export", help="导出报告为 HTML/DOCX")
    ex.add_argument("target", help="含 report.md 的目录")
    ex.add_argument("--format", choices=["html", "docx"], default="html")
    ex.add_argument("--out", default=None, help="输出文件路径（默认在报告目录）")
    ex.set_defaults(func=cmd_export)
    def _cmd_cookie(args):
        import cookie_export
        if getattr(args, "list", False):
            sys.exit(cookie_export.main(["--list"]))
        if not args.platform:
            print("用法：el.py cookie <platform>（--list 查看可用平台）", file=sys.stderr)
            sys.exit(2)
        sys.exit(cookie_export.export(args.platform, args.browser, args.timeout))
    ck = sub.add_parser("cookie", help="一键导出平台 cookie（Playwright + 系统浏览器）")
    ck.add_argument("platform", nargs="?", help="平台标识（如 zhihu/bilibili）")
    ck.add_argument("--list", action="store_true", help="列出已声明导出的平台")
    ck.add_argument("--browser", default="msedge", choices=["msedge", "chrome"])
    ck.add_argument("--timeout", type=int, default=300, help="等待登录秒数（默认 300）")
    ck.set_defaults(func=_cmd_cookie)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
