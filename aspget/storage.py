"""生データの保存と読み出し（CLAUDE.md 最重要原則5）。

パースにバグが見つかっても、ASPへ再アクセスせずに再処理できるように
レスポンス原本を storage/raw/{asp}/{YYYY-MM-DD}/ に置く。
DRY_RUN でも保存する（保存しないと再処理の材料が残らないため）。
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from .config import jst_today

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "storage" / "raw"
STATE_DIR = ROOT / "storage" / "state"


def raw_dir(asp_name: str, run_date: date | None = None) -> Path:
    run_date = run_date or jst_today()
    return RAW_DIR / asp_name / run_date.isoformat()


def save_raw_json(asp_name: str, filename: str, payload: object, run_date: date | None = None) -> Path:
    directory = raw_dir(asp_name, run_date)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("生データを保存しました: %s", path.relative_to(ROOT))
    return path


def load_raw_json(asp_name: str, run_date: date) -> list[tuple[Path, object]]:
    """保存済みの生データを日付単位で読み出す（再処理用）。"""
    directory = raw_dir(asp_name, run_date)
    if not directory.is_dir():
        raise FileNotFoundError(f"生データが見つかりません: {directory}")

    items = []
    for path in sorted(directory.glob("*.json")):
        items.append((path, json.loads(path.read_text(encoding="utf-8"))))
    if not items:
        raise FileNotFoundError(f"生データが見つかりません: {directory}")
    return items


# ---- 実行間隔ガード -------------------------------------------------
# 「1日2回以上の収集実行」は禁止事項2。DBの collection_runs でも判定できるが、
# DRY_RUN 中はDBに書かないため、開発中の反復実行でASPを叩き続けてしまう。
# ローカルの状態ファイルで DRY_RUN でも歯止めをかける。

def _state_path(asp_name: str) -> Path:
    return STATE_DIR / f"last_fetch_{asp_name}.json"


def last_fetch_date(asp_name: str) -> date | None:
    path = _state_path(asp_name)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("last_fetch_date")
        return date.fromisoformat(value) if value else None
    except (ValueError, OSError):
        logger.warning("状態ファイルを読めませんでした: %s", path)
        return None


def record_fetch(asp_name: str, run_date: date | None = None) -> None:
    run_date = run_date or jst_today()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(asp_name).write_text(
        json.dumps({"last_fetch_date": run_date.isoformat()}), encoding="utf-8"
    )
