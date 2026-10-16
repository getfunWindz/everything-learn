"""everything-learn 更新助手（2026-10-17）

安全更新（git pull）本技能仓库，自动处理「引导标签位」（SKILL.md ONBOARDING）的本地修改：
  1) 丢弃 SKILL.md 的本地标签位改动（更新后自动按原状态恢复）
  2) 其他本地改动 → git stash（更新后 pop 恢复）
  3) git pull --ff-only
  4) 若更新前已引导完成 → onboard.complete()（标签位 0 / config.onboarded=true 恢复）
  5) stash 恢复（如有）

用法：
  python scripts/update.py            # 执行更新
  python scripts/update.py --dry-run  # 只显示计划，不做任何修改
（等价入口：python scripts/el.py update [--dry-run]）
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # 技能根目录（= git 仓库根）


class UpdateError(Exception):
    """更新失败（可读）"""


def _git(args: list):
    """执行 git（cwd=技能根）；返回 (rc, stdout, stderr)

    注意：stdout 仅 rstrip 换行——porcelain 首行前导空格有语义（XY 状态列），不能 strip。"""
    p = subprocess.run(["git"] + args, cwd=ROOT, capture_output=True, text=True, timeout=120)
    return p.returncode, (p.stdout or "").rstrip("\n"), (p.stderr or "").strip()


def _read_onboarded() -> bool:
    """更新前是否已引导完成（config.onboarded 或 SKILL.md 标签位=0）"""
    try:
        import onboard
        st = onboard.read_status()
        return bool(st.get("onboarded") or st.get("flag") == 0)
    except Exception:
        return False


def _restore_onboarded() -> str:
    """恢复引导状态（更新后调用）；返回步骤描述"""
    import onboard
    r = onboard.complete()
    return ("恢复引导标签位 0（onboard --complete）" if r.get("flag_written")
            else "恢复 config.onboarded=true（SKILL.md 标记未找到，已跳过文件改写）")


def plan() -> dict:
    """只读：更新计划 {head, dirty_files, skill_dirty, others, onboarded}"""
    rc, out, err = _git(["status", "--porcelain"])
    if rc != 0:
        raise UpdateError(f"不是 git 仓库或 git 不可用：{err or out}")
    files = [line[3:].strip() for line in out.splitlines() if line.strip()]
    rc2, head, _ = _git(["rev-parse", "--short", "HEAD"])
    return {"head": head if rc2 == 0 else "?",
            "dirty_files": files,
            "skill_dirty": any(f == "SKILL.md" for f in files),
            "others": [f for f in files if f != "SKILL.md"],
            "onboarded": _read_onboarded()}


def update(dry_run: bool = False) -> dict:
    """执行更新；dry_run 时只报告计划（零修改）"""
    st = plan()
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": st,
                "message": "（--dry-run：未做任何修改）"}
    steps = []
    # 1) 丢弃 SKILL.md 标签位本地改动（更新后恢复）
    if st["skill_dirty"]:
        rc, _, err = _git(["checkout", "--", "SKILL.md"])
        if rc != 0:
            raise UpdateError(f"无法丢弃 SKILL.md 本地改动：{err}")
        steps.append("丢弃 SKILL.md 标签位本地改动（更新后恢复）")
    # 2) 其他本地改动 → stash
    stashed = False
    if st["others"]:
        rc, _, err = _git(["stash", "push", "-m", "el-update 自动暂存"])
        if rc != 0:
            raise UpdateError(f"git stash 失败：{err}")
        stashed = True
        steps.append(f"暂存其他本地改动（{len(st['others'])} 个文件）")
    # 3) pull（仅快进，避免合并提交）
    rc, out, err = _git(["pull", "--ff-only"])
    if rc != 0:
        if stashed:                       # 失败也要把用户改动还回去
            _git(["stash", "pop"])
        raise UpdateError(f"git pull 失败（{err or out}）；已尝试恢复本地改动")
    steps.append("git pull --ff-only：" + out.replace("\n", " ")[:120])
    # 4) 恢复引导状态
    if st["onboarded"]:
        try:
            steps.append(_restore_onboarded())
        except Exception as e:
            steps.append(f"⚠️ 引导标签位恢复失败：{str(e)[:80]}（可手动：el.py onboard --complete）")
    else:
        steps.append("更新前未引导完成（跳过标签位恢复）")
    # 5) stash 恢复
    if stashed:
        rc, _, err = _git(["stash", "pop"])
        if rc != 0:
            raise UpdateError(f"代码已更新，但暂存改动恢复失败（保留在 git stash，可手动 pop）：{err}")
        steps.append("恢复暂存的本地改动")
    rc2, head, _ = _git(["rev-parse", "--short", "HEAD"])
    return {"ok": True, "dry_run": False, "steps": steps,
            "head": head if rc2 == 0 else "?", "message": "更新完成"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="everything-learn 更新助手（git pull + 自动恢复引导标签位）")
    ap.add_argument("--dry-run", action="store_true", help="只显示计划，不做任何修改")
    args = ap.parse_args(argv)
    try:
        result = update(dry_run=args.dry_run)
    except UpdateError as e:
        print(f"更新失败：{e}", file=sys.stderr)
        return 2
    if result.get("dry_run"):
        p = result["plan"]
        print("=== 更新计划（--dry-run）===")
        print(f"当前 HEAD：{p['head']}")
        print(f"改动文件：{'、'.join(p['dirty_files']) or '（无）'}")
        print(f"处理方式：SKILL.md 标签位丢弃后恢复；其他改动 stash → pull → pop")
        print(f"引导状态：{'已引导（更新后将恢复标签位 0）' if p['onboarded'] else '未引导（标签位保持远端状态）'}")
    else:
        print("=== 更新完成 ===")
        for s in result["steps"]:
            print(f"· {s}")
        print(f"新 HEAD：{result['head']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
