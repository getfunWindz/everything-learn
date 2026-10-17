"""文件写入工具：Windows 兼容的原子写（2026-10-17）

Windows 下 `os.replace` 偶发 PermissionError（目标文件被瞬时锁定：杀毒/索引/快速连续替换）
→ 短暂重试；临时文件 + 替换保证不产生半写文件。
"""
import os
import re
import time
import unicodedata


def atomic_write_text(path: str, text: str, retries: int = 4) -> None:
    """原子写文本：临时文件 + os.replace；PermissionError 短暂重试后仍失败才抛出"""
    tmp = path + ".tmp"
    for attempt in range(retries):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def safe_title(title: str, maxlen: int = 60) -> str:
    """文件名安全化（严格）：只保留文字/数字/空格，其余符号（含中英文标点、emoji）转空格。

    - 连字符/下划线/斜杠先转空格（保留可读性：V4.1-Flash → V4 1 Flash）
    - 仅保留 Unicode alnum + 空格；折叠空白；空 → 未命名
    """
    t = unicodedata.normalize("NFKC", str(title or ""))
    t = re.sub(r"[-_/|]+", " ", t)
    t = "".join(ch if ch.isalnum() else " " for ch in t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:maxlen].strip() or "未命名"
