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
from core.pipeline import RunOptions, PipelineError
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
        print(json.dumps({"kind": "search", "keyword": args.input}, ensure_ascii=False))


def cmd_search(args):
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


def cmd_run(args, client: ApiClient = None):
    cfg = config.load_config()
    config.apply_hf_env(cfg)
    client = client or ApiClient(max_retries=cfg.get("api_retries", 4),
                                 retry_base_delay=cfg.get("api_retry_base", 1.5))
    try:
        out_root = config.resolve_out_dir(getattr(args, "out", None))
    except config.ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        sys.exit(2)
    try:
        if client.check_login() is False:
            print("⚠ 登录 cookie 已过期或未配置：搜索与 AI 字幕将不可用，仅可获取无字幕视频并走 Whisper 转写。", file=sys.stderr)
            print("  请更新 scripts/.bili_cookie（SESSDATA=xxx）或设置环境变量 BILI_COOKIE", file=sys.stderr)
    except Exception:
        pass  # 检测失败不阻断主流程
    if getattr(args, "fav", None):
        try:
            bvid, _t = _resolve_fav(client, args.fav, args.pick)
        except BiliError as e:
            print(f"收藏夹解析失败：{e}", file=sys.stderr)
            sys.exit(2)
        input_str = bvid
    else:
        input_str = args.input
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
    )

    def _run(inp):
        return pipeline.run_input(inp, opts, client=client)

    try:
        result = _run(input_str)
    except SearchNeeded:
        try:
            results = client.search(input_str, limit=max(1, args.pick))
        except BiliError as e:
            print(f"搜索失败：{e}（可能是风控，建议改用链接）", file=sys.stderr)
            sys.exit(2)
        if not results:
            print(f"未找到与「{input_str}」相关的视频", file=sys.stderr)
            sys.exit(1)
        hit = results[args.pick - 1]
        print(f"名称搜索命中：{hit['title']}（{hit['bvid']}）")
        try:
            result = _run(hit["bvid"])
        except PipelineError as e:
            print(f"获取失败：{e}", file=sys.stderr)
            sys.exit(4)
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
    s = sub.add_parser("search", help="搜索视频候选（bilibili）")
    s.add_argument("keyword"); s.add_argument("--limit", type=int, default=5)
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
    doc = sub.add_parser("doctor", help="环境自检（cookie/GPU/whisper/模型缓存/vision）")
    doc.set_defaults(func=_cmd_doctor)
    run = sub.add_parser("run", help="获取内容（字幕/转写）并落盘")
    run.add_argument("input", nargs="?", default=None, help="内容链接/名称（--fav 模式可省略）")
    g = run.add_mutually_exclusive_group()
    g.add_argument("--page", type=int, default=None, help="单分P页码（默认 1；与 --pages/--all 互斥）")
    g.add_argument("--pages", default=None, help='批量分P范围，如 "1-10" 或 "1,3,5-8"')
    g.add_argument("--all", dest="all_", action="store_true", help="批量处理全部分P")
    run.add_argument("--fav", default=None, help='从收藏夹选视频（收藏夹id或名称，配合 --pick 选第N个）')
    run.add_argument("--resume", action="store_true", help="跳过已成功处理的P（断点续跑）")
    run.add_argument("--lang", default=None, help='字幕语言（zh/ja/en等，默认自动中文优先）')
    run.add_argument("--model", default=None, help="whisper 模型（tiny/small/medium/large-v3，默认 config.whisper_model）")
    gv = run.add_mutually_exclusive_group()
    gv.add_argument("--vad", dest="vad", action="store_true",
                    help="强制开启 Whisper VAD（长视频默认关闭，可手动打开）")
    gv.add_argument("--no-vad", dest="vad", action="store_false", help="强制关闭 Whisper VAD")
    run.set_defaults(vad=None)
    run.add_argument("--no-vision-check", action="store_true", help="关闭多模态复检（默认开启：配置 vision 后固定执行）")
    run.add_argument("--out", default=None, help="报告输出根目录（未配置时必传：首次使用需用户自选）")
    run.add_argument("--no-whisper", action="store_true", help="无字幕时不转写直接失败")
    run.add_argument("--no-cache", dest="no_cache", action="store_true", help="不使用字幕/转写缓存（重新获取）")
    run.add_argument("--interval", type=float, default=1.5, help="批量分P之间的请求间隔秒数（默认 1.5，防接口限流）")
    run.add_argument("--pick", type=int, default=1, help="名称搜索时选第 N 个候选")
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
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
