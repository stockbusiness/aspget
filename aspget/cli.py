"""コマンドラインエントリポイント。

  python -m aspget.cli check                    Step 0: DB・LINEの疎通確認
  python -m aspget.cli migrate                  マイグレーション適用
  python -m aspget.cli collect --asp valuecommerce
  python -m aspget.cli reprocess --asp valuecommerce --date 2026-08-17

複数ASPを同時に走らせるオプションは用意しない（禁止事項1）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

from . import storage
from .adapters.valuecommerce import ValueCommerceAdapter, parse_transactions
from .config import ConfigError, jst_now, jst_today, load_settings
from .db import Database
from .logging_setup import setup_logging
from .notifier import LineNotifier

logger = logging.getLogger("aspget")

ADAPTERS = {
    "valuecommerce": ValueCommerceAdapter,
}

PARSERS = {
    "valuecommerce": parse_transactions,
}


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"日付は YYYY-MM-DD 形式で指定してください: {text}")


# ---- check ----------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    settings = load_settings()
    ok = True

    logger.info("DRY_RUN = %s", settings.dry_run)

    try:
        database = Database(settings)
        version = database.check_connection()
        logger.info("DB接続: OK (%s)", version.split(",")[0])
        tables = database.existing_tables()
        expected = {"programs", "program_snapshots", "program_changes", "conversions", "collection_runs"}
        missing = sorted(expected - set(tables))
        if missing:
            logger.warning("未作成のテーブルがあります: %s（migrate を実行してください）", ", ".join(missing))
            ok = False
        else:
            logger.info("テーブル: OK (%d件)", len(expected))
    except Exception as exc:
        logger.error("DB確認に失敗しました: %s: %s", type(exc).__name__, exc)
        ok = False

    try:
        settings.require("vc_api_token_key", "vc_api_secret")
        logger.info("バリューコマース認証情報: 設定済み")
    except ConfigError as exc:
        logger.warning("%s", exc)
        ok = False

    notifier = LineNotifier(settings)
    sent = notifier.push(f"[aspget] 疎通確認 {jst_now():%Y-%m-%d %H:%M} JST")
    if settings.dry_run:
        logger.info("LINE通知: DRY_RUN のため送信していません")
    elif not sent:
        ok = False

    logger.info("疎通確認: %s", "OK" if ok else "要対応")
    return 0 if ok else 1


# ---- migrate --------------------------------------------------------

def cmd_migrate(args: argparse.Namespace) -> int:
    settings = load_settings()
    database = Database(settings)
    applied = database.apply_migrations()
    logger.info("適用しました: %s", ", ".join(applied))
    return 0


# ---- collect --------------------------------------------------------

def cmd_collect(args: argparse.Namespace) -> int:
    settings = load_settings()
    asp_name = args.asp
    run_date = jst_today()

    # 禁止事項2: 1日2回以上の収集実行。DRY_RUN でもASPへは実際に接続するため、
    # DBではなくローカルの状態ファイルで先に止める。
    if storage.last_fetch_date(asp_name) == run_date:
        if not args.ignore_daily_guard:
            logger.error(
                "%s には本日（%s JST）すでにアクセスしています。"
                "保存済みの生データで再処理してください:\n"
                "  python -m aspget.cli reprocess --asp %s --date %s",
                asp_name, run_date, asp_name, run_date,
            )
            return 1
        logger.warning(
            "--ignore-daily-guard により、本日2回目以降のアクセスを行います (%s)", asp_name
        )

    database = Database(settings)
    if not settings.dry_run and database.has_successful_run_today(run_date, asp_name):
        logger.error("%s は本日すでに収集済みです（collection_runs）。中止します。", asp_name)
        return 1

    notifier = LineNotifier(settings)
    run_id = database.start_run(run_date, asp_name)
    started = jst_now()

    # 最重要原則4: 1社の失敗が全体を止めない。ASPが増えてもこの形を保つ。
    try:
        adapter = ADAPTERS[asp_name](settings)
        storage.record_fetch(asp_name, run_date)   # 接続前に記録する
        result = adapter.collect(
            run_date=run_date,
            from_date=args.from_date,
            to_date=args.to_date,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        logger.exception("%s の収集に失敗しました", asp_name)
        database.finish_run(run_id, "failed", None, message)
        notifier.push(
            f"[aspget] {asp_name} の収集に失敗しました\n"
            f"{started:%Y-%m-%d %H:%M} JST\n{message}"
        )
        return 1

    written = database.upsert_conversions(result.rows)
    database.finish_run(run_id, "success", result.count, None)

    logger.info(
        "%s: %d件を取得、%d件をDBへ書き込み、生データ %d ファイル",
        asp_name, result.count, written, len(result.raw_paths),
    )
    notifier.push(
        f"[aspget] {asp_name} 収集完了\n"
        f"{started:%Y-%m-%d %H:%M} JST\n"
        f"取得 {result.count} 件 / 書き込み {written} 件"
    )
    return 0


# ---- reprocess ------------------------------------------------------

def cmd_reprocess(args: argparse.Namespace) -> int:
    """保存済みの生データから再処理する。ASPへはアクセスしない。"""
    settings = load_settings()
    asp_name = args.asp

    items = storage.load_raw_json(asp_name, args.date)
    parser = PARSERS[asp_name]

    rows: list[dict] = []
    for path, payload in items:
        parsed = parser(payload)
        logger.info("%s: %d件", path.name, len(parsed))
        rows.extend(parsed)

    written = Database(settings).upsert_conversions(rows)
    logger.info("%s: 合計 %d件を再処理、%d件をDBへ書き込み", asp_name, len(rows), written)
    return 0


# ---- main -----------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aspget")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="DB・LINEの疎通確認").set_defaults(func=cmd_check)
    sub.add_parser("migrate", help="マイグレーション適用").set_defaults(func=cmd_migrate)

    collect = sub.add_parser("collect", help="ASPから収集（1社ずつ）")
    collect.add_argument("--asp", required=True, choices=sorted(ADAPTERS))
    collect.add_argument("--from-date", type=_parse_date, dest="from_date", default=None)
    collect.add_argument("--to-date", type=_parse_date, dest="to_date", default=None)
    collect.add_argument(
        "--ignore-daily-guard",
        action="store_true",
        help="本日アクセス済みでも実行する。ASPへの追加アクセスになるため、"
             "障害対応など理由があるときだけ使うこと",
    )
    collect.set_defaults(func=cmd_collect)

    reprocess = sub.add_parser("reprocess", help="保存済み生データから再処理（ASPへアクセスしない）")
    reprocess.add_argument("--asp", required=True, choices=sorted(PARSERS))
    reprocess.add_argument("--date", type=_parse_date, required=True)
    reprocess.set_defaults(func=cmd_reprocess)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(settings.secret_values())
    try:
        return args.func(args)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
