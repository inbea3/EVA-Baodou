"""EVA 持久化：设置 DATABASE_URL 时使用 PostgreSQL（Neon），否则回退本地文件。"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

DATABASE_URL = _raw_url = os.environ.get("DATABASE_URL", "")


def _normalize_database_url(url: str) -> str:
    """Neon 连接串里的 channel_binding=require 在 Railway 上常导致连接失败。"""
    if not url:
        return url
    url = re.sub(r"([?&])channel_binding=[^&]*&?", r"\1", url)
    url = url.replace("?&", "?").rstrip("?&")
    return url


DATABASE_URL = _normalize_database_url(_raw_url)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eva_knowledge (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS eva_hints (
    project_dir TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS eva_sessions (
    project_dir TEXT PRIMARY KEY,
    messages JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS eva_locks (
    project_dir TEXT PRIMARY KEY,
    pid INTEGER NOT NULL,
    locked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wechat_accounts (
    account_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    credentials JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def enabled() -> bool:
    return bool(DATABASE_URL)


def _connect():
    import psycopg

    try:
        return psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=15)
    except Exception as e:
        raise RuntimeError(
            f"无法连接 Neon 数据库，请检查 DATABASE_URL 是否正确、Neon 项目是否在线。\n详情：{e}"
        ) from e


def init_schema() -> None:
    if not enabled():
        return
    with _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS wechat_credentials")
        conn.execute(_SCHEMA)


def _mirror(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pid_alive(pid: int, is_windows: bool) -> bool:
    if pid <= 0:
        return False
    try:
        if is_windows:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
            )
            return str(pid) in result.stdout
        return os.path.exists(f"/proc/{pid}")
    except Exception:
        return False


def load_knowledge(eva_file: Path, seed_example: Path | None = None) -> str:
    if not enabled():
        return eva_file.read_text(encoding="utf-8") if eva_file.exists() else ""

    with _connect() as conn:
        row = conn.execute(
            "SELECT content FROM eva_knowledge WHERE id = %s", ("default",)
        ).fetchone()
        if row and row[0]:
            _mirror(eva_file, row[0])
            return row[0]

        seed = eva_file.read_text(encoding="utf-8") if eva_file.exists() else ""
        if not seed and seed_example and seed_example.exists():
            seed = seed_example.read_text(encoding="utf-8")

        if seed:
            conn.execute(
                """
                INSERT INTO eva_knowledge (id, content)
                VALUES ('default', %s)
                ON CONFLICT (id) DO UPDATE
                SET content = EXCLUDED.content, updated_at = NOW()
                """,
                (seed,),
            )
            _mirror(eva_file, seed)
        return seed


def load_hints(project_dir: str, hint_file: Path) -> str:
    if not enabled():
        return hint_file.read_text(encoding="utf-8") if hint_file.exists() else ""

    with _connect() as conn:
        row = conn.execute(
            "SELECT content FROM eva_hints WHERE project_dir = %s", (project_dir,)
        ).fetchone()
        content = row[0] if row else ""
        if content:
            _mirror(hint_file, content)
        return content


def save_hints(project_dir: str, content: str, hint_file: Path) -> None:
    _mirror(hint_file, content)
    if not enabled():
        return
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO eva_hints (project_dir, content)
            VALUES (%s, %s)
            ON CONFLICT (project_dir) DO UPDATE
            SET content = EXCLUDED.content, updated_at = NOW()
            """,
            (project_dir, content),
        )


