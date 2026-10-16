import pytest

from cartalytix.auth import callback_code


def test_callback_rejects_missing_or_wrong_state():
    for url in [
        "com.lidlplus.app://callback?code=x",
        "com.lidlplus.app://callback?code=x&state=wrong",
        "https://evil.test/callback?code=x&state=right",
    ]:
        with pytest.raises(ValueError):
            callback_code(url, "right")


def test_callback_code_is_decoded_without_logging():
    assert (
        callback_code("com.lidlplus.app://callback?code=a%2Bb&state=right", "right")
        == "a+b"
    )
