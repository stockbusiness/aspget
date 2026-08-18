"""環境変数の読み込みと実行時設定。

DRY_RUN は「未設定なら true」。設定漏れで本番書き込み・LINE通知が
走るほうが事故なので、安全側に倒している。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

JST = ZoneInfo("Asia/Tokyo")

load_dotenv()


def jst_now() -> datetime:
    return datetime.now(JST)


def jst_today() -> date:
    """収集日の判定は必ずJSTで行う（CLAUDE.md 技術スタック）。"""
    return jst_now().date()


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    vc_api_token_key: str | None
    vc_api_secret: str | None
    line_channel_access_token: str | None
    line_to_user_id: str | None
    dry_run: bool

    def require(self, *names: str) -> None:
        """必要な設定が欠けていれば、値を出さずに名前だけ挙げて止める。"""
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            env_names = {
                "database_url": "DATABASE_URL",
                "vc_api_token_key": "VC_API_TOKEN_KEY",
                "vc_api_secret": "VC_API_SECRET",
                "line_channel_access_token": "LINE_CHANNEL_ACCESS_TOKEN",
                "line_to_user_id": "LINE_TO_USER_ID",
            }
            raise ConfigError(
                "必須の環境変数が未設定です: "
                + ", ".join(env_names.get(n, n) for n in missing)
            )

    def secret_values(self) -> list[str]:
        """ログから伏せるべき実値（禁止事項9）。"""
        values = [
            self.vc_api_token_key,
            self.vc_api_secret,
            self.line_channel_access_token,
            self.line_to_user_id,
            self.database_url,
        ]
        return [v for v in values if v]


def load_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL"),
        vc_api_token_key=os.getenv("VC_API_TOKEN_KEY"),
        vc_api_secret=os.getenv("VC_API_SECRET"),
        line_channel_access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN"),
        line_to_user_id=os.getenv("LINE_TO_USER_ID"),
        dry_run=_flag("DRY_RUN", default=True),
    )
