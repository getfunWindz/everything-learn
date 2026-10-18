"""内建知识库：把学习报告/正文索引进本地 SQLite，支持关键词检索。

- 默认关闭（config.kb.enabled）；`el.py setup --kb` 或 `el.py kb build` 开启/建库。
- 纯本地、无外部服务；表结构简单，便于任意 harness 读取。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "kb.sqlite3")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE,
  title TEXT,
  author TEXT,
  platform TEXT,
  url TEXT,
  kind TEXT,
  created_at TEXT,
  chars INTEGER,
  content TEXT
);
CREATE INDEX IF NOT EXISTS idx_docs_title ON docs(title);
"""


def _db_path() -> str:
    try:
        from core import config
        p = ((config.load_config().get("kb") or {}).get("path") or "").strip()
        if p:
            return os.path.abspath(os.path.expanduser(p))
    except Exception:
        pass
    return DB_PATH


def _connect(path: str = None):
    path = path or _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def index_dir(out_dir: str, db_path: str = None) -> bool:
    """索引一个产物目录（content_info.json + content.txt + 学习报告.md）"""
    info_path = os.path.join(out_dir, "content_info.json")
    content_path = os.path.join(out_dir, "content.txt")
    if not os.path.exists(info_path):
        return False
    try:
        info = json.load(open(info_path, encoding="utf-8")) or {}
    except Exception:
        info = {}
    content = ""
    if os.path.exists(content_path):
        content = open(content_path, encoding="utf-8", errors="replace").read()
    report = ""
    for name in os.listdir(out_dir):
        if name.endswith("学习报告.md"):
            report = open(os.path.join(out_dir, name), encoding="utf-8", errors="replace").read()
            break
    body = "\n\n".join(x for x in (report, content) if x)
    if not body.strip():
        return False
    title = info.get("title") or os.path.basename(os.path.dirname(out_dir)) or "未命名"
    row = {
        "path": os.path.abspath(out_dir),
        "title": title,
        "author": info.get("author") or "",
        "platform": info.get("platform") or "",
        "url": info.get("url") or "",
        "kind": info.get("kind") or "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "chars": len(body),
        "content": body,
    }
    conn = _connect(db_path)
    try:
        conn.execute("""
            INSERT INTO docs(path,title,author,platform,url,kind,created_at,chars,content)
            VALUES(:path,:title,:author,:platform,:url,:kind,:created_at,:chars,:content)
            ON CONFLICT(path) DO UPDATE SET
              title=excluded.title, author=excluded.author, platform=excluded.platform,
              url=excluded.url, kind=excluded.kind, created_at=excluded.created_at,
              chars=excluded.chars, content=excluded.content
        """, row)
        conn.commit()
        return True
    finally:
        conn.close()


def index_root(root: str, db_path: str = None) -> int:
    """递归索引 root 下所有含 content_info.json 的目录"""
    count = 0
    for dirpath, _dirs, files in os.walk(root):
        if "content_info.json" in files and index_dir(dirpath, db_path):
            count += 1
    return count


def search(query: str, limit: int = 10, db_path: str = None) -> list:
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    conn = _connect(db_path)
    try:
        rows = conn.execute("""
            SELECT title, author, platform, url, path, chars,
                   CASE WHEN title LIKE ? THEN 0 ELSE 1 END AS rank
            FROM docs
            WHERE title LIKE ? OR author LIKE ? OR content LIKE ?
            ORDER BY rank, chars DESC
            LIMIT ?
        """, (like, like, like, like, max(1, int(limit)))).fetchall()
        return [{"title": r[0], "author": r[1], "platform": r[2], "url": r[3],
                 "path": r[4], "chars": r[5]} for r in rows]
    finally:
        conn.close()


def stats(db_path: str = None) -> dict:
    conn = _connect(db_path)
    try:
        n, chars = conn.execute("SELECT COUNT(*), COALESCE(SUM(chars),0) FROM docs").fetchone()
        return {"docs": n, "chars": chars, "db": _db_path() if not db_path else db_path}
    finally:
        conn.close()
