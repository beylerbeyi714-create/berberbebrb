"""PostgreSQL bağlantı havuzu ve küçük yardımcılar."""
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=10, open=False,
                      kwargs={"row_factory": dict_row, "autocommit": True})


@contextmanager
def tx():
    """Tek bir işlem (transaction) içinde çalışan bağlantı."""
    with pool.connection() as conn:
        with conn.transaction():
            yield conn


def one(sql: str, params=None):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()


def all_(sql: str, params=None):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def exec_(sql: str, params=None):
    with pool.connection() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount


def get_setting(key: str, default=None):
    row = one("SELECT value FROM settings WHERE key=%s", (key,))
    return row["value"] if row else default


def all_settings() -> dict:
    return {r["key"]: r["value"] for r in all_("SELECT key, value FROM settings ORDER BY key")}


def set_setting(key: str, value) -> None:
    exec_("INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, now()) "
          "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (key, Jsonb(value)))


def log_error(source: str, message: str, video_id=None, detail: dict | None = None) -> None:
    exec_("INSERT INTO error_logs (source, video_id, message, detail) VALUES (%s,%s,%s,%s)",
          (source, video_id, message[:4000], Jsonb(detail or {})))


def log_run(workflow: str, status: str, video_id=None, execution_id=None, detail=None) -> None:
    exec_("INSERT INTO workflow_runs (workflow, execution_id, video_id, status, detail) "
          "VALUES (%s,%s,%s,%s,%s)", (workflow, execution_id, video_id, status, Jsonb(detail or {})))
