import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode

from telegram_auth import validate_init_data


def signed_init_data(token, user_id, auth_date):
    params = {
        "auth_date": str(auth_date),
        "query_id": "test-query",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(
        secret,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(params)


class TelegramAuthTest(unittest.TestCase):
    def test_accepts_valid_signature(self):
        now = int(time.time())
        init_data = signed_init_data("test-token", 123, now)
        params = validate_init_data(init_data, bot_token="test-token", now=now)
        self.assertIsNotNone(params)
        self.assertEqual(json.loads(params["user"])["id"], 123)

    def test_rejects_tampered_user(self):
        now = int(time.time())
        init_data = signed_init_data("test-token", 123, now).replace(
            "%22id%22%3A123",
            "%22id%22%3A456",
        )
        self.assertIsNone(validate_init_data(init_data, bot_token="test-token", now=now))

    def test_rejects_expired_signature(self):
        now = int(time.time())
        init_data = signed_init_data("test-token", 123, now - 86401)
        self.assertIsNone(validate_init_data(init_data, bot_token="test-token", now=now))


if __name__ == "__main__":
    unittest.main()
