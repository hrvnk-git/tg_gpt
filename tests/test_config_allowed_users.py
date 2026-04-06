from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import Settings


class ConfigAllowedUsersTests(unittest.TestCase):
    def test_empty_allowlist_is_secure_default(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "OPENAI_API_KEY": "key",
                "ALLOWED_USER_IDS": "",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertFalse(settings.allow_all_users)
        self.assertEqual(settings.allowed_user_ids, [])
        self.assertFalse(settings.is_user_allowed(123456))

    def test_star_allowlist_allows_everyone(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "OPENAI_API_KEY": "key",
                "ALLOWED_USER_IDS": "*",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertTrue(settings.allow_all_users)
        self.assertEqual(settings.allowed_user_ids, [])
        self.assertTrue(settings.is_user_allowed(123456))

    def test_explicit_allowlist_parses_ids(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "OPENAI_API_KEY": "key",
                "ALLOWED_USER_IDS": "123, 456",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertFalse(settings.allow_all_users)
        self.assertEqual(settings.allowed_user_ids, [123, 456])
        self.assertTrue(settings.is_user_allowed(123))
        self.assertFalse(settings.is_user_allowed(999))


if __name__ == "__main__":
    unittest.main()
