"""アダプタ共通の型とリトライ規則。

CLAUDE.md「ブラウザ動作ルール（変更禁止）」の待機・リトライ値を
ここに集約する。アダプタ側で別の値を使わないこと。
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

# 変更禁止（CLAUDE.md ブラウザ動作ルール）
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (30, 120, 300)
PAGE_WAIT_RANGE = (2.0, 5.0)

T = TypeVar("T")


class AdapterError(RuntimeError):
    """収集の継続を諦めるべきエラー。"""


class NonRetryableError(AdapterError):
    """リトライしてはいけないエラー（認証失敗・レート制限・4xx全般）。

    ここでリトライを重ねるとアカウントロックを招く（禁止事項5）。
    """


@dataclass
class CollectionResult:
    asp_name: str
    rows: list[dict] = field(default_factory=list)
    raw_paths: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.rows)


def polite_wait() -> None:
    """画面遷移・ページング間の待機。"""
    time.sleep(random.uniform(*PAGE_WAIT_RANGE))


def with_retry(operation: Callable[[], T], description: str) -> T:
    """指数バックオフ付きリトライ。3回失敗したら諦めて投げる。

    NonRetryableError は即座に投げ直す。
    """
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return operation()
        except NonRetryableError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                break
            wait = BACKOFF_SECONDS[attempt - 1]
            logger.warning(
                "%s に失敗しました (%d/%d): %s — %d秒後に再試行します",
                description, attempt, MAX_ATTEMPTS, type(exc).__name__, wait,
            )
            time.sleep(wait)

    raise AdapterError(
        f"{description} が {MAX_ATTEMPTS} 回失敗しました: {type(last_error).__name__}"
    ) from last_error
