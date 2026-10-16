"""文件写入工具：Windows 兼容的原子写（2026-10-17）

Windows 下 `os.replace` 偶发 PermissionError（目标文件被瞬时锁定：杀毒/索引/快速连续替换）
→ 短暂重试；临时文件 + 替换保证不产生半写文件。
"""
import os
import time


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
