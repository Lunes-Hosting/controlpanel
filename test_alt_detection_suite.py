import datetime
import unittest
from unittest.mock import MagicMock, patch

# Mock PteroCache.update_all before managers package is imported so offline tests pass
with patch("pterocache.PteroCache.update_all", return_value=None):
    from managers.alt_detection import (
        ensure_alt_schema,
        is_global_ip,
        is_older_account,
        record_user_ip,
        check_login_ip,
    )
    from discord_bot.account_approval import _review_embed, queue_account_review


class TestAltDetection(unittest.TestCase):

    def setUp(self):
        import managers.alt_detection as ad
        ad._schema_ready = True

    def test_is_global_ip(self):
        """Verify global public IPs return True and private/local return False."""
        self.assertTrue(is_global_ip("8.8.8.8"))
        self.assertTrue(is_global_ip("203.0.113.19"))
        self.assertTrue(is_global_ip("2001:4860:4860::8888"))

        self.assertFalse(is_global_ip("127.0.0.1"))
        self.assertFalse(is_global_ip("::1"))
        self.assertFalse(is_global_ip("localhost"))
        self.assertFalse(is_global_ip("192.168.1.100"))
        self.assertFalse(is_global_ip("10.0.0.5"))
        self.assertFalse(is_global_ip("172.16.0.1"))
        self.assertFalse(is_global_ip(""))
        self.assertFalse(is_global_ip(None))

    def test_is_older_account(self):
        """Verify account age comparison correctly favors earlier timestamp or lower ID."""
        t1 = datetime.datetime(2026, 1, 1, 12, 0, 0)
        t2 = datetime.datetime(2026, 2, 1, 12, 0, 0)

        # t1 is older than t2
        self.assertTrue(is_older_account(t1, 10, t2, 5))
        self.assertFalse(is_older_account(t2, 5, t1, 10))

        # Same timestamp: lower ID is older
        self.assertTrue(is_older_account(t1, 1, t1, 2))
        self.assertFalse(is_older_account(t1, 2, t1, 1))

        # None timestamps: lower ID is older
        self.assertTrue(is_older_account(None, 5, None, 10))
        self.assertFalse(is_older_account(None, 10, None, 5))

    @patch("managers.alt_detection.DatabaseManager.execute_query")
    def test_record_user_ip(self, mock_query):
        """Verify record_user_ip invokes the proper INSERT/ON DUPLICATE KEY UPDATE query."""
        record_user_ip(42, "203.0.113.25")
        mock_query.assert_called()
        args = mock_query.call_args[0]
        self.assertIn("INSERT INTO user_ip_history", args[0])
        self.assertIn("ON DUPLICATE KEY UPDATE", args[0])
        self.assertEqual(args[1], (42, "203.0.113.25"))

    @patch("managers.alt_detection.webhook_log")
    @patch("managers.alt_detection.DatabaseManager.execute_query")
    def test_alt_logging_into_mains_ip_is_suspended(self, mock_query, mock_webhook):
        """When an alt logs into an IP previously used by their main account, the alt is suspended."""
        main_created = datetime.datetime(2026, 1, 10)
        alt_created = datetime.datetime(2026, 3, 15)

        # Mock DB returning the main account matching this IP
        mock_query.side_effect = [
            None,  # ensure_schema / record_user_ip
            # SELECT query for matches:
            [
                (1, "main_user", "main@example.com", "member", main_created, 0, "203.0.113.50")
            ],
            None,  # UPDATE users SET suspended = 1 WHERE id = 2
        ]

        result = check_login_ip(
            user_id=2,
            email="alt@example.com",
            name="alt_user",
            role="member",
            created_at=alt_created,
            ip="203.0.113.50",
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "alt_suspended")
        self.assertEqual(result["main_account"]["id"], 1)
        self.assertEqual(result["main_account"]["email"], "main@example.com")

        # Check webhook was called
        mock_webhook.assert_called_once()
        webhook_msg = mock_webhook.call_args[0][0]
        self.assertIn("alt@example.com", webhook_msg)
        self.assertIn("main@example.com", webhook_msg)
        self.assertIn("203.0.113.50", webhook_msg)
        self.assertEqual(mock_webhook.call_args[1]["status"], 2)

        # Verify NO emojis in webhook message
        for char in webhook_msg:
            self.assertTrue(ord(char) < 0x1000 or ord(char) > 0x1FFFF, f"Emoji or unexpected unicode detected: {char}")

    @patch("managers.alt_detection.webhook_log")
    @patch("managers.alt_detection.DatabaseManager.execute_query")
    def test_main_logging_in_suspends_newer_alt_on_shared_ip(self, mock_query, mock_webhook):
        """When the main account logs in, it remains active and any newer alt on that IP is suspended."""
        main_created = datetime.datetime(2026, 1, 10)
        alt_created = datetime.datetime(2026, 3, 15)

        mock_query.side_effect = [
            None,  # record_user_ip
            # SELECT matches:
            [
                (2, "alt_user", "alt@example.com", "member", alt_created, 0, "203.0.113.50")
            ],
            None,  # UPDATE users SET suspended = 1 WHERE id = 2
        ]

        result = check_login_ip(
            user_id=1,
            email="main@example.com",
            name="main_user",
            role="member",
            created_at=main_created,
            ip="203.0.113.50",
        )

        self.assertTrue(result["allowed"])
        mock_webhook.assert_called_once()
        webhook_msg = mock_webhook.call_args[0][0]
        self.assertIn("alt@example.com", webhook_msg)
        self.assertIn("main@example.com", webhook_msg)
        self.assertIn("Active Main Account", webhook_msg)
        self.assertIn("Suspended Alt Account", webhook_msg)

    @patch("managers.alt_detection.webhook_log")
    @patch("managers.alt_detection.DatabaseManager.execute_query")
    def test_staff_accounts_never_banned(self, mock_query, mock_webhook):
        """Staff roles ('admin', 'support') are protected from auto-suspension."""
        result = check_login_ip(
            user_id=99,
            email="admin@example.com",
            name="admin_user",
            role="admin",
            created_at=datetime.datetime(2026, 5, 1),
            ip="203.0.113.99",
        )
        self.assertTrue(result["allowed"])
        mock_webhook.assert_not_called()

    @patch("managers.alt_detection.webhook_log")
    @patch("managers.alt_detection.DatabaseManager.execute_query")
    def test_private_localhost_ip_does_not_trigger_ban(self, mock_query, mock_webhook):
        """Logins from localhost or private LAN never trigger cross-account suspension."""
        result = check_login_ip(
            user_id=5,
            email="dev@example.com",
            name="dev_user",
            role="member",
            created_at=datetime.datetime(2026, 4, 1),
            ip="127.0.0.1",
        )
        self.assertTrue(result["allowed"])
        mock_webhook.assert_not_called()

    def test_discord_review_embed_no_emojis_and_full_info(self):
        """Verify the review embed contains full email, signup IP, main account details, and NO emojis."""
        main_info = {
            "id": 10,
            "name": "original_owner",
            "email": "primary@example.com",
            "ip": "203.0.113.11",
        }

        embed = _review_embed(
            user_id=55,
            name="alt_account",
            email="alt_full@example.com",
            status="Pending review",
            reason="Browser profile matches account ID 10",
            alt_ip="198.51.100.99",
            main_account_info=main_info,
        )

        field_names = [f.name for f in embed.fields]
        field_values = [f.value for f in embed.fields]

        self.assertIn("Full Email", field_names)
        self.assertIn("alt_full@example.com", field_values)

        self.assertIn("Signup IP", field_names)
        self.assertIn("198.51.100.99", field_values)

        self.assertIn("Matched Main Account", field_names)
        main_field_idx = field_names.index("Matched Main Account")
        self.assertIn("primary@example.com", field_values[main_field_idx])
        self.assertIn("203.0.113.11", field_values[main_field_idx])
        self.assertIn("original_owner", field_values[main_field_idx])

        # Check title, description, and fields for any emojis
        full_text = f"{embed.title} {embed.description} {' '.join(field_names)} {' '.join(field_values)}"
        for char in full_text:
            self.assertTrue(ord(char) < 0x1000 or ord(char) > 0x1FFFF, f"Emoji detected in embed: {char}")

    @patch("managers.authentication.bcrypt.checkpw", return_value=True)
    @patch("managers.authentication.DatabaseManager.execute_query")
    def test_login_returns_suspended_when_account_is_suspended(self, mock_query, mock_bcrypt):
        """Verify login returns 'suspended' if result[15] (suspended) is 1."""
        from managers.authentication import login
        user_row = (
            1, "user", "member", 100, 1, 10, None, "test@example.com",
            None, "hashedpw", None, datetime.datetime.now(), None, "1.1.1.1", None, 1
        )
        mock_query.return_value = user_row
        res = login("test@example.com", "mypass", "1.1.1.1")
        self.assertEqual(res, "suspended")

    @patch("managers.alt_detection.check_login_ip")
    @patch("managers.authentication.bcrypt.checkpw", return_value=True)
    @patch("managers.authentication.DatabaseManager.execute_query")
    def test_login_returns_alt_suspended_when_ip_check_fails(self, mock_query, mock_bcrypt, mock_check_ip):
        """Verify login returns 'alt_suspended' when check_login_ip returns allowed=False."""
        from managers.authentication import login
        user_row = (
            2, "alt", "member", 100, 1, 10, None, "alt@example.com",
            None, "hashedpw", None, datetime.datetime.now(), None, "1.1.1.1", None, 0
        )
        mock_query.return_value = user_row
        mock_check_ip.return_value = {"allowed": False, "reason": "alt_suspended"}
        res = login("alt@example.com", "mypass", "1.1.1.1")
        self.assertEqual(res, "alt_suspended")

    def test_queue_account_review_queues_all_fields(self):
        """Verify queue_account_review queues full email, alt IP, and main account info."""
        import discord_bot.account_approval as aa
        with aa._pending_lock:
            aa._pending_reviews.clear()

        main_info = {"id": 1, "name": "main", "email": "main@example.com", "ip": "1.1.1.1"}
        queue_account_review(
            user_id=45,
            name="test_alt",
            email="test_alt@example.com",
            reason="Browser profile match",
            alt_ip="8.8.8.8",
            main_account_info=main_info,
        )

        with aa._pending_lock:
            self.assertEqual(len(aa._pending_reviews), 1)
            queued = aa._pending_reviews[0]
            self.assertEqual(queued[0], 45)
            self.assertEqual(queued[1], "test_alt")
            self.assertEqual(queued[2], "test_alt@example.com")
            self.assertEqual(queued[3], "Browser profile match")
            self.assertEqual(queued[4], "8.8.8.8")
            self.assertEqual(queued[5], main_info)
            aa._pending_reviews.clear()


if __name__ == "__main__":
    unittest.main()
