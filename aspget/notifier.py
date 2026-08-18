"""LINE Messaging API への通知。

エンドポイント: POST https://api.line.me/v2/bot/message/push
  ヘッダ: Authorization: Bearer {channel access token} / Content-Type: application/json
  ボディ: {"to": "<userId>", "messages": [{"type": "text", "text": "..."}]}
  messages は最大5件、text は最大5000文字。

通知の失敗で収集全体を落とさない。通知は結果の報告であって、
収集そのものではないため。
"""
from __future__ import annotations

import logging

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_TEXT_LENGTH = 5000
TIMEOUT = httpx.Timeout(20.0)


class LineNotifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.dry_run = settings.dry_run

    def push(self, text: str) -> bool:
        """送信できたら True。失敗しても例外を投げない。"""
        if len(text) > MAX_TEXT_LENGTH:
            text = text[: MAX_TEXT_LENGTH - 3] + "..."

        if self.dry_run:
            logger.info("[DRY_RUN] LINE通知を送信しません。本文:\n%s", text)
            return False

        try:
            self._settings.require("line_channel_access_token", "line_to_user_id")
        except Exception as exc:  # ConfigError
            logger.error("LINE通知の設定が不足しています: %s", exc)
            return False

        try:
            response = httpx.post(
                PUSH_URL,
                headers={
                    "Authorization": f"Bearer {self._settings.line_channel_access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "to": self._settings.line_to_user_id,
                    "messages": [{"type": "text", "text": text}],
                },
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.error("LINE通知に失敗しました（通信エラー）: %s", type(exc).__name__)
            return False

        if response.status_code != 200:
            # レスポンス本文にトークンは含まれないが、念のため本文は出さない
            logger.error("LINE通知に失敗しました (HTTP %s)", response.status_code)
            return False

        logger.info("LINE通知を送信しました")
        return True
