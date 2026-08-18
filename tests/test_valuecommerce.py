import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aspget.adapters.base import NonRetryableError
from aspget.adapters.valuecommerce import (
    _extract_token,
    _map_status,
    parse_transactions,
)
from aspget.config import JST

FIXTURE = Path(__file__).parent / "fixtures" / "vc_transaction_documented_shape.json"


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_transactions_maps_all_rows(payload):
    rows = parse_transactions(payload)
    assert len(rows) == 3

    first = rows[0]
    assert first["asp_name"] == "valuecommerce"
    assert first["asp_conversion_id"] == "1234567890"   # 数値でも必ずTEXT
    assert first["asp_program_id"] == "222222"
    assert first["occurred_at"] == datetime(2026, 8, 15, 10, 23, 45, tzinfo=JST)
    assert first["status"] == "pending"
    assert first["reward_amount"] == Decimal("1200")
    assert first["site_identifier"] == "example.com"
    assert first["custom_param"] == "campaign_a"
    assert first["payload"]["detail"][0]["itemName"] == "サンプル商品A"


def test_empty_custom_param_becomes_none(payload):
    rows = parse_transactions(payload)
    assert rows[1]["custom_param"] is None   # 空文字
    assert rows[2]["custom_param"] is None   # null


def test_missing_transaction_oid_raises(payload):
    del payload["resultSet"]["rowData"][0]["transactionOid"]
    with pytest.raises(ValueError, match="transactionOid"):
        parse_transactions(payload)


def test_missing_result_set_raises():
    with pytest.raises(ValueError, match="resultSet"):
        parse_transactions({"error": "invalid_token"})


def test_no_row_data_returns_empty():
    assert parse_transactions({"resultSet": {"responseInfo": {}}}) == []


@pytest.mark.parametrize(
    "code,expected",
    [("p", "pending"), ("a", "approved"), ("c", "rejected"), ("i", "billed"),
     ("P", "pending"), ("A", "approved")],
)
def test_status_mapping(code, expected):
    assert _map_status(code) == expected


def test_unknown_status_is_not_guessed():
    # 未知のコードを approved / rejected に寄せると承認状況を取り違える
    assert _map_status("z") == "unknown:z"


def test_extract_token_from_row_data_object():
    payload = {"resultSet": {"rowData": {"bearer_token": "abc123"}}}
    assert _extract_token(payload) == "abc123"


def test_extract_token_from_row_data_array():
    payload = {"resultSet": {"rowData": [{"bearer_token": "abc123"}]}}
    assert _extract_token(payload) == "abc123"


def test_extract_token_from_result_set_directly():
    payload = {"resultSet": {"bearer_token": "abc123"}}
    assert _extract_token(payload) == "abc123"


def test_extract_token_missing_raises_non_retryable():
    # リトライすると 30分ロックを招くため、必ず NonRetryableError
    with pytest.raises(NonRetryableError):
        _extract_token({"resultSet": {"rowData": {}}})

    with pytest.raises(NonRetryableError):
        _extract_token({"error": "invalid_client"})
