"""ログ設定。認証情報・トークンは必ず伏せる（禁止事項9）。

伏せ方は2段構え。
  1. .env から読んだ実値を literal で置換する
  2. 実値を知らないトークン（APIから受け取った bearer_token 等）を
     パターンで潰す
"""
from __future__ import annotations

import logging
import re
import sys

MASK = "***REDACTED***"

# 実値を知らないトークン類。Bearer に続く文字列、JSONの bearer_token など。
_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-+/=]{8,}", re.IGNORECASE),
    re.compile(r'("(?:bearer_token|access_token|password|secret)"\s*:\s*")[^"]+'),
    re.compile(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+"),
]


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        # 長い順に置換しないと、部分一致で取りこぼす
        self._secrets = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        for pattern in _PATTERNS:
            text = pattern.sub(rf"\1{MASK}", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub(str(v)) for k, v in record.args.items()}
            else:
                record.args = tuple(self._scrub(str(a)) for a in record.args)
        return True


def setup_logging(secrets: list[str] | None = None, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    handler.addFilter(RedactingFilter(secrets or []))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # httpx は URL を INFO で吐く。クエリに認証情報が乗る可能性があるため落とす。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
