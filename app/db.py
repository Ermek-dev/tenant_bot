import aiosqlite
import os
import asyncio
import secrets
from datetime import datetime, timezone
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

CREATE TABLE IF NOT EXISTS issue_assignees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    user_name TEXT,
    is_lead INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(issue_id) REFERENCES issues(id) ON DELETE CASCADE,
    UNIQUE(issue_id, user_id)
);
"""


async def init_db(path: str) -> aiosqlite.Connection:
    """Initialize database connection and run migrations.
    
    Args:
        path: Path to SQLite database file.
        
    Returns:
        Active database connection.
    """
    global _conn
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _conn = await aiosqlite.connect(path)
    await _conn.executescript(CREATE_SQL)
    await _conn.commit()
    await _migrate_add_company_id_column()
    await _migrate_add_deadline_column()
    await _migrate_create_issue_assignees_table()
    await _migrate_add_rating_columns()
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
    """Create a new issue in the database.
    
    Returns:
        ID of the created issue.
    """
    conn = _require_conn()


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

    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO issue_photos(issue_id,file_id,is_completion,uploader_user_id,created_at) VALUES(?,?,?,?,?)",
        (issue_id, file_id, 1 if is_completion else 0, uploader_user_id, now),
    )
    await conn.commit()


async def set_staff_message(issue_id: int, staff_chat_id: int, staff_message_id: int) -> None:
    conn = _require_conn()

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
    if cur.rowcount > 0:
        # Also register as lead assignee in issue_assignees
        await add_issue_assignee(issue_id, assignee_user_id, assignee_name, is_lead=True)
        return True
    return False


async def complete_issue(issue_id: int) -> None:
    conn = _require_conn()

    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "UPDATE issues SET status='closed', updated_at=? WHERE id=?",
        (now, issue_id),
    )
    await conn.commit()


async def reassign_issue(issue_id: int) -> bool:
    """Reset an issue back to 'open' status: clear assignee and remove all assignees.

    Returns True if the issue was successfully reset.
    """
    conn = _require_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = await conn.execute(
        """
        UPDATE issues
        SET status='open', assignee_user_id=NULL, assignee_name=NULL, deadline=NULL, updated_at=?
        WHERE id=? AND status='assigned'
        """,
        (now, issue_id),
    )
    if cur.rowcount > 0:
        # Remove all assignees
        await conn.execute("DELETE FROM issue_assignees WHERE issue_id=?", (issue_id,))
        await conn.commit()
        return True
    await conn.commit()
    return False


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
        "SELECT id, category, status, assignee_name, created_at FROM issues WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ) as cur:
        rows = await cur.fetchall()
        # Some type stubs declare fetchall returns Iterable[Row]; coerce to list
        return list(rows)


async def all_pending_issues(limit: int = 5, offset: int = 0) -> List[aiosqlite.Row]:
    """Get all pending (open or assigned) issues for admin/staff view with pagination.
    
    Args:
        limit: Maximum number of issues to return per page.
        offset: Number of issues to skip (for pagination).
        
    Returns:
        List of issues with open or assigned status, ordered by newest first.
    """
    conn = _require_conn()
    conn.row_factory = aiosqlite.Row
    async with conn.execute(
        """SELECT id, category, status, assignee_name, user_name, created_at 
           FROM issues 
           WHERE status IN ('open', 'assigned') 
           ORDER BY id DESC 
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ) as cur:
        rows = await cur.fetchall()
        return list(rows)


async def count_pending_issues() -> int:
    """Count all pending (open or assigned) issues.
    
    Returns:
        Total count of pending issues.
    """
    conn = _require_conn()
    async with conn.execute(
        "SELECT COUNT(*) FROM issues WHERE status IN ('open', 'assigned')"
    ) as cur:
        row = await cur.fetchone()
        return int(row[0]) if row else 0


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


