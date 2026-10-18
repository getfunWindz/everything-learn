"""长内容分块：把 content.txt 切成适合多轮写报告的 part 文件 + manifest。"""
import json
import os
import re

_TS = re.compile(r"^\[([\d.]+)-([\d.]+)\]")


def split_text(text: str, chunk_chars: int = 20000, overlap_chars: int = 1000):
    """按字符切块，尽量在行边界；块间保留 overlap_chars 的上下文重叠。"""
    chunk_chars = max(2000, int(chunk_chars))
    overlap_chars = max(0, min(int(overlap_chars), chunk_chars // 2))
    lines = (text or "").splitlines(keepends=True)
    parts, cur, cur_len = [], [], 0
    for line in lines:
        cur.append(line)
        cur_len += len(line)
        if cur_len >= chunk_chars:
            parts.append("".join(cur))
            if overlap_chars:
                tail, n = [], 0
                for l in reversed(cur):
                    tail.insert(0, l)
                    n += len(l)
                    if n >= overlap_chars:
                        break
                cur = tail
                cur_len = sum(len(l) for l in cur)
            else:
                cur, cur_len = [], 0
    if cur:
        parts.append("".join(cur))
    return parts


def _range_of(chunk: str):
    times = [(_TS.match(l)) for l in chunk.splitlines()]
    rs = [(float(m.group(1)), float(m.group(2))) for m in times if m]
    return (rs[0][0], rs[-1][1]) if rs else (None, None)


def split_dir(out_dir: str, chunk_chars: int = 20000, overlap_chars: int = 1000) -> dict:
    src = os.path.join(out_dir, "content.txt")
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    text = open(src, encoding="utf-8").read()
    parts = split_text(text, chunk_chars, overlap_chars)
    items = []
    for i, chunk in enumerate(parts, 1):
        name = f"content.part{i:02d}.md"
        start, end = _range_of(chunk)
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(f"<!-- part {i}/{len(parts)} | {start}–{end}s | {len(chunk)} chars -->\n")
            f.write(chunk)
        items.append({"file": name, "chars": len(chunk), "start": start, "end": end})
    manifest = {"source": os.path.abspath(src), "chunk_chars": chunk_chars,
                "overlap_chars": overlap_chars, "parts": items}
    with open(os.path.join(out_dir, "split_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest
