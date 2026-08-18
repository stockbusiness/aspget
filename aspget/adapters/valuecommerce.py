"""バリューコマース アダプタ（Step 1・成果データ）。

公式技術資料で確定した仕様に基づく。
  トークン取得API   GET https://api.valuecommerce.com/auth/v1/affiliate/token/
                    ?grant_type=client_credentials
                    Authorization: Bearer base64("CLIENT_KEY|CLIENT_SECRET")
                    Accept: application/json
                    トークン有効期限 30分 / 30分あたり9,000回でロック
  注文別レポートAPI GET https://api.valuecommerce.com/report/v3/affiliate/transaction/
                    Authorization: Bearer {bearer_token}
                    30分あたり900回でロック / 期間指定は最大6ヶ月・遡及25ヶ月

案件マスタ（提携可能な全案件一覧）を返すAPIは提供されていない。
このアダプタが扱うのは成果データのみ。案件収集は Step 2.5 で判断する。
"""
from __future__ import annotations

import base64
import logging
from datetime import date, timedelta
from typing import Any

import httpx

from ..config import Settings, jst_today
from ..normalizer import parse_datetime_jst, to_decimal
from ..storage import save_raw_json
from .base import CollectionResult, NonRetryableError, polite_wait, with_retry

logger = logging.getLogger(__name__)

ASP_NAME = "valuecommerce"

TOKEN_URL = "https://api.valuecommerce.com/auth/v1/affiliate/token/"
REPORT_URL = "https://api.valuecommerce.com/report/v3/affiliate/transaction/"

PAGE_LIMIT = 1000          # APIの上限。ページ数＝アクセス回数を最小化する
MAX_PAGES = 50             # 暴走時の歯止め
TIMEOUT = httpx.Timeout(60.0)

# 承認状況は後日変わるため、過去分を遡って取り直す。
# 【要判断】35日は暫定値。承認確定までの実際の日数を見て調整すること。
LOOKBACK_DAYS = 35

# 検索基準日 o=注文日 / c=クリック日 / a=承認日 / i=データ挿入日
# 【要実測】'i' が「登録日」か「更新日」かを実データで確認していない。
#          確認できるまでは注文日基準（API既定値と同じ）を使う。
DEFAULT_CRITERIA = "o"

# p=未確定 a=承認 c=却下 i=支払済
APPROVAL_STATUS_ALL = "p,a,c,i"

_STATUS_MAP = {
    "p": "pending",
    "a": "approved",
    "c": "rejected",
    "i": "billed",   # スキーマのコメントには無い値。請求済みを approved と混ぜない
}


class ValueCommerceAdapter:
    name = ASP_NAME

    def __init__(self, settings: Settings) -> None:
        settings.require("vc_api_token_key", "vc_api_secret")
        self._settings = settings

    # ---- 認証 -------------------------------------------------------

    def _signature(self) -> str:
        raw = f"{self._settings.vc_api_token_key}|{self._settings.vc_api_secret}"
        return base64.b64encode(raw.encode("utf-8")).decode("ascii")

    def fetch_token(self, client: httpx.Client) -> str:
        def _call() -> str:
            response = client.get(
                TOKEN_URL,
                params={"grant_type": "client_credentials"},
                headers={
                    "Authorization": f"Bearer {self._signature()}",
                    "Accept": "application/json",
                },
            )
            _raise_for_status(response, "トークン取得")
            return _extract_token(response.json())

        token = with_retry(_call, "バリューコマース トークン取得")
        logger.info("トークンを取得しました（有効期限30分）")
        return token

    # ---- 収集 -------------------------------------------------------

    def collect(
        self,
        run_date: date | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        criteria: str = DEFAULT_CRITERIA,
    ) -> CollectionResult:
        run_date = run_date or jst_today()
        to_date = to_date or run_date
        from_date = from_date or (to_date - timedelta(days=LOOKBACK_DAYS))

        if from_date > to_date:
            raise ValueError("from_date が to_date より後ろになっています")
        if (to_date - from_date).days > 180:
            raise ValueError("期間指定はAPIの上限（最大6ヶ月）を超えられません")

        result = CollectionResult(asp_name=ASP_NAME)
        logger.info(
            "バリューコマース: %s 〜 %s の成果データを取得します (criteria=%s)",
            from_date, to_date, criteria,
        )

        with httpx.Client(timeout=TIMEOUT) as client:
            token = self.fetch_token(client)

            offset = 0
            for page in range(1, MAX_PAGES + 1):
                payload = self._fetch_page(client, token, from_date, to_date, criteria, offset)

                path = save_raw_json(
                    ASP_NAME, f"transaction_{page:03d}.json", payload, run_date
                )
                result.raw_paths.append(str(path))

                rows = parse_transactions(payload)
                result.rows.extend(rows)

                next_offset = _response_info(payload).get("nextOffset")
                logger.info(
                    "ページ %d: %d件取得（累計 %d件）", page, len(rows), result.count
                )

                if not rows or next_offset in (None, "", 0) or next_offset == offset:
                    break
                offset = int(next_offset)
                polite_wait()
            else:
                logger.warning("ページ数が上限 %d に達したため打ち切りました", MAX_PAGES)

        return result

    def _fetch_page(
        self,
        client: httpx.Client,
        token: str,
        from_date: date,
        to_date: date,
        criteria: str,
        offset: int,
    ) -> dict:
        def _call() -> dict:
            response = client.get(
                REPORT_URL,
                params={
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                    "criteria": criteria,
                    "approval_status": APPROVAL_STATUS_ALL,
                    "limit": PAGE_LIMIT,
                    "offset": offset,
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
            _raise_for_status(response, "注文別レポート取得")
            return response.json()

        return with_retry(_call, f"バリューコマース 注文別レポート取得 (offset={offset})")


# ---- レスポンス解析（通信を伴わない。再処理とテストから直接呼ぶ） ----

def _response_info(payload: dict) -> dict:
    result_set = payload.get("resultSet")
    if not isinstance(result_set, dict):
        raise ValueError(
            "レスポンスに resultSet がありません。"
            f"最上位キー: {sorted(payload)[:10]}"
        )
    info = result_set.get("responseInfo")
    return info if isinstance(info, dict) else {}


def _extract_token(payload: dict) -> str:
    """bearer_token を取り出す。

    公式資料のレスポンス構造は resultSet.rowData.bearer_token と読めるが、
    完全なサンプルJSONの掲載が無く、rowData が配列で返る可能性も残る。
    見つかった場所をログに残し、どこにも無ければ推測せず例外にする。
    """
    result_set = payload.get("resultSet")
    if not isinstance(result_set, dict):
        raise NonRetryableError(
            "トークン応答に resultSet がありません。"
            f"最上位キー: {sorted(payload)[:10]}"
        )

    row_data = result_set.get("rowData")
    candidates: list[tuple[str, Any]] = []
    if isinstance(row_data, dict):
        candidates.append(("resultSet.rowData", row_data))
    elif isinstance(row_data, list) and row_data and isinstance(row_data[0], dict):
        candidates.append(("resultSet.rowData[0]", row_data[0]))
    candidates.append(("resultSet", result_set))

    for location, container in candidates:
        token = container.get("bearer_token")
        if isinstance(token, str) and token:
            logger.debug("bearer_token の位置: %s", location)
            return token

    raise NonRetryableError(
        "トークン応答に bearer_token が見つかりません。"
        "レスポンス構造が資料と異なります（storage/raw の原本を確認してください）"
    )


def parse_transactions(payload: dict) -> list[dict]:
    """注文別レポートのレスポンスを conversions テーブルの行に変換する。"""
    result_set = payload.get("resultSet")
    if not isinstance(result_set, dict):
        raise ValueError(
            f"レスポンスに resultSet がありません。最上位キー: {sorted(payload)[:10]}"
        )

    row_data = result_set.get("rowData")
    if row_data is None:
        return []
    if isinstance(row_data, dict):
        row_data = [row_data]
    if not isinstance(row_data, list):
        raise ValueError(f"rowData の型が想定外です: {type(row_data).__name__}")

    return [_to_conversion(row) for row in row_data]


def _to_conversion(row: dict) -> dict:
    # 期待するフィールドが無ければ、近い値で代用せず止める（コーディング規約）
    transaction_oid = row.get("transactionOid")
    if transaction_oid in (None, ""):
        raise ValueError(
            f"transactionOid がありません。行のキー: {sorted(row)[:15]}"
        )

    program_oid = row.get("programOid")

    return {
        "asp_name": ASP_NAME,
        "asp_conversion_id": str(transaction_oid),
        "asp_program_id": None if program_oid in (None, "") else str(program_oid),
        "occurred_at": parse_datetime_jst(row.get("orderDate")),
        "status": _map_status(row.get("approvalStatus")),
        "reward_amount": to_decimal(row.get("affilPayment")),
        # v3 のみ。v1/v2 では欠ける
        "site_identifier": _as_text(row.get("affiliateSite")),
        # もしもの「任意パラメータ」に相当するVCのポイントパラメータ
        "custom_param": _as_text(row.get("vcptn")),
        "payload": row,
    }


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _map_status(value: object) -> str | None:
    if value is None:
        return None
    code = str(value).strip().lower()
    if not code:
        return None
    mapped = _STATUS_MAP.get(code)
    if mapped is None:
        # 新しいコードを既存の値へ寄せると、承認/却下を取り違える
        logger.warning("未知の approvalStatus です: %r", value)
        return f"unknown:{code}"
    return mapped


def _raise_for_status(response: httpx.Response, description: str) -> None:
    if response.status_code == 200:
        return

    # 4xx はリトライしない。特に 403 locked は叩き続けるとロックが伸びる
    if 400 <= response.status_code < 500:
        detail = _error_detail(response)
        raise NonRetryableError(
            f"{description} が HTTP {response.status_code} で失敗しました: {detail}"
        )

    raise httpx.HTTPStatusError(
        f"{description} が HTTP {response.status_code} で失敗しました",
        request=response.request,
        response=response,
    )


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "(本文を解釈できません)"
    if isinstance(body, dict):
        return f"{body.get('error')} / {body.get('error_description')}"
    return "(想定外の本文形式)"