async def _migrate_create_issue_assignees_table() -> None:
    conn = _require_conn()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS issue_assignees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            is_lead INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(issue_id) REFERENCES issues(id) ON DELETE CASCADE,
            UNIQUE(issue_id, user_id)
        )
    """)
    await conn.commit()


# --- Issue assignees helpers ---

async def add_issue_assignee(issue_id: int, user_id: int, user_name: str, is_lead: bool = False) -> bool:
    """Add an assignee to an issue. Returns True if added, False if already exists."""
    conn = _require_conn()
    now = datetime.now(timezone.utc).isoformat()
    try:
        await conn.execute(
            "INSERT INTO issue_assignees(issue_id, user_id, user_name, is_lead, created_at) VALUES(?,?,?,?,?)",
            (issue_id, user_id, user_name, 1 if is_lead else 0, now),
        )
        await conn.commit()
        return True
    except Exception:
        # UNIQUE constraint — already assigned
        return False


async def get_issue_assignees(issue_id: int) -> List[aiosqlite.Row]:
    """Get all assignees for an issue, lead first."""
    conn = _require_conn()
    conn.row_factory = aiosqlite.Row
    async with conn.execute(
        "SELECT * FROM issue_assignees WHERE issue_id=? ORDER BY is_lead DESC, id ASC",
        (issue_id,),
    ) as cur:
        return list(await cur.fetchall())


async def is_issue_assignee(issue_id: int, user_id: int) -> bool:
    """Check if a user is an assignee of an issue."""
    conn = _require_conn()
    async with conn.execute(
        "SELECT 1 FROM issue_assignees WHERE issue_id=? AND user_id=?",
        (issue_id, user_id),
    ) as cur:
        return (await cur.fetchone()) is not None


async def _migrate_add_rating_columns() -> None:
    conn = _require_conn()
    try:
        await conn.execute("ALTER TABLE issues ADD COLUMN rating INTEGER")
        await conn.execute("ALTER TABLE issues ADD COLUMN rated_by_user_id INTEGER")
        await conn.execute("ALTER TABLE issues ADD COLUMN rated_by_name TEXT")
        await conn.commit()
    except Exception:
        pass  # Columns already exist


# --- Rating helpers ---

async def rate_issue(issue_id: int, rating: int, user_id: int, user_name: str) -> bool:
    """Save or update a rating for a completed issue. Returns True on success."""
    conn = _require_conn()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "UPDATE issues SET rating=?, rated_by_user_id=?, rated_by_name=?, updated_at=? WHERE id=? AND status='closed'",
        (rating, user_id, user_name, now, issue_id),
    )
    await conn.commit()
    return True


async def get_staff_stats(year: int, month: int) -> List[Dict[str, Any]]:
    """Get monthly statistics per staff member: issue count, avg rating, category breakdown.
    
    Returns list of dicts: {user_id, user_name, total, avg_rating, categories: {cat: count}}
    """
    conn = _require_conn()
    conn.row_factory = aiosqlite.Row
    # Date range for the month
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    
    # Get all closed issues in the date range with their assignees
    async with conn.execute("""
        SELECT ia.user_id, ia.user_name, i.id as issue_id, i.category, i.rating
        FROM issue_assignees ia
        JOIN issues i ON ia.issue_id = i.id
        WHERE i.status = 'closed'
          AND i.updated_at >= ? AND i.updated_at < ?
        ORDER BY ia.user_id
    """, (start, end)) as cur:
        rows = list(await cur.fetchall())
    
    # Aggregate per user
    from collections import defaultdict
    users: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        uid = r["user_id"]
        if uid not in users:
            users[uid] = {
                "user_id": uid,
                "user_name": r["user_name"],
                "total": 0,
                "ratings": [],
                "categories": defaultdict(int),
            }
        users[uid]["total"] += 1
        if r["rating"] is not None:
            users[uid]["ratings"].append(r["rating"])
        cat = r["category"] or "Без категории"
        users[uid]["categories"][cat] += 1
    
    result = []
    for u in users.values():
        avg = round(sum(u["ratings"]) / len(u["ratings"]), 1) if u["ratings"] else None
        result.append({
            "user_id": u["user_id"],
            "user_name": u["user_name"],
            "total": u["total"],
            "avg_rating": avg,
            "categories": dict(u["categories"]),
        })
    
    # Sort by total desc
    result.sort(key=lambda x: x["total"], reverse=True)
    return result
