"""Tests for phone_utils.normalize_phone.

Run with: python -m pytest tests/test_phone_utils.py -v
(or python -m unittest tests.test_phone_utils)
"""
from __future__ import annotations

import unittest

from school_bot.bot.utils.phone_utils import normalize_phone


class NormalizePhoneTests(unittest.TestCase):
    # --- Already canonical ---------------------------------------------------
    def test_canonical_plus_998(self) -> None:
        self.assertEqual(normalize_phone("+998901234567"), "+998901234567")

    # --- Telegram Contact format (no +) -------------------------------------
    def test_no_plus_prefix(self) -> None:
        self.assertEqual(normalize_phone("998901234567"), "+998901234567")

    # --- 9-digit subscriber portion only ------------------------------------
    def test_nine_digit_subscriber(self) -> None:
        self.assertEqual(normalize_phone("901234567"), "+998901234567")

    # --- Formatting noise ---------------------------------------------------
    def test_spaces_and_dashes(self) -> None:
        self.assertEqual(normalize_phone("+998 90 123-45-67"), "+998901234567")

    def test_parentheses(self) -> None:
        self.assertEqual(normalize_phone("+998 (90) 123 45 67"), "+998901234567")

    def test_double_zero_prefix(self) -> None:
        self.assertEqual(normalize_phone("00998901234567"), "+998901234567")

    # --- Invalid / empty ----------------------------------------------------
    def test_none(self) -> None:
        self.assertIsNone(normalize_phone(None))

    def test_empty_string(self) -> None:
        self.assertIsNone(normalize_phone(""))

    def test_letters_only(self) -> None:
        self.assertIsNone(normalize_phone("abc"))

    def test_too_short(self) -> None:
        self.assertIsNone(normalize_phone("12345"))

    def test_too_long(self) -> None:
        # 13-digit blob that doesn't start with 998 and isn't a 9-digit
        # subscriber part → rejected.
        self.assertIsNone(normalize_phone("1234567890123"))

    def test_wrong_country_code(self) -> None:
        # 12 digits but not starting with 998.
        self.assertIsNone(normalize_phone("123456789012"))

    # --- Real-world samples pulled from the registration backlog ------------
    def test_live_samples_match_alochi_canonical(self) -> None:
        cases = {
            "998943910579": "+998943910579",   # Shoiraxon
            "998903069860": "+998903069860",   # Mohiraxon
            "998975018681": "+998975018681",   # Muhabbatxon
            "998905097066": "+998905097066",   # Muyassarxon
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_phone(raw), expected, f"input={raw!r}")


if __name__ == "__main__":
    unittest.main()
