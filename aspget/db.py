"""PostgreSQL 接続とマイグレーション、書き込み。

DRY_RUN=true のときは接続はするが INSERT/UPDATE を発行しない。
「何を書くつもりだったか」はログに出す。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .config import Settings, jst_now

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


class Database:
    def __init__(self, settings: Settings) -> None:
        settings.require("database_url")
        self._dsn = settings.database_url
        self.dry_run = settings.dry_run

    @contextmanager
    def connect(self, autocommit: bool = False) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self._dsn, row_factory=dict_row, autocommit=autocommit) as conn:
            yield conn

    # ---- Step 0: 疎通とマイグレーション ----------------------------

    def check_connection(self) -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT version()").fetchone()
        return row["version"]

    def existing_tables(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = current_schema() ORDER BY tablename"
            ).fetchall()
        return [r["tablename"] for r in rows]

    def apply_migrations(self) -> list[str]:
        """migrations/*.sql を名前順に適用する。

        各SQLは BEGIN/COMMIT と IF NOT EXISTS を自前で持つ前提。
        DRY_RUN でも適用する（スキーマが無いと以降が何も検証できないため）。
        """
        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not files:
            raise FileNotFoundError(f"マイグレーションがありません: {MIGRATIONS_DIR}")

        applied = []
        # SQL側が BEGIN / COMMIT を持つため、接続側でトランザクションを張らない
        with self.connect(autocommit=True) as conn:
            for path in files:
                logger.info("マイグレーション適用: %s", path.name)
                conn.execute(path.read_text(encoding="utf-8"))
                applied.append(path.name)
        return applied

    # ---- 実行ログ ---------------------------------------------------

    def start_run(self, run_date: date, asp_name: str) -> int | None:
        if self.dry_run:
            logger.info("[DRY_RUN] collection_runs に開始行を書きません (%s)", asp_name)
            return None
        with self.connect() as conn:
            row = conn.execute(
                "INSERT INTO collection_runs (run_date, asp_name, status, started_at) "
                "VALUES (%s, %s, 'failed', %s) RETURNING id",
                (run_date, asp_name, jst_now()),
            ).fetchone()
        return row["id"]

    def finish_run(
        self,
        run_id: int | None,
        status: str,
        records_fetched: int | None = None,
        error_message: str | None = None,
    ) -> None:
        if self.dry_run or run_id is None:
            logger.info("[DRY_RUN] collection_runs 更新なし (status=%s, records=%s)", status, records_fetched)
            return
        with self.connect() as conn:
            conn.execute(
                "UPDATE collection_runs SET status = %s, records_fetched = %s, "
                "error_message = %s, finished_at = %s WHERE id = %s",
                (status, records_fetched, error_message, jst_now(), run_id),
            )

    def has_successful_run_today(self, run_date: date, asp_name: str) -> bool:
        """禁止事項2（1日2回以上の収集実行）の判定。"""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM collection_runs WHERE run_date = %s AND asp_name = %s "
                "AND status IN ('success', 'partial') LIMIT 1",
                (run_date, asp_name),
            ).fetchone()
        return row is not None

    # ---- 成果データ -------------------------------------------------

    def upsert_conversions(self, rows: Sequence[dict]) -> int:
        if not rows:
            return 0
        if self.dry_run:
            logger.info("[DRY_RUN] conversions に %d 件を書き込みません", len(rows))
            self._log_sample(rows)
            return 0

        sql = """
            INSERT INTO conversions (
                asp_name, asp_conversion_id, asp_program_id, occurred_at,
                status, reward_amount, site_identifier, custom_param, payload
            ) VALUES (
                %(asp_name)s, %(asp_conversion_id)s, %(asp_program_id)s, %(occurred_at)s,
                %(status)s, %(reward_amount)s, %(site_identifier)s, %(custom_param)s, %(payload)s
            )
            ON CONFLICT (asp_name, asp_conversion_id) DO UPDATE SET
                asp_program_id  = EXCLUDED.asp_program_id,
                occurred_at     = EXCLUDED.occurred_at,
                status          = EXCLUDED.status,
                reward_amount   = EXCLUDED.reward_amount,
                site_identifier = EXCLUDED.site_identifier,
                custom_param    = EXCLUDED.custom_param,
                payload         = EXCLUDED.payload
        """
        params = [{**r, "payload": Json(r.get("payload"))} for r in rows]
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, params)
        return len(rows)

    @staticmethod
    def _log_sample(rows: Iterable[dict], limit: int = 3) -> None:
        for i, row in enumerate(rows):
            if i >= limit:
                break
            logger.info(
                "[DRY_RUN] 例: conversion_id=%s program_id=%s occurred_at=%s status=%s reward=%s",
                row.get("asp_conversion_id"),
                row.get("asp_program_id"),
                row.get("occurred_at"),
                row.get("status"),
                row.get("reward_amount"),
            )
