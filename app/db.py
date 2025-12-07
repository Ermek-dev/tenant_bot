import aiosqlite
import os
import asyncio
from typing import Any, Dict, List, Optional, Tuple


_conn: Optional[aiosqlite.Connection] = None


CREATE_SQL = r"""
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Enterprises
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    invite_code TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_companies (
    user_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
);

-- Issues
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    user_name TEXT,
    category TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL,
    assignee_user_id INTEGER,
    assignee_name TEXT,
    staff_chat_id INTEGER,
    staff_message_id INTEGER,
    tenant_chat_id INTEGER,
    company_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS issue_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    is_completion INTEGER NOT NULL DEFAULT 0,
    uploader_user_id INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(issue_id) REFERENCES issues(id) ON DELETE CASCADE
);
"""


async def init_db(path: str) -> aiosqlite.Connection:
    global _conn
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _conn = await aiosqlite.connect(path)
    await _conn.executescript(CREATE_SQL)
    await _conn.commit()
    await _migrate_add_company_id_column()
    await _migrate_add_deadline_column()
    return _conn


def _require_conn() -> aiosqlite.Connection:
    if _conn is None:
        raise RuntimeError("DB not initialized")
    return _conn


async def set_setting(key: str, value: str) -> None:
    conn = _require_conn()
    await conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    await conn.commit()


async def get_setting(key: str) -> Optional[str]:
    conn = _require_conn()
    async with conn.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
        row = await cur.fetchone()
        return row[0] if row else None


async def create_issue(
    *,
    user_id: int,
    user_name: Optional[str],
    category: str,
    description: str,
    tenant_chat_id: int,
    company_id: int,
) -> int:
    conn = _require_conn()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    cur = await conn.execute(
        """
        INSERT INTO issues(user_id,user_name,category,description,status,tenant_chat_id,company_id,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (user_id, user_name, category, description, "open", tenant_chat_id, company_id, now, now),
    )
    await conn.commit()
    # lastrowid can be Optional in type hints, but after INSERT it is set
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def add_issue_photo(issue_id: int, file_id: str, *, is_completion: bool, uploader_user_id: Optional[int]) -> None:
    conn = _require_conn()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO issue_photos(issue_id,file_id,is_completion,uploader_user_id,created_at) VALUES(?,?,?,?,?)",
        (issue_id, file_id, 1 if is_completion else 0, uploader_user_id, now),
    )
    await conn.commit()


async def set_staff_message(issue_id: int, staff_chat_id: int, staff_message_id: int) -> None:
    conn = _require_conn()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "UPDATE issues SET staff_chat_id=?, staff_message_id=?, updated_at=? WHERE id=?",
        (staff_chat_id, staff_message_id, now, issue_id),
    )
    await conn.commit()


async def get_issue(issue_id: int) -> Optional[aiosqlite.Row]:
    conn = _require_conn()
    conn.row_factory = aiosqlite.Row
    async with conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)) as cur:
        return await cur.fetchone()


async def claim_issue(issue_id: int, assignee_user_id: int, assignee_name: str, deadline: Optional[str] = None) -> bool:
    conn = _require_conn()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    # Only claim if status is open and not assigned
    cur = await conn.execute(
        """
        UPDATE issues
        SET status='assigned', assignee_user_id=?, assignee_name=?, deadline=?, updated_at=?
        WHERE id=? AND (status='open' OR (status='assigned' AND assignee_user_id IS NULL))
        """,
        (assignee_user_id, assignee_name, deadline, now, issue_id),
    )
    await conn.commit()
    return cur.rowcount > 0


async def complete_issue(issue_id: int) -> None:
    conn = _require_conn()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "UPDATE issues SET status='closed', updated_at=? WHERE id=?",
        (now, issue_id),
    )
    await conn.commit()


async def get_issue_photos(issue_id: int, *, is_completion: Optional[bool] = None) -> List[str]:
    conn = _require_conn()
    query = "SELECT file_id FROM issue_photos WHERE issue_id=?"
    params: Tuple[Any, ...] = (issue_id,)
    if is_completion is not None:
        query += " AND is_completion=?"
        params += (1 if is_completion else 0,)
    async with conn.execute(query, params) as cur:
        rows = await cur.fetchall()
    return [r[0] for r in rows]


# Companies helpers
async def create_company(name: str, invite_code: Optional[str]) -> int:
    conn = _require_conn()
    from datetime import datetime, timezone
    import secrets

    code = invite_code or secrets.token_hex(3).upper()
    now = datetime.now(timezone.utc).isoformat()
    cur = await conn.execute(
        "INSERT INTO companies(name, invite_code, created_at) VALUES(?,?,?)",
        (name, code, now),
    )
    await conn.commit()
    # lastrowid can be Optional in type hints, but after INSERT it is set
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


async def list_companies() -> List[aiosqlite.Row]:
    conn = _require_conn()
    conn.row_factory = aiosqlite.Row
    async with conn.execute("SELECT * FROM companies ORDER BY id ASC") as cur:
        rows = await cur.fetchall()
        # Some type stubs declare fetchall returns Iterable[Row]; coerce to list
        return list(rows)


async def get_company_by_invite(code: str) -> Optional[aiosqlite.Row]:
    conn = _require_conn()
    conn.row_factory = aiosqlite.Row
    async with conn.execute("SELECT * FROM companies WHERE invite_code=?", (code,)) as cur:
        return await cur.fetchone()


async def get_company(company_id: int) -> Optional[aiosqlite.Row]:
    conn = _require_conn()
    conn.row_factory = aiosqlite.Row
    async with conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)) as cur:
        return await cur.fetchone()


async def get_user_company(user_id: int) -> Optional[aiosqlite.Row]:
    conn = _require_conn()
    conn.row_factory = aiosqlite.Row
    async with conn.execute(
        "SELECT c.* FROM user_companies uc JOIN companies c ON c.id=uc.company_id WHERE uc.user_id=?",
        (user_id,),
    ) as cur:
        return await cur.fetchone()


async def set_user_company(user_id: int, company_id: int) -> None:
    conn = _require_conn()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO user_companies(user_id,company_id,created_at) VALUES(?,?,?)\n         ON CONFLICT(user_id) DO UPDATE SET company_id=excluded.company_id",
        (user_id, company_id, now),
    )
    await conn.commit()


async def company_member_count(company_id: int) -> int:
    conn = _require_conn()
    async with conn.execute("SELECT COUNT(*) FROM user_companies WHERE company_id=?", (company_id,)) as cur:
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def user_issues(user_id: int, limit: int = 5) -> List[aiosqlite.Row]:
    conn = _require_conn()
    conn.row_factory = aiosqlite.Row
    async with conn.execute(
        "SELECT id, category, status, created_at FROM issues WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ) as cur:
        rows = await cur.fetchall()
        # Some type stubs declare fetchall returns Iterable[Row]; coerce to list
        return list(rows)


async def _migrate_add_company_id_column() -> None:
    conn = _require_conn()
    # Check if company_id exists in issues
    async with conn.execute("PRAGMA table_info(issues)") as cur:
        cols = [row[1] for row in await cur.fetchall()]
    if "company_id" not in cols:
        await conn.execute("ALTER TABLE issues ADD COLUMN company_id INTEGER")
        await conn.commit()


async def _migrate_add_deadline_column() -> None:
    conn = _require_conn()
    # Check if deadline exists in issues
    async with conn.execute("PRAGMA table_info(issues)") as cur:
        cols = [row[1] for row in await cur.fetchall()]
    if "deadline" not in cols:
        await conn.execute("ALTER TABLE issues ADD COLUMN deadline TEXT")
        await conn.commit()
