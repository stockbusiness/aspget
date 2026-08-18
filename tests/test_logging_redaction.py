import logging

from aspget.logging_setup import MASK, RedactingFilter


def _apply(secrets, msg, args=()):
    record = logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)
    RedactingFilter(secrets).filter(record)
    return record.getMessage()


def test_known_secret_value_is_masked():
    assert "s3cr3t-value-xyz" not in _apply(["s3cr3t-value-xyz"], "key=s3cr3t-value-xyz")


def test_secret_in_args_is_masked():
    assert "s3cr3t-value-xyz" not in _apply(["s3cr3t-value-xyz"], "key=%s", ("s3cr3t-value-xyz",))


def test_bearer_token_is_masked_without_knowing_the_value():
    # APIから受け取ったトークンは .env に無いので、パターンで潰す必要がある
    message = _apply([], "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc")
    assert "eyJhbGciOiJIUzI1NiJ9.abc" not in message
    assert MASK in message


def test_bearer_token_in_json_is_masked():
    message = _apply([], '{"bearer_token": "abcdef123456"}')
    assert "abcdef123456" not in message


def test_database_password_is_masked():
    message = _apply([], "postgresql://user:hunter2@db.example.com/aspget")
    assert "hunter2" not in message
    assert "db.example.com" in message   # 接続先は残す


def test_short_values_are_not_treated_as_secrets():
    # 3文字以下を伏せると、ログが ***REDACTED*** だらけになって読めなくなる
    assert _apply(["ab"], "status=ab") == "status=ab"
