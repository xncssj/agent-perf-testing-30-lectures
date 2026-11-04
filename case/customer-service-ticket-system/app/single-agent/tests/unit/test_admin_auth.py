"""管理员播种与口令校验。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.models.database import Database
from src.services.admin_auth import hash_password, verify_password


class AdminAuthTest(unittest.TestCase):
    def test_seed_admin_password_is_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "agent.db")
            user = db.get_admin_by_username("admin")
            self.assertIsNotNone(user)
            assert user is not None
            self.assertNotIn("AgentPerf@2026", user["password_hash"])
            self.assertTrue(user["password_hash"].startswith("pbkdf2_sha256$"))
            self.assertTrue(verify_password("AgentPerf@2026", user["password_hash"]))
            self.assertFalse(verify_password("wrong-password", user["password_hash"]))

    def test_profile_and_password_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "agent.db")
            user = db.get_admin_by_username("admin")
            assert user is not None
            updated = db.update_admin_profile(user["id"], "测试用户", "test@example.com", "00000000000", "值班长")
            assert updated is not None
            self.assertEqual(updated["display_name"], "测试用户")
            self.assertNotIn("password_hash", updated)
            db.update_admin_password(user["id"], hash_password("new-pass-1"))
            again = db.get_admin_by_username("admin")
            assert again is not None
            self.assertTrue(verify_password("new-pass-1", again["password_hash"]))
            self.assertFalse(verify_password("AgentPerf@2026", again["password_hash"]))


if __name__ == "__main__":
    unittest.main()
