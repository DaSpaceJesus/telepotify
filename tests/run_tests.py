"""
Security Test Runner for Telepotify using standard library unittest and asyncio
Runs in project virtual environment without extra test runner dependencies.
"""

import os
import sys
import stat
import html
import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state_db import StateDB
from telegram_reporter import TelegramReporter
from telegram_listener import TelegramChatListener
from file_watcher import PlaylistFileWatcher
from spotify_client import SpotifySyncClient
from main import secure_sensitive_files


class TestTelepotifySecurity(unittest.TestCase):

    def test_state_db_permissions_and_wal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_sync.db")
            db = StateDB(db_path)
            
            # Check permissions: must be 0600 (read/write only by owner)
            file_stat = os.stat(db_path)
            mode = stat.S_IMODE(file_stat.st_mode)
            self.assertEqual(mode, 0o600, f"Expected 0600 permissions, got {oct(mode)}")

            # Check WAL mode
            with db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA journal_mode;")
                journal_mode = cursor.fetchone()[0]
                self.assertEqual(journal_mode.lower(), "wal")

    def test_main_secure_sensitive_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = os.path.join(tmpdir, ".env")
            cache_file = os.path.join(tmpdir, ".cache-spotify")
            with open(env_file, "w") as f:
                f.write("SECRET=123")
            with open(cache_file, "w") as f:
                f.write("TOKEN=xyz")
            
            # Set permissive permissions
            os.chmod(env_file, 0o644)
            os.chmod(cache_file, 0o644)

            current_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                secure_sensitive_files()
                self.assertEqual(stat.S_IMODE(os.stat(env_file).st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(os.stat(cache_file).st_mode), 0o600)
            finally:
                os.chdir(current_cwd)

    def test_url_and_html_sanitization(self):
        reporter = TelegramReporter(
            bot_token="123456:dummy_token",
            notify_chat_ids=["100"],
            state_db=None
        )

        self.assertTrue(reporter._is_safe_url("https://open.spotify.com/track/123"))
        self.assertTrue(reporter._is_safe_url("http://127.0.0.1:8888/callback"))
        self.assertFalse(reporter._is_safe_url("javascript:alert(1)"))
        self.assertFalse(reporter._is_safe_url("data:text/html,<script>"))
        self.assertFalse(reporter._is_safe_url(""))
        self.assertFalse(reporter._is_safe_url(None))

        # Check token redaction in logs
        redacted = reporter._redact("Error sending to https://api.telegram.org/bot123456:dummy_token/sendPhoto")
        self.assertNotIn("123456:dummy_token", redacted)
        self.assertIn("[REDACTED_BOT_TOKEN]", redacted)

    def test_listener_regex_and_deduplication(self):
        listener = TelegramChatListener(
            api_id=12345,
            api_hash="dummy_hash",
            phone="+1234567890",
            target_chat="12345",
            file_watcher=None,
            spotify_client=None,
            telegram_reporter=None,
            state_db=None
        )

        text = (
            "Check out https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT and "
            "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT again! "
            "Also album https://open.spotify.com/album/37i9dQZF1DXc7FZ2VBjaeT and "
            "non-spotify https://music.apple.com/us/album/test/12345."
        )

        track_matches = listener.track_re.findall(text)
        self.assertEqual(len(track_matches), 2)
        self.assertEqual(track_matches[0][0], "4cOdK2wGLETKBW3PvgPWqT")

        album_match = listener.album_re.search(text)
        self.assertIsNotNone(album_match)
        self.assertEqual(album_match.group(1), "37i9dQZF1DXc7FZ2VBjaeT")

        non_spotify = listener.non_spotify_re.search(text)
        self.assertIsNotNone(non_spotify)
        cleaned_url = non_spotify.group(0).rstrip(".,!?;:)>'\"")
        self.assertEqual(cleaned_url, "https://music.apple.com/us/album/test/12345")

    def test_spotify_client_id_validation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, ".cache-test")
            with open(cache_path, "w") as f:
                f.write("token")

            with patch("spotipy.Spotify"):
                client = SpotifySyncClient(
                    client_id="id",
                    client_secret="secret",
                    redirect_uri="http://127.0.0.1:8888/callback",
                    playlist_id="playlist_123",
                    cache_path=cache_path
                )

                self.assertTrue(client._is_valid_id("4cOdK2wGLETKBW3PvgPWqT"))
                self.assertFalse(client._is_valid_id("invalid/id; injection"))
                self.assertFalse(client._is_valid_id(""))
                self.assertFalse(client._is_valid_id("short"))

                # Check cache file permission
                self.assertEqual(stat.S_IMODE(os.stat(cache_path).st_mode), 0o600)


