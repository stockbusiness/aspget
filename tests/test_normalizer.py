from datetime import datetime
from decimal import Decimal

import pytest

from aspget.config import JST
from aspget.normalizer import (
    normalize_advertiser_key,
    parse_datetime_jst,
    parse_reward,
    to_decimal,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-08-15 10:23:45", datetime(2026, 8, 15, 10, 23, 45, tzinfo=JST)),
        ("2026/08/15 10:23", datetime(2026, 8, 15, 10, 23, tzinfo=JST)),
        ("2026-08-15", datetime(2026, 8, 15, tzinfo=JST)),
    ],
)
def test_parse_datetime_is_jst(text, expected):
    assert parse_datetime_jst(text) == expected


@pytest.mark.parametrize("text", ["", "  ", None, "unknown", "-", "壊れた値"])
def test_parse_datetime_returns_none_instead_of_raising(text):
    assert parse_datetime_jst(text) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        (1200, Decimal("1200")),
        ("1,200", Decimal("1200")),
        ("1200円", Decimal("1200")),
        ("１２００", Decimal("1200")),   # 全角
        ("3.5%", Decimal("3.5")),
        (None, None),
        ("", None),
        ("なし", None),
        (True, None),                    # bool を 1 として扱わない
    ],
)
def test_to_decimal(value, expected):
    assert to_decimal(value) == expected


def test_advertiser_key_ignores_company_form_and_spacing():
    assert normalize_advertiser_key("株式会社サンプル") == normalize_advertiser_key("サンプル(株)")
    assert normalize_advertiser_key("Sample Inc.") == normalize_advertiser_key("sample")


def test_advertiser_key_of_blank_is_none():
    assert normalize_advertiser_key("") is None
    assert normalize_advertiser_key(None) is None
    assert normalize_advertiser_key("株式会社") is None


@pytest.mark.parametrize(
    "raw,reward_type,amount,rate",
    [
        ("1200円", "fixed", Decimal("1200"), None),
        ("3.5%", "percentage", None, Decimal("3.5")),
        ("売上の2%または500円", "mixed", Decimal("500"), Decimal("2")),
        ("要問い合わせ", "unknown", None, None),
        ("", "unknown", None, None),
    ],
)
def test_parse_reward(raw, reward_type, amount, rate):
    result = parse_reward(raw)
    assert result["reward_type"] == reward_type
    assert result["reward_amount"] == amount
    assert result["reward_rate"] == rate
    assert result["reward_raw"] == raw   # 原文は必ず残す


def test_parse_reward_never_raises():
    # パース失敗で収集を止めない（CLAUDE.md コーディング規約）
    assert parse_reward(object())["reward_type"] == "unknown"