def save_session(project_dir: str, messages: list, session_file: Path) -> str:
    payload = json.dumps(messages, ensure_ascii=False, indent=2)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(payload, encoding="utf-8")

    if not enabled():
        return str(session_file)

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO eva_sessions (project_dir, messages)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (project_dir) DO UPDATE
            SET messages = EXCLUDED.messages, updated_at = NOW()
            """,
            (project_dir, payload),
        )
    return f"数据库 eva_sessions ({project_dir})"


def load_session(project_dir: str, session_file: Path) -> tuple[list | None, int]:
    """返回 (messages, size_kb)。"""
    if enabled():
        with _connect() as conn:
            row = conn.execute(
                "SELECT messages FROM eva_sessions WHERE project_dir = %s",
                (project_dir,),
            ).fetchone()
            if not row:
                return None, 0
            messages = row[0]
            if isinstance(messages, str):
                messages = json.loads(messages)
            payload = json.dumps(messages, ensure_ascii=False)
            session_file.parent.mkdir(parents=True, exist_ok=True)
            session_file.write_text(
                json.dumps(messages, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            size_kb = (len(payload.encode("utf-8")) + 999) // 1000
            return messages, size_kb

    if not session_file.exists():
        return None, 0
    messages = json.loads(session_file.read_text(encoding="utf-8"))
    size_kb = (session_file.stat().st_size + 999) // 1000
    return messages, size_kb


def list_sessions(project_dir: str, session_dir: Path, current_name: str) -> None:
    if enabled():
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT project_dir, messages, updated_at
                FROM eva_sessions
                ORDER BY project_dir
                """
            ).fetchall()
        print("存储: Neon PostgreSQL (eva_sessions)\n")
        if not rows:
            print("> 没有找到任何会话记录。")
            return
        print(f"> 共找到 {len(rows)} 个会话:")
        print("-" * 60)
        for i, (key, messages, updated_at) in enumerate(rows, start=1):
            payload = messages if isinstance(messages, str) else json.dumps(messages)
            size_kb = (len(payload.encode("utf-8")) + 999) // 1000
            marker = "    <=== 当前目录" if key == project_dir else ""
            print(f"  {i}. {key} ({format(size_kb, ',')} KB, {updated_at}){marker}")
        print("-" * 60)
        return

    print(f"目录: {session_dir}\n")
    if not session_dir.exists():
        print("> 没有找到任何会话记录。")
        return
    files = [f for f in os.listdir(session_dir) if f.endswith(".json")]
    if not files:
        print("> 没有找到任何会话记录。")
        return
    print(f"> 共找到 {len(files)} 个会话:")
    print("-" * 60)
    for i, name in enumerate(sorted(files), start=1):
        path = session_dir / name
        size_kb = (path.stat().st_size + 999) // 1000
        marker = "    <=== 当前目录" if name == current_name else ""
        print(f"  {i}. {name} ({format(size_kb, ',')} KB){marker}")
    print("-" * 60)


def clear_session(project_dir: str, session_file: Path) -> bool:
    existed = session_file.exists()
    if session_file.exists():
        session_file.unlink()

    if enabled():
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM eva_sessions WHERE project_dir = %s RETURNING project_dir",
                (project_dir,),
            )
            existed = existed or cur.fetchone() is not None
    return existed


def try_acquire_lock(
    project_dir: str, pid: int, lock_file: Path, is_windows: bool
) -> tuple[bool, str]:
    if not enabled():
        if lock_file.exists():
            try:
                old_pid = int(lock_file.read_text(encoding="utf-8").strip())
                if _pid_alive(old_pid, is_windows) and old_pid != pid:
                    return False, (
                        f"错误：该目录已有 EVA 实例正在运行（PID: {old_pid}），不允许重复启动。\n"
                        f"如需强制启动，请先删除锁文件：{lock_file}"
                    )
            except Exception:
                pass
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(str(pid), encoding="utf-8")
        return True, ""

    with _connect() as conn:
        row = conn.execute(
            "SELECT pid FROM eva_locks WHERE project_dir = %s", (project_dir,)
        ).fetchone()
        if row:
            old_pid = row[0]
            if _pid_alive(old_pid, is_windows) and old_pid != pid:
                return False, (
                    f"错误：该目录已有 EVA 实例正在运行（PID: {old_pid}），不允许重复启动。"
                )
        conn.execute(
            """
            INSERT INTO eva_locks (project_dir, pid, locked_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (project_dir) DO UPDATE
            SET pid = EXCLUDED.pid, locked_at = NOW()
            """,
            (project_dir, pid),
        )
    return True, ""


