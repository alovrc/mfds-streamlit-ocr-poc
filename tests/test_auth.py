from __future__ import annotations

import base64
import os
import unittest

from auth import password_digest, verify_password


class PasswordAuthTests(unittest.TestCase):
    def test_valid_password(self) -> None:
        salt = base64.b64encode(os.urandom(16)).decode("ascii")
        expected = password_digest("correct horse battery staple", salt, 100_000)
        self.assertTrue(
            verify_password(
                "correct horse battery staple",
                salt,
                expected,
                100_000,
            )
        )

    def test_wrong_password(self) -> None:
        salt = base64.b64encode(os.urandom(16)).decode("ascii")
        expected = password_digest("expected password", salt, 100_000)
        self.assertFalse(verify_password("wrong password", salt, expected, 100_000))

    def test_rejects_weak_iteration_count(self) -> None:
        salt = base64.b64encode(os.urandom(16)).decode("ascii")
        expected = password_digest("password", salt, 99_999)
        self.assertFalse(verify_password("password", salt, expected, 99_999))


if __name__ == "__main__":
    unittest.main()
