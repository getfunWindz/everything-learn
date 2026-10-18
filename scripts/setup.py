"""一键初始化：依赖检查/安装（需明确同意）、报告目录、可选知识库、可选登录。

设计原则：
- 只自动化「非用户必须」操作；out_dir 必须由用户选择（--out 或交互输入）。
- 安装软件需 `--install` 显式同意；登录需 `--login`（已登录 profile 走 headless，无窗口）。
- 可被任意 harness 调用：纯 CLI，无 MCP/agent 依赖。
"""
import os
import subprocess
import sys

from core import auth, config

INSTALLABLE = {"faster-whisper", "yt-dlp", "playwright"}


def _missing():
    try:
        from doctor import missing_optional
        return [d for d in missing_optional() if d.get("name") in INSTALLABLE]
    except Exception:
        return []


def _pip_install(names, runner=subprocess.run) -> bool:
    names = sorted(n for n in set(names) if n in INSTALLABLE)
    if not names:
        return True
    print(f"安装：pip install {' '.join(names)}", file=sys.stderr)
    try:
        r = runner([sys.executable, "-m", "pip", "install", *names], check=False)
        return getattr(r, "returncode", 0) == 0
    except Exception as e:
        print(f"安装失败：{str(e)[:120]}", file=sys.stderr)
        return False


def run(out: str = None, install: bool = False, login: bool = False,
        kb: bool = None, yes: bool = False, ask=None,
        runner=subprocess.run) -> int:
    ask = ask or input
    config.ensure_config()

    missing = _missing()
    if missing:
        names = [d["name"] for d in missing]
        print(f"缺少可选依赖：{', '.join(names)}", file=sys.stderr)
        if install:
            if not _pip_install(names, runner):
                print("依赖安装失败：继续按低配模式运行（可稍后重试 setup --install）。",
                      file=sys.stderr)
        else:
            print("提示：加 --install 可自动安装（需网络）；不加则按低配模式运行。", file=sys.stderr)

    if not out:
        if yes:
            print("未指定报告目录：非交互模式（--yes）必须显式传 --out（硬性要求，不能默认写入未知路径）。",
                  file=sys.stderr)
            return 2
        out = (ask("报告根目录（必填，建议新建「学习笔记」总文件夹）：") or "").strip()
    out = (out or "").strip()
    if not out:
        print("未选择报告目录：请用 --out 指定（硬性要求，不能默认写未知路径）。", file=sys.stderr)
        return 2
    out = os.path.abspath(os.path.expanduser(out))

    if kb is None:
        if yes:
            # 非交互模式未显式选择：保留既有 enabled，只记录「已询问」。
            kb = bool((config.load_config().get("kb") or {}).get("enabled"))
        else:
            ans = (ask("是否内建本地知识库（把报告/正文索引进可搜索 KB，之后可 el.py kb search；"
                       "支持关键词/平台/作者/日期过滤）？[y/N]：") or "").strip().lower()
            kb = ans in ("y", "yes", "是", "1")

    updates = {"out_dir": out, "kb": {"enabled": bool(kb), "asked": True}}
    config.write_user_config(updates)
    try:
        import onboard
        onboard.complete()
    except Exception as e:
        print(f"引导置位失败（可稍后 el.py onboard --complete）：{str(e)[:120]}", file=sys.stderr)

    if login:
        for p in ("bilibili", "zhihu", "weibo", "douyin"):
            ok = auth.ensure(p, interactive=True)
            print(f"  {'✓' if ok else '·'} {p} 登录态{'已就绪' if ok else '未就绪（可稍后 el.py cookie ' + p + '）'}")

    print("\n初始化完成：")
    print(f"  报告目录：{out}")
    if kb:
        print("  知识库：开启（先 el.py kb build \"<学习笔记根目录>\" 建库；之后 el.py kb search \"<词>\"）")
    else:
        print("  知识库：未开启（特征位 config.kb.enabled=false；可 el.py onboard --kb on 重新开启）")
    print("  下一步：python scripts/el.py run \"<链接>\"")
    return 0
