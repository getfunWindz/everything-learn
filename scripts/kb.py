"""内建知识库 v2：索引/去重/清理 + FTS5 检索（SQLite 本地）。

- 默认关闭（config.kb.enabled）；`el.py setup --kb` 或 `el.py kb build` 开启/建库。
- 去重键：platform:item_id:sub_id（缺失回退 path）。
- 合集汇总（kind=batch）默认跳过，只索引 P 条目。
- `kb build` 默认 prune 已删除/不再含 content_info.json 的记录。
- FTS5 优先 trigram（中文子串）；查询 <3 字符或 FTS 不可用时回退 LIKE。
"""
import json
import os
import re
import sqlite3
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "kb.sqlite3")
SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  id INTEGER PRIMARY KEY,
  doc_key TEXT UNIQUE,
  path TEXT,
  title TEXT,
  author TEXT,
  platform TEXT,
  item_id TEXT,
  sub_id TEXT,
  url TEXT,
  kind TEXT,
  published TEXT,
  created_at TEXT,
  mtime REAL,
  chars INTEGER,
  content TEXT
);
CREATE INDEX IF NOT EXISTS idx_docs_title ON docs(title);
CREATE INDEX IF NOT EXISTS idx_docs_platform ON docs(platform);
CREATE INDEX IF NOT EXISTS idx_docs_published ON docs(published);
CREATE INDEX IF NOT EXISTS idx_docs_path ON docs(path);
"""

_FTS_TOKENIZER = None


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
    _ensure_schema(conn)
    try:
        from core.fsutil import restrict_file
        restrict_file(path)
    except Exception:
        pass
    return conn


def _fts_exists(conn) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs_fts'"
    ).fetchone() is not None


def _ensure_fts(conn) -> None:
    global _FTS_TOKENIZER
    if _fts_exists(conn):
        return
    for tok in ("trigram", "unicode61"):
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE docs_fts USING fts5("
                "doc_key UNINDEXED, title, author, content, "
                f"tokenize='{tok}')")
            conn.commit()
            _FTS_TOKENIZER = tok
            return
        except sqlite3.OperationalError:
            continue
    _FTS_TOKENIZER = None


def _ensure_schema(conn) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0] or 0
    if version != SCHEMA_VERSION:
        # 知识库是派生索引：旧 schema 直接重建，源报告/正文不受影响。
        conn.executescript("DROP TABLE IF EXISTS docs_fts; DROP TABLE IF EXISTS docs;")
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    else:
        conn.executescript(_SCHEMA)
    _ensure_fts(conn)


def _published(info: dict) -> str:
    raw = info.get("published")
    s = str(raw).strip() if raw is not None else ""
    if not s:
        return ""
    if re.fullmatch(r"\d{10,}", s):
        try:
            return datetime.fromtimestamp(int(s)).date().isoformat()
        except (OSError, ValueError, OverflowError):
            return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else ""


def _doc_key(info: dict, out_dir: str) -> str:
    if str(info.get("kind") or "") == "batch":
        # 合集汇总与 P 条目使用独立键，避免 include_collections 时互相覆盖。
        return "batch:" + os.path.normcase(os.path.abspath(out_dir))
    platform = str(info.get("platform") or "").strip()
    item_id = str(info.get("item_id") or "").strip()
    sub_id = str(info.get("sub_id") or "").strip()
    if platform and item_id:
        return f"{platform}:{item_id}:{sub_id or '1'}"
    return "path:" + os.path.normcase(os.path.abspath(out_dir))


def _report_path(out_dir: str) -> str:
    hits = sorted(f for f in os.listdir(out_dir) if f.endswith("学习报告.md"))
    return os.path.join(out_dir, hits[0]) if hits else ""


def _body_and_mtime(out_dir: str):
    content_path = os.path.join(out_dir, "content.txt")
    report_path = _report_path(out_dir)
    content = ""
    if os.path.exists(content_path):
        with open(content_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    report = ""
    if report_path:
        with open(report_path, encoding="utf-8", errors="replace") as f:
            report = f.read()
    body = "\n\n".join(x for x in (report, content) if x)
    mtimes = [os.path.getmtime(p) for p in (content_path, report_path) if p and os.path.exists(p)]
    return body, (max(mtimes) if mtimes else time.time())


def _fts_delete(conn, keys) -> None:
    if not _fts_exists(conn):
        return
    for key in keys:
        conn.execute("DELETE FROM docs_fts WHERE doc_key=?", (key,))


def _fts_insert(conn, row: dict) -> None:
    if not _fts_exists(conn):
        return
    conn.execute(
        "INSERT INTO docs_fts(doc_key,title,author,content) VALUES(?,?,?,?)",
        (row["doc_key"], row["title"], row["author"], row["content"]))


def index_dir(out_dir: str, db_path: str = None, include_collections: bool = False) -> str:
    """索引一个产物目录；返回 doc_key（跳过/无内容返回 None）。"""
    info_path = os.path.join(out_dir, "content_info.json")
    if not os.path.exists(info_path):
        return None
    try:
        with open(info_path, encoding="utf-8") as f:
            info = json.load(f) or {}
    except Exception:
        info = {}
    kind = str(info.get("kind") or "")
    if kind == "batch" and not include_collections:
        return None
    body, mtime = _body_and_mtime(out_dir)
    if not body.strip():
        return None
    title = info.get("title") or os.path.basename(os.path.dirname(out_dir)) or "未命名"
    key = _doc_key(info, out_dir)
    row = {
        "doc_key": key,
        "path": os.path.abspath(out_dir),
        "title": title,
        "author": info.get("author") or "",
        "platform": info.get("platform") or "",
        "item_id": str(info.get("item_id") or ""),
        "sub_id": str(info.get("sub_id") or ""),
        "url": info.get("url") or "",
        "kind": kind,
        "published": _published(info),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mtime": float(mtime),
        "chars": len(body),
        "content": body,
    }
    conn = _connect(db_path)
    try:
        existing = conn.execute(
            "SELECT path FROM docs WHERE doc_key=?", (key,)).fetchone()
        if existing and not os.path.exists(existing[0]):
            # 原路径已删除：允许较小副本接管，避免指向失效路径。
            conn.execute("DELETE FROM docs WHERE doc_key=?", (key,))
            _fts_delete(conn, [key])
        cur = conn.execute("""
            INSERT INTO docs(doc_key,path,title,author,platform,item_id,sub_id,url,
                             kind,published,created_at,mtime,chars,content)
            VALUES(:doc_key,:path,:title,:author,:platform,:item_id,:sub_id,:url,
                   :kind,:published,:created_at,:mtime,:chars,:content)
            ON CONFLICT(doc_key) DO UPDATE SET
              path=excluded.path, title=excluded.title, author=excluded.author,
              platform=excluded.platform, item_id=excluded.item_id,
              sub_id=excluded.sub_id, url=excluded.url, kind=excluded.kind,
              published=excluded.published, created_at=excluded.created_at,
              mtime=excluded.mtime, chars=excluded.chars, content=excluded.content
            WHERE excluded.chars >= docs.chars
        """, row)
        if cur.rowcount > 0:
            _fts_delete(conn, [key])
            _fts_insert(conn, row)
        conn.commit()
        return key
    finally:
        conn.close()


def prune_stale(root: str, db_path: str = None, seen=None) -> int:
    """清理 root 下不再存在/不在 seen 中的记录；返回删除条数。"""
    conn = _connect(db_path)
    try:
        root_abs = os.path.abspath(root)
        root_norm = os.path.normcase(root_abs)
        prefix = root_norm + os.sep
        rows = conn.execute("SELECT doc_key,path FROM docs").fetchall()
        stale = []
        for key, path in rows:
            if not path:
                continue
            path_norm = os.path.normcase(os.path.abspath(path))
            if not (path_norm == root_norm or path_norm.startswith(prefix)):
                continue
            if seen is not None:
                if key in seen:
                    continue
            elif os.path.exists(path) and os.path.exists(
                    os.path.join(path, "content_info.json")):
                continue
            stale.append(key)
        if stale:
            conn.executemany("DELETE FROM docs WHERE doc_key=?", [(k,) for k in stale])
            _fts_delete(conn, stale)
            conn.commit()
        return len(stale)
    finally:
        conn.close()


def index_root(root: str, db_path: str = None, prune: bool = True,
               include_collections: bool = False) -> dict:
    """递归索引 root；返回 {scanned, pruned, docs, chars, db}。"""
    root = os.path.abspath(root)
    seen = set()
    scanned = 0
    for dirpath, _dirs, files in os.walk(root):
        if "content_info.json" in files:
            key = index_dir(dirpath, db_path, include_collections=include_collections)
            if key:
                scanned += 1
                seen.add(key)
    pruned = prune_stale(root, db_path, seen=seen) if prune else 0
    st = stats(db_path)
    return {"scanned": scanned, "pruned": pruned, "docs": st["docs"],
            "chars": st["chars"], "db": st["db"]}


def rebuild(root: str, db_path: str = None, include_collections: bool = False) -> dict:
    """清空知识库并重建（索引是派生数据）。"""
    conn = _connect(db_path)
    try:
        conn.executescript("DROP TABLE IF EXISTS docs_fts; DROP TABLE IF EXISTS docs;")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()
    return index_root(root, db_path, include_collections=include_collections)


def delete_doc(path: str, db_path: str = None) -> int:
    """按产物目录删除记录；返回删除条数。"""
    target = os.path.normcase(os.path.abspath(path))
    conn = _connect(db_path)
    try:
        keys = [k for k, p in conn.execute("SELECT doc_key,path FROM docs").fetchall()
                if p and os.path.normcase(os.path.abspath(p)) == target]
        if keys:
            conn.executemany("DELETE FROM docs WHERE doc_key=?", [(k,) for k in keys])
            _fts_delete(conn, keys)
            conn.commit()
        return len(keys)
    finally:
        conn.close()


def _escape_like(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet(content: str, query: str, width: int = 36) -> str:
    c = content or ""
    q = query or ""
    idx = c.lower().find(q.lower()) if q else -1
    if idx < 0:
        return re.sub(r"\s+", " ", c[:width * 2]).strip()
    start = max(0, idx - width)
    end = min(len(c), idx + len(q) + width)
    s = re.sub(r"\s+", " ", c[start:end]).strip()
    return ("…" if start > 0 else "") + s + ("…" if end < len(c) else "")


def _filters(prefix: str, platform=None, author=None, kind=None, since=None):
    clauses, params = [], []
    if platform:
        clauses.append(f"lower({prefix}platform)=lower(?)")
        params.append(platform)
    if author:
        clauses.append(f"{prefix}author LIKE ? ESCAPE '\\'")
        params.append("%" + _escape_like(author) + "%")
    if kind:
        clauses.append(f"{prefix}kind=?")
        params.append(kind)
    if since:
        clauses.append(f"{prefix}published >= ?")
        params.append(since)
    return (" AND " + " AND ".join(clauses) if clauses else ""), params


def _fts_query(query: str) -> str:
    return '"' + (query or "").replace('"', '""') + '"'


def search(query: str, limit: int = 10, db_path: str = None,
           platform: str = None, author: str = None, kind: str = None,
           since: str = None) -> list:
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, int(limit))
    conn = _connect(db_path)
    try:
        flt, fparams = _filters("d.", platform, author, kind, since)
        rows = []
        # FTS5：trigram 需要 >=3 字符；短查询/不可用/无命中 → LIKE 回退
        if _fts_exists(conn) and len(q) >= 3:
            sql = ("SELECT d.title,d.author,d.platform,d.url,d.path,d.chars,d.published,"
                   "d.kind, snippet(docs_fts,3,'「','」','…',24), bm25(docs_fts) "
                   "FROM docs_fts JOIN docs d ON d.doc_key=docs_fts.doc_key "
                   "WHERE docs_fts MATCH ?" + flt +
                   " ORDER BY bm25(docs_fts), d.chars DESC LIMIT ?")
            try:
                rows = conn.execute(sql, [_fts_query(q)] + fparams + [limit]).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if rows:
            return [{"title": r[0], "author": r[1], "platform": r[2], "url": r[3],
                     "path": r[4], "chars": r[5], "published": r[6], "kind": r[7],
                     "snippet": re.sub(r"\s+", " ", r[8] or "").strip(),
                     "score": r[9]} for r in rows]

        like = "%" + _escape_like(q) + "%"
        sql = ("SELECT d.title,d.author,d.platform,d.url,d.path,d.chars,d.published,"
               "d.kind,d.content FROM docs d WHERE "
               "(d.title LIKE ? ESCAPE '\\' OR d.author LIKE ? ESCAPE '\\' "
               "OR d.content LIKE ? ESCAPE '\\')" + flt + " LIMIT 200")
        raw = conn.execute(sql, [like, like, like] + fparams).fetchall()
        ql = q.lower()
        out = []
        for r in raw:
            title, author_name, content = r[0] or "", r[1] or "", r[8] or ""
            in_title = ql in title.lower()
            in_author = ql in author_name.lower()
            count = content.lower().count(ql)
            rank = 0 if in_title else (1 if in_author else 2)
            out.append((rank, -count, -int(r[5] or 0), r, _snippet(content, q)))
        out.sort(key=lambda x: (x[0], x[1], x[2]))
        return [{"title": r[0], "author": r[1], "platform": r[2], "url": r[3],
                 "path": r[4], "chars": r[5], "published": r[6], "kind": r[7],
                 "snippet": snip, "score": rank} for rank, _cnt, _ch, r, snip in out[:limit]]
    finally:
        conn.close()


def stats(db_path: str = None) -> dict:
    conn = _connect(db_path)
    try:
        n, chars = conn.execute("SELECT COUNT(*), COALESCE(SUM(chars),0) FROM docs").fetchone()
        return {"docs": n, "chars": chars, "db": _db_path() if not db_path else db_path,
                "fts": _fts_exists(conn)}
    finally:
        conn.close()