def release_lock(project_dir: str, lock_file: Path) -> None:
    if lock_file.exists():
        try:
            lock_file.unlink()
        except Exception:
            pass
    if not enabled():
        return
    with _connect() as conn:
        conn.execute("DELETE FROM eva_locks WHERE project_dir = %s", (project_dir,))


def _wechat_accounts_dir() -> Path:
    base = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.environ.get("EVA_DATA_DIR")
    if base:
        return Path(base) / "wechatbot" / "accounts"
    return Path(__file__).resolve().parent / "data" / "wechatbot" / "accounts"


def _safe_account_filename(account_id: str) -> str:
    return re.sub(r"[^\w.-@]", "_", account_id)


def list_wechat_accounts() -> list[dict]:
    if enabled():
        with _connect() as conn:
            rows = conn.execute(
                "SELECT account_id, user_id, credentials FROM wechat_accounts ORDER BY updated_at"
            ).fetchall()
        accounts = []
        for account_id, user_id, credentials in rows:
            creds = credentials if isinstance(credentials, dict) else json.loads(credentials)
            accounts.append(
                {"account_id": account_id, "user_id": user_id, "credentials": creds}
            )
        return accounts

    accounts_dir = _wechat_accounts_dir()
    if not accounts_dir.exists():
        return []
    out = []
    for path in sorted(accounts_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            creds = json.loads(path.read_text(encoding="utf-8"))
            account_id = creds.get("accountId") or creds.get("account_id") or path.stem
            user_id = creds.get("userId") or creds.get("user_id") or account_id
            out.append(
                {"account_id": account_id, "user_id": user_id, "credentials": creds}
            )
        except Exception:
            continue
    return out


def save_wechat_account(account_id: str, user_id: str, credentials: dict) -> None:
    cred_dir = _wechat_accounts_dir()
    cred_dir.mkdir(parents=True, exist_ok=True)
    cred_file = cred_dir / f"{_safe_account_filename(account_id)}.json"
    cred_file.write_text(json.dumps(credentials, ensure_ascii=False, indent=2), encoding="utf-8")

    if not enabled():
        return
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO wechat_accounts (account_id, user_id, credentials)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (account_id) DO UPDATE
            SET user_id = EXCLUDED.user_id,
                credentials = EXCLUDED.credentials,
                updated_at = NOW()
            """,
            (account_id, user_id, json.dumps(credentials, ensure_ascii=False)),
        )


def wechat_cred_path(account_id: str) -> Path:
    return _wechat_accounts_dir() / f"{_safe_account_filename(account_id)}.json"


def migrate_legacy_wechat_cred(legacy_path: Path) -> bool:
    """将旧版单文件 credentials.json 迁移为多账号存储。"""
    if not legacy_path.exists() or list_wechat_accounts():
        return False
    try:
        creds = json.loads(legacy_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    account_id = creds.get("accountId") or creds.get("account_id")
    user_id = creds.get("userId") or creds.get("user_id")
    if not account_id or not user_id:
        return False
    save_wechat_account(account_id, user_id, creds)
    dest = wechat_cred_path(account_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if legacy_path.resolve() != dest.resolve():
        dest.write_text(json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"> 已迁移旧版微信凭证 → 账号 {account_id}", flush=True)
    return True


def sync_wechat_cred_files() -> None:
    """从数据库记录恢复本地凭证文件（容器重启后 ephemeral 磁盘可能丢失文件）。"""
    for acc in list_wechat_accounts():
        path = wechat_cred_path(acc["account_id"])
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(acc["credentials"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"> 已从数据库恢复凭证文件: {acc['account_id']}", flush=True)
