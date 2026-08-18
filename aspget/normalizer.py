"""正規化ロジックの集約先（CLAUDE.md コーディング規約）。

アダプタ側にこの手の処理を書かないこと。ASPが増えるたびに
同じ「円」「%」「税込」の揺れを各アダプタで再実装することになる。
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .config import JST

logger = logging.getLogger(__name__)

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
)

# 会社形態の表記揺れ。広告主名をASP間で突き合わせるために落とす。
_COMPANY_TOKENS = (
    "株式会社", "有限会社", "合同会社", "合資会社", "一般社団法人", "公益社団法人",
    "(株)", "（株）", "(有)", "（有）", "co.,ltd.", "co.,ltd", "co., ltd.",
    "ltd.", "ltd", "inc.", "inc", "corp.", "corp", "k.k.", "株式會社",
)


def parse_datetime_jst(value: object) -> datetime | None:
    """ASPの日時表記を JST aware な datetime にする。

    解釈できない場合は例外を投げず None を返す。生の値は payload に
    そのまま残るため、後から再処理できる。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"unknown", "null", "-"}:
        return None

    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=JST)
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("日時として解釈できませんでした: %r", text)
        return None
    return parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed


def to_decimal(value: object) -> Decimal | None:
    """数値化。失敗しても例外は投げない（原文は payload / raw に残す）。"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("円", "").replace("¥", "").replace("%", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def normalize_advertiser_key(name: object) -> str | None:
    """ASP間で同一広告主を突き合わせるためのキー。

    完全一致を狙うものではない。あくまで突き合わせ候補を絞る用途。
    """
    if name is None:
        return None
    text = unicodedata.normalize("NFKC", str(name)).strip().lower()
    if not text:
        return None

    for token in _COMPANY_TOKENS:
        text = text.replace(token.lower(), "")
    text = re.sub(r"[\s　]+", "", text)
    text = re.sub(r"[!-/:-@\[-`{-~、。・「」【】（）]", "", text)
    return text or None


def parse_reward(raw: object) -> dict:
    """報酬表記を type / amount / rate に分解する。

    失敗しても例外を投げない（CLAUDE.md コーディング規約）。
    reward_raw に原文を残し、type='unknown' で通す。

    NOTE: Step 1（バリューコマース）では案件マスタを扱わないため未使用。
          Step 2 の CSV パースで使う。実CSVの表記を確認したうえで
          パターンを追加すること。
    """
    result: dict = {
        "reward_type": "unknown",
        "reward_amount": None,
        "reward_rate": None,
        "reward_raw": None if raw is None else str(raw),
    }
    if raw is None:
        return result

    text = unicodedata.normalize("NFKC", str(raw)).strip()
    if not text:
        return result

    rate_match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    amount_match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*円", text)

    if rate_match and amount_match:
        result["reward_type"] = "mixed"
        result["reward_rate"] = to_decimal(rate_match.group(1))
        result["reward_amount"] = to_decimal(amount_match.group(1))
    elif rate_match:
        result["reward_type"] = "percentage"
        result["reward_rate"] = to_decimal(rate_match.group(1))
    elif amount_match:
        result["reward_type"] = "fixed"
        result["reward_amount"] = to_decimal(amount_match.group(1))

    return result
