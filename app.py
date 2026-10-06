"""小剧场排练 · 剧本换版（Flask + SQLite + 原生 JavaScript）

核心规则：
- 台词编号 INTEGER PRIMARY KEY AUTOINCREMENT，删除后永不复用；顺序由 position 维护。
- 笔记以台词编号为键，重排/改词不动笔记；删除或改派仍带笔记的台词会被整次拒绝。
- 工作稿有单调递增的修订号；台词与笔记的写操作都必须带所见修订号，过期整笔拒绝。
- 发布把当前全部台词与笔记快照到 published_* 表，冻结在发布时的修订号，旧版永久可查。
"""
import os
import sqlite3
from datetime import datetime, timezone

from flask import (
    Flask, g, request, jsonify, render_template, abort,
)

DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "theater.db"),
)

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS meta (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    working_revision INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS lines (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,  -- 永不复用的台词编号
    position  INTEGER NOT NULL,                   -- 1..n 的密集顺序号
    character TEXT NOT NULL,
    text      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lines_position ON lines(position);
CREATE INDEX IF NOT EXISTS idx_lines_character ON lines(character);

CREATE TABLE IF NOT EXISTS notes (
    line_id    INTEGER PRIMARY KEY REFERENCES lines(id) ON DELETE RESTRICT,
    content    TEXT NOT NULL,                     -- 笔记只跟随台词编号
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publications (
    revision     INTEGER PRIMARY KEY,             -- 冻结时的工作稿修订号
    label        TEXT,
    published_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS published_lines (
    revision  INTEGER NOT NULL REFERENCES publications(revision),
    line_id   INTEGER NOT NULL,
    position  INTEGER NOT NULL,
    character TEXT NOT NULL,
    text      TEXT NOT NULL,
    PRIMARY KEY (revision, line_id)
);

CREATE TABLE IF NOT EXISTS published_notes (
    revision INTEGER NOT NULL REFERENCES publications(revision),
    line_id  INTEGER NOT NULL,
    content  TEXT NOT NULL,
    PRIMARY KEY (revision, line_id)
);
"""

# 首次初始化时的示例工作稿（编号即 1..9，之后永不复用）
SEED_LINES = [
    ("林夏", "你来啦。我还以为今晚要一个人对词了。"),
    ("周野", "路上耽搁了。第三幕那场，你想好怎么处理了吗？"),
    ("林夏", "哪一场？门口分手那场？"),
    ("周野", "对。你转身走，我留在台上。导演说这里要再慢一点。"),
    ("林夏", "慢下来容易空。我给自己留了个记号，说到“走”字以后数三拍。"),
    ("陈海", "（上场）二位，灯光走位表更新了，开场前都看一眼。"),
    ("周野", "收到。对了，那句“再慢一点”到底是你说还是我说？"),
    ("林夏", "你说。我接的是沉默，然后转身。"),
    ("陈海", "好，那就从我进门开始，从头到尾走一遍。"),
]
SEED_NOTE_LINE_ID = 5
SEED_NOTE = "数三拍时看向窗外的灯，第三拍灯灭，正好接转身。"

MAX_CHAR_LEN = 100
MAX_TEXT_LEN = 5000
MAX_NOTE_LEN = 5000
MAX_LABEL_LEN = 100

app = Flask(__name__)


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


@app.errorhandler(ApiError)
def handle_api_error(err):
    return jsonify(error=err.message, code=err.code, details=err.details), err.status


# ---------------------------------------------------------------- 数据库连接

def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    """建表并在空库时写入示例工作稿。幂等，容器每次启动都会执行。"""
    db = get_db()
    db.executescript(SCHEMA)
    if db.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("INSERT INTO meta(id, working_revision) VALUES (1, 1)")
            for idx, (character, text) in enumerate(SEED_LINES, start=1):
                db.execute(
                    "INSERT INTO lines(id, position, character, text) VALUES (?, ?, ?, ?)",
                    (idx, idx, character, text),
                )
            db.execute(
                "INSERT INTO notes(line_id, content, updated_at) VALUES (?, ?, ?)",
                (SEED_NOTE_LINE_ID, SEED_NOTE, now_iso()),
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise


@app.cli.command("init-db")
def init_db_command():
    """容器启动入口：初始化/校验数据库。"""
    with app.app_context():
        init_db()
    print(f"database ready at {DB_PATH}")


# ---------------------------------------------------------------- 组装与校验

def get_revision(db):
    return db.execute("SELECT working_revision FROM meta WHERE id = 1").fetchone()[0]


def build_payload(revision, line_rows, note_rows):
    """把台词行装成 JSON，并按当前顺序计算每句的“紧前一句”提示。"""
    notes = {r["line_id"]: r["content"] for r in note_rows}
    lines = []
    previous = None
    for r in line_rows:
        lines.append({
            "id": r["id"],
            "position": r["position"],
            "character": r["character"],
            "text": r["text"],
            "note": notes.get(r["id"], ""),
            "cue": previous,  # 提示完全由顺序推导，重排后自然重算
        })
        previous = {"id": r["id"], "character": r["character"], "text": r["text"]}
    characters = []
    for line in lines:
        if line["character"] not in characters:
            characters.append(line["character"])
    return {"revision": revision, "lines": lines, "characters": characters}


def draft_payload(db):
    rows = db.execute(
        "SELECT id, position, character, text FROM lines ORDER BY position, id"
    ).fetchall()
    note_rows = db.execute("SELECT line_id, content FROM notes").fetchall()
    payload = build_payload(get_revision(db), rows, note_rows)
    payload["frozen"] = False
    payload["publication"] = None
    return payload


def published_payload(db, revision, character=None):
    pub = db.execute(
        "SELECT revision, label, published_at FROM publications WHERE revision = ?",
        (revision,),
    ).fetchone()
    if pub is None:
        raise ApiError(404, "not_found", f"没有找到第 {revision} 版发布稿")
    rows = db.execute(
        "SELECT line_id AS id, position, character, text FROM published_lines "
        "WHERE revision = ? ORDER BY position, line_id",
        (revision,),
    ).fetchall()
    note_rows = db.execute(
        "SELECT line_id, content FROM published_notes WHERE revision = ?",
        (revision,),
    ).fetchall()
    payload = build_payload(revision, rows, note_rows)
    payload["frozen"] = True
    payload["publication"] = {
        "revision": pub["revision"],
        "label": pub["label"],
        "published_at": pub["published_at"],
    }
    if character is not None:
        payload["lines"] = [l for l in payload["lines"] if l["character"] == character]
        payload["character"] = character
    return payload


def body_json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "bad_json", "请求体必须是 JSON 对象")
    return data


def require_revision(data):
    rev = data.get("base_revision")
    if not isinstance(rev, int) or isinstance(rev, bool):
        raise ApiError(
            400, "bad_revision",
            "缺少 base_revision：请提交你看到的工作稿修订号",
        )
    return rev


def clean_str(value, field, max_len):
    if not isinstance(value, str):
        raise ApiError(400, "bad_field", f"“{field}”必须是文本")
    value = value.strip()
    if not value:
        raise ApiError(400, "bad_field", f"“{field}”不能为空")
    if len(value) > max_len:
        raise ApiError(400, "bad_field", f"“{field}”长度不能超过 {max_len} 字")
    return value


def begin_edit(data):
    """开启一笔带乐观锁的写事务；修订号过期则整次拒绝（不留下任何改动）。"""
    base_rev = require_revision(data)
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    current_rev = get_revision(db)
    if base_rev != current_rev:
        db.execute("ROLLBACK")
        raise ApiError(
            409, "stale_revision",
            f"修订号已过期：你看到的是第 {base_rev} 版，工作稿现在是第 {current_rev} 版。"
            "本次修改已整次拒绝，请刷新后在最新稿上重试。",
            details={"submitted": base_rev, "current": current_rev},
        )
    return db, current_rev


def finish_edit(db):
    """提交工作稿改动并把修订号 +1。"""
    db.execute("UPDATE meta SET working_revision = working_revision + 1 WHERE id = 1")
    new_rev = get_revision(db)
    db.execute("COMMIT")
    return new_rev


def rollback(db):
    db.execute("ROLLBACK")


def ordered_ids(db):
    return [r[0] for r in db.execute("SELECT id FROM lines ORDER BY position, id")]


def reposition(db, ids):
    """按给定编号顺序重写 position。"""
    db.executemany(
        "UPDATE lines SET position = ? WHERE id = ?",
        [(idx, line_id) for idx, line_id in enumerate(ids, start=1)],
    )


def blocking_notes(db, line_ids):
    """找出这些台词上仍存在的笔记——删除/改派前必须先由演员清空。"""
    if not line_ids:
        return []
    marks = ",".join("?" * len(line_ids))
    rows = db.execute(
        f"""
        SELECT n.line_id AS line_id, l.character AS character, n.content AS content
        FROM notes n JOIN lines l ON l.id = n.line_id
        WHERE n.line_id IN ({marks})
        ORDER BY n.line_id
        """,
        list(line_ids),
    ).fetchall()
    return [{
        "line_id": r["line_id"],
        "character": r["character"],
        "content": r["content"][:80],
    } for r in rows]


# ---------------------------------------------------------------- HTML 页面

@app.get("/")
def page_index():
    db = get_db()
    draft = draft_payload(db)
    stats = {}
    for line in draft["lines"]:
        st = stats.setdefault(line["character"], {"lines": 0, "notes": 0})
        st["lines"] += 1
        if line["note"]:
            st["notes"] += 1
    publications = db.execute(
        "SELECT revision, label, published_at FROM publications "
        "ORDER BY revision DESC"
    ).fetchall()
    return render_template(
        "index.html", draft=draft, stats=stats, publications=publications,
    )


@app.get("/editor")
def page_editor():
    return render_template("editor.html")


@app.get("/character/<name>")
def page_character(name):
    db = get_db()
    if db.execute("SELECT 1 FROM lines WHERE character = ? LIMIT 1", (name,)).fetchone() is None:
        abort(404)
    return render_template(
        "character.html", character=name, frozen=False, revision=None,
        data_url=f"/api/character/{name}",
    )


@app.get("/r/<int:revision>/")
def page_published_index(revision):
    db = get_db()
    payload = published_payload(db, revision)
    counts = {}
    for line in payload["lines"]:
        counts[line["character"]] = counts.get(line["character"], 0) + 1
    return render_template(
        "published_index.html", publication=payload["publication"],
        characters=payload["characters"], counts=counts,
    )


@app.get("/r/<int:revision>/character/<name>")
def page_published_character(revision, name):
    db = get_db()
    payload = published_payload(db, revision, character=name)
    if not payload["lines"]:
        abort(404)
    return render_template(
        "character.html", character=name, frozen=True, revision=revision,
        data_url=f"/api/revisions/{revision}/character/{name}",
    )


# ---------------------------------------------------------------- JSON API

@app.get("/api/draft")
def api_draft():
    return jsonify(draft_payload(get_db()))


@app.get("/api/character/<name>")
def api_character(name):
    db = get_db()
    payload = draft_payload(db)
    mine = [l for l in payload["lines"] if l["character"] == name]
    if not mine:
        raise ApiError(404, "not_found", f"工作稿中没有角色“{name}”的台词")
    payload["lines"] = mine
    payload["character"] = name
    return jsonify(payload)


@app.get("/api/revisions/<int:revision>/character/<name>")
def api_published_character(revision, name):
    payload = published_payload(get_db(), revision, character=name)
    if not payload["lines"]:
        raise ApiError(404, "not_found", f"第 {revision} 版中没有角色“{name}”的台词")
    return jsonify(payload)


@app.get("/api/publications")
def api_publications():
    rows = get_db().execute(
        """
        SELECT p.revision AS revision, p.label AS label, p.published_at AS published_at,
               (SELECT COUNT(*) FROM published_lines pl WHERE pl.revision = p.revision) AS line_count,
               (SELECT COUNT(*) FROM published_notes pn WHERE pn.revision = p.revision) AS note_count
        FROM publications p ORDER BY p.revision DESC
        """
    ).fetchall()
    return jsonify({"publications": [dict(r) for r in rows]})


@app.post("/api/lines")
def api_add_line():
    data = body_json()
    character = clean_str(data.get("character"), "角色", MAX_CHAR_LEN)
    text = clean_str(data.get("text"), "台词正文", MAX_TEXT_LEN)
    after_id = data.get("after_id")
    db, _rev = begin_edit(data)
    ids = ordered_ids(db)
    if after_id is not None:
        if not isinstance(after_id, int) or isinstance(after_id, bool) or after_id not in ids:
            rollback(db)
            raise ApiError(400, "bad_field", "after_id 必须是当前已有的台词编号")
    cursor = db.execute(
        "INSERT INTO lines(position, character, text) VALUES (0, ?, ?)",
        (character, text),
    )
    new_id = cursor.lastrowid  # AUTOINCREMENT：编号永不复用
    if after_id is None:
        ids.append(new_id)
    else:
        ids.insert(ids.index(after_id) + 1, new_id)
    reposition(db, ids)
    new_rev = finish_edit(db)
    return jsonify(ok=True, revision=new_rev, line_id=new_id)


@app.patch("/api/lines/<int:line_id>")
def api_edit_line(line_id):
    data = body_json()
    character = clean_str(data.get("character"), "角色", MAX_CHAR_LEN)
    text = clean_str(data.get("text"), "台词正文", MAX_TEXT_LEN)
    db, rev = begin_edit(data)
    row = db.execute(
        "SELECT id, character, text FROM lines WHERE id = ?", (line_id,)
    ).fetchone()
    if row is None:
        rollback(db)
        raise ApiError(404, "not_found", f"台词 #{line_id} 不存在")
    if character != row["character"]:
        blocked = blocking_notes(db, [line_id])
        if blocked:
            rollback(db)
            raise ApiError(
                409, "notes_blocking",
                f"改派被整次拒绝：台词 #{line_id} 上的笔记属于原角色“{row['character']}”，"
                f"不能让它落到角色“{character}”。请先到“{row['character']}”的角色页清空笔记。",
                details=blocked,
            )
    if character == row["character"] and text == row["text"]:
        rollback(db)
        return jsonify(ok=True, revision=rev, changed=False)
    db.execute(
        "UPDATE lines SET character = ?, text = ? WHERE id = ?",
        (character, text, line_id),
    )
    new_rev = finish_edit(db)
    return jsonify(ok=True, revision=new_rev, changed=True)


@app.delete("/api/lines/<int:line_id>")
def api_delete_line(line_id):
    data = body_json()
    db, _rev = begin_edit(data)
    row = db.execute(
        "SELECT id, character FROM lines WHERE id = ?", (line_id,)
    ).fetchone()
    if row is None:
        rollback(db)
        raise ApiError(404, "not_found", f"台词 #{line_id} 不存在")
    blocked = blocking_notes(db, [line_id])
    if blocked:
        rollback(db)
        raise ApiError(
            409, "notes_blocking",
            f"删除被整次拒绝：台词 #{line_id}（{row['character']}）上还有笔记。"
            "请先到该角色页把笔记清空，再回来删除。",
            details=blocked,
        )
    db.execute("DELETE FROM lines WHERE id = ?", (line_id,))
    ids = [x for x in ordered_ids(db) if x != line_id]
    reposition(db, ids)
    new_rev = finish_edit(db)
    return jsonify(ok=True, revision=new_rev)


@app.post("/api/lines/reorder")
def api_reorder():
    data = body_json()
    order = data.get("order")
    if (not isinstance(order, list)
            or any(not isinstance(x, int) or isinstance(x, bool) for x in order)):
        raise ApiError(400, "bad_field", "order 必须是台词编号的数组")
    db, rev = begin_edit(data)
    current = ordered_ids(db)
    if len(order) != len(set(order)) or set(order) != set(current):
        rollback(db)
        raise ApiError(
            400, "bad_field",
            "重排名单必须恰好包含当前全部台词编号，不能重复、遗漏或新增",
        )
    if order == current:
        rollback(db)
        return jsonify(ok=True, revision=rev, changed=False)
    reposition(db, order)
    new_rev = finish_edit(db)
    return jsonify(ok=True, revision=new_rev, changed=True)


@app.put("/api/lines/<int:line_id>/note")
def api_put_note(line_id):
    data = body_json()
    raw = data.get("content", "")
    if not isinstance(raw, str):
        raise ApiError(400, "bad_field", "笔记内容必须是文本")
    content = raw.strip()
    if len(content) > MAX_NOTE_LEN:
        raise ApiError(400, "bad_field", f"笔记长度不能超过 {MAX_NOTE_LEN} 字")
    db, rev = begin_edit(data)
    row = db.execute("SELECT id FROM lines WHERE id = ?", (line_id,)).fetchone()
    if row is None:
        rollback(db)
        raise ApiError(404, "not_found", f"台词 #{line_id} 不存在")
    existing = db.execute(
        "SELECT content FROM notes WHERE line_id = ?", (line_id,)
    ).fetchone()
    if content == "":
        db.execute("DELETE FROM notes WHERE line_id = ?", (line_id,))
        changed = existing is not None
    elif existing is not None and existing["content"] == content:
        changed = False
    else:
        db.execute(
            """
            INSERT INTO notes(line_id, content, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(line_id) DO UPDATE SET content = excluded.content,
                                              updated_at = excluded.updated_at
            """,
            (line_id, content, now_iso()),
        )
        changed = True
    if not changed:
        rollback(db)
        return jsonify(ok=True, revision=rev, changed=False)
    new_rev = finish_edit(db)
    return jsonify(ok=True, revision=new_rev, changed=True)


@app.post("/api/publish")
def api_publish():
    data = body_json()
    label = data.get("label")
    if label is not None:
        label = clean_str(label, "版本备注", MAX_LABEL_LEN)
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    rev = get_revision(db)
    try:
        if db.execute(
            "SELECT 1 FROM publications WHERE revision = ?", (rev,)
        ).fetchone() is not None:
            raise ApiError(
                409, "already_published",
                f"第 {rev} 版已经发布过；工作稿没有新改动，无需重复发布。",
            )
        db.execute(
            "INSERT INTO publications(revision, label, published_at) VALUES (?, ?, ?)",
            (rev, label, now_iso()),
        )
        db.execute(
            "INSERT INTO published_lines(revision, line_id, position, character, text) "
            "SELECT ?, id, position, character, text FROM lines ORDER BY position, id",
            (rev,),
        )
        db.execute(
            "INSERT INTO published_notes(revision, line_id, content) "
            "SELECT ?, line_id, content FROM notes",
            (rev,),
        )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return jsonify(ok=True, revision=rev, label=label)


@app.errorhandler(404)
def handle_404(_err):
    if request.path.startswith("/api/"):
        return jsonify(error="资源不存在", code="not_found"), 404
    return render_template("404.html"), 404


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
