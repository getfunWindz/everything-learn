"""首次引导门：SKILL.md 双标签位读写 + config.onboarded 状态机（2026-10-17）

- SKILL.md 两处标记：
  <!-- ⛳ ONBOARDING: 0|1 -->（顶部，状态值）
  <!-- ⛔ READ-GATE: ONBOARDING: 0|1 -->（引导章节前，阅读门提醒）
- 1 = 引导未完成（首次部署/重置后）；0 = 已完成（跳过文末「初次调用逻辑」）。
- `complete()` 在用户完成五项确认后调用：校验 out_dir → 写 config.onboarded=true + 标记置 0。
"""
import os
import re

from core import config as core_config

# 技能目录（scripts/ 的上一级）下的 SKILL.md；测试可 monkeypatch
SKILL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SKILL.md")

_FLAG1 = re.compile(r"<!-- ⛳ ONBOARDING: [01] -->")
_FLAG2 = re.compile(r"<!-- ⛔ READ-GATE: ONBOARDING: [01] -->")
_FLAG1_READ = re.compile(r"<!-- ⛳ ONBOARDING: ([01]) -->")


class OnboardError(Exception):
    """引导状态操作失败（可读）"""


def set_flags(value: int) -> bool:
    """同步改写 SKILL.md 两处标签位（原子写）；文件/标记缺失 → False（不抛）"""
    value = 0 if int(value) == 0 else 1
    try:
        with open(SKILL_PATH, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    new1 = _FLAG1.sub(f"<!-- ⛳ ONBOARDING: {value} -->", text)
    new2 = _FLAG2.sub(f"<!-- ⛔ READ-GATE: ONBOARDING: {value} -->", new1)
    if new1 == text and new2 == new1:
        return False                     # 无标记 / 已是目标值
    _atomic_write(SKILL_PATH, new2)
    return True


def _atomic_write(path: str, text: str) -> None:
    """原子写（Windows 兼容：PermissionError 短暂重试，见 core.fsutil）"""
    from core import fsutil
    fsutil.atomic_write_text(path, text)


def _flag_from_skill():
    """从 SKILL.md 解析标签位；文件/标记缺失 → None"""
    try:
        with open(SKILL_PATH, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    m = _FLAG1_READ.search(text)
    return int(m.group(1)) if m else None


def read_status() -> dict:
    """当前引导状态：{onboarded, flag, out_dir, whisper_model}"""
    cfg = core_config.load_config()
    return {"onboarded": cfg.get("onboarded") is True,
            "flag": _flag_from_skill(),
            "out_dir": cfg.get("out_dir") or "",
            "whisper_model": cfg.get("whisper_model") or ""}


def complete() -> dict:
    """完成引导：校验 out_dir → config.onboarded=true + 标签位置 0"""
    cfg = core_config.load_config()
    if not (cfg.get("out_dir") or "").strip():
        raise OnboardError(
            "未配置产出目录：请先与用户确认报告根目录（建议新建「学习笔记」总文件夹），"
            "写入 config.out_dir 后再完成引导。")
    core_config.write_user_config({"onboarded": True})
    written = set_flags(0)
    return {"onboarded": True, "flag_written": written}


def reset() -> dict:
    """重开引导：config.onboarded=false + 标签位置 1"""
    core_config.write_user_config({"onboarded": False})
    written = set_flags(1)
    return {"onboarded": False, "flag_written": written}