class TestAsyncTelepotifySecurity(unittest.IsolatedAsyncioTestCase):

    async def test_telegram_reporter_authorization_blocked(self):
        """Verify that unauthorized users cannot click buttons to approve/dismiss tracks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = StateDB(os.path.join(tmpdir, "test.db"))
            # Create pending approval
            db.create_pending_approval(
                approval_id="album_test_123",
                entity_type="album",
                entity_id="test_id_123",
                title="Secret Album",
                artist="Secret Artist",
                track_count=5,
                track_ids=["id1", "id2"],
                message_ids={"100": 1}
            )

            reporter = TelegramReporter(
                bot_token="123456:dummy_token",
                notify_chat_ids=["100", "200"],
                state_db=db
            )

            # Mock aiohttp session
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value={"ok": True})
            
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            mock_cm.__aexit__.return_value = None
            mock_post = MagicMock(return_value=mock_cm)

            with patch("aiohttp.ClientSession.post", mock_post):
                # Unauthorized user ID 999 attempts to approve
                unauthorized_query = {
                    "id": "query_999",
                    "data": "approve:album_test_123",
                    "from": {"id": 999, "first_name": "Attacker"}
                }
                await reporter._handle_callback_query(unauthorized_query)

                # Verification: DB status must remain 'pending'
                approval = db.get_pending_approval("album_test_123")
                self.assertEqual(approval["status"], "pending", "Unauthorized user was able to modify approval status!")

                # Verification: answerCallbackQuery must have been called with alert / Access Denied
                self.assertTrue(mock_post.called)
                call_args = mock_post.call_args_list[0][1]["json"]
                self.assertTrue(call_args.get("show_alert"))
                self.assertIn("Access Denied", call_args.get("text", ""))

            await reporter.close()

    async def test_telegram_reporter_authorization_allowed(self):
        """Verify that authorized users can successfully approve."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = StateDB(os.path.join(tmpdir, "test.db"))
            db.create_pending_approval(
                approval_id="album_auth_456",
                entity_type="album",
                entity_id="test_id_456",
                title="Authorized Album",
                artist="Authorized Artist",
                track_count=3,
                track_ids=["id1", "id2", "id3"],
                message_ids={"100": 1, "200": 2}
            )

            callback_called = False
            async def mock_callback(approval, action, user_name):
                nonlocal callback_called
                callback_called = True
                self.assertEqual(action, "approve")
                self.assertEqual(user_name, "Alice")

            reporter = TelegramReporter(
                bot_token="123456:dummy_token",
                notify_chat_ids=["100", "200"],
                state_db=db
            )
            reporter._approval_callback = mock_callback

            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value={"ok": True})
            
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            mock_cm.__aexit__.return_value = None
            mock_post = MagicMock(return_value=mock_cm)

            with patch("aiohttp.ClientSession.post", mock_post):
                authorized_query = {
                    "id": "query_100",
                    "data": "approve:album_auth_456",
                    "from": {"id": 100, "first_name": "Alice"}
                }
                await reporter._handle_callback_query(authorized_query)

                # DB status must be 'approved'
                approval = db.get_pending_approval("album_auth_456")
                self.assertEqual(approval["status"], "approved")
                self.assertEqual(approval["resolved_by"], "Alice")
                self.assertTrue(callback_called)

            await reporter.close()

    async def test_listener_chat_filtering(self):
        listener = TelegramChatListener(
            api_id=12345,
            api_hash="dummy_hash",
            phone="+1234567890",
            target_chat="1944372923",
            file_watcher=None,
            spotify_client=None,
            telegram_reporter=None,
            state_db=None
        )

        # Event matching target chat ID
        event_target = MagicMock()
        event_target.chat_id = 1944372923
        self.assertTrue(await listener._is_target_chat(event_target))

        # Event with -100 prefix variation
        event_prefixed = MagicMock()
        event_prefixed.chat_id = -1001944372923
        self.assertTrue(await listener._is_target_chat(event_prefixed))

        # Event from unauthorized stranger or group
        event_stranger = MagicMock()
        event_stranger.chat_id = 88888888
        event_stranger.get_chat = AsyncMock(return_value=MagicMock(id=88888888, username="stranger"))
        self.assertFalse(await listener._is_target_chat(event_stranger))

    async def test_file_watcher_retrigger(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            links_file = os.path.join(tmpdir, "spotify_links.txt")
            db = StateDB(os.path.join(tmpdir, "test.db"))

            mock_spotify = MagicMock()
            mock_spotify.get_playlist_total.return_value = 1
            mock_spotify.get_track_info.return_value = {"title": "Test", "artist": "Artist"}
            mock_spotify.add_tracks.return_value = True

            mock_reporter = AsyncMock()

            loop = asyncio.get_running_loop()
            watcher = PlaylistFileWatcher(
                links_file_path=links_file,
                spotify_client=mock_spotify,
                state_db=db,
                telegram_reporter=mock_reporter,
                loop=loop
            )

            # Append track and run sync
            watcher.append_links(["https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"])
            await watcher.sync_diff()

            # Track should now be recorded in DB
            self.assertTrue(db.is_track_synced("4cOdK2wGLETKBW3PvgPWqT"))
            self.assertEqual(db.get_total_active_count(), 1)


if __name__ == "__main__":
    unittest.main()
