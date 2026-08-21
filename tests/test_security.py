"""
Automated Security & Integrity Test Suite for Telepotify
Tests authorization, HTML escaping, permission controls, concurrency, and validation.
"""

import os
import stat
import html
import pytest
import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from state_db import StateDB
from telegram_reporter import TelegramReporter
from telegram_listener import TelegramChatListener
from file_watcher import PlaylistFileWatcher
from spotify_client import SpotifySyncClient
from main import secure_sensitive_files


# ==============================================================================
# 1. State DB & File Permissions Tests
# ==============================================================================

def test_state_db_permissions_and_wal():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_sync.db")
        db = StateDB(db_path)
        
        # Check permissions: must be 0600 (read/write only by owner)
        file_stat = os.stat(db_path)
        mode = stat.S_IMODE(file_stat.st_mode)
        assert mode == 0o600, f"Expected 0600 permissions, got {oct(mode)}"

        # Check WAL mode
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode;")
            journal_mode = cursor.fetchone()[0]
            assert journal_mode.lower() == "wal"


def test_main_secure_sensitive_files():
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
            assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600
            assert stat.S_IMODE(os.stat(cache_file).st_mode) == 0o600
        finally:
            os.chdir(current_cwd)


# ==============================================================================
# 2. Telegram Reporter Authorization Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_telegram_reporter_authorization_blocked():
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
        mock_post = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ok": True})
        mock_post.return_value.__aenter__.return_value = mock_resp

        with patch("aiohttp.ClientSession.post", mock_post):
            # Unauthorized user ID 999 attempts to approve
            unauthorized_query = {
                "id": "query_999",
                "data": "approve:album_test_123",
                "from": {"id": 999, "first_name": "Attacker"}
            }
            await reporter._handle_callback_query(unauthorized_query)

            # Verification: DB status must remain "pending"
            approval = db.get_pending_approval("album_test_123")
            assert approval["status"] == "pending", "Unauthorized user was able to modify approval status!"

            # Verification: answerCallbackQuery must have been called with alert / Access Denied
            assert mock_post.called
            call_args = mock_post.call_args_list[0][1]["json"]
            assert call_args.get("show_alert") is True
            assert "Access Denied" in call_args.get("text", "")


@pytest.mark.asyncio
async def test_telegram_reporter_authorization_allowed():
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
            assert action == "approve"
            assert user_name == "Alice"

        reporter = TelegramReporter(
            bot_token="123456:dummy_token",
            notify_chat_ids=["100", "200"],
            state_db=db
        )
        reporter._approval_callback = mock_callback

        mock_post = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ok": True})
        mock_post.return_value.__aenter__.return_value = mock_resp

        with patch("aiohttp.ClientSession.post", mock_post):
            authorized_query = {
                "id": "query_100",
                "data": "approve:album_auth_456",
                "from": {"id": 100, "first_name": "Alice"}
            }
            await reporter._handle_callback_query(authorized_query)

            # DB status must be "approved"
            approval = db.get_pending_approval("album_auth_456")
            assert approval["status"] == "approved"
            assert approval["resolved_by"] == "Alice"
            assert callback_called is True


# ==============================================================================
# 3. HTML Escaping & URL Sanitization Tests
# ==============================================================================

def test_url_and_html_sanitization():
    reporter = TelegramReporter(
        bot_token="123456:dummy_token",
        notify_chat_ids=["100"],
        state_db=None
    )

    assert reporter._is_safe_url("https://open.spotify.com/track/123") is True
    assert reporter._is_safe_url("http://127.0.0.1:8888/callback") is True
    assert reporter._is_safe_url("javascript:alert(1)") is False
    assert reporter._is_safe_url("data:text/html,<script>") is False
    assert reporter._is_safe_url("") is False
    assert reporter._is_safe_url(None) is False

    # Check token redaction in logs
    redacted = reporter._redact("Error sending to https://api.telegram.org/bot123456:dummy_token/sendPhoto")
    assert "123456:dummy_token" not in redacted
    assert "[REDACTED_BOT_TOKEN]" in redacted


# ==============================================================================
# 4. Telegram Listener Target Chat Filtering Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_listener_chat_filtering():
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
    assert await listener._is_target_chat(event_target) is True

    # Event with -100 prefix variation
    event_prefixed = MagicMock()
    event_prefixed.chat_id = -1001944372923
    assert await listener._is_target_chat(event_prefixed) is True

    # Event from unauthorized stranger or group
    event_stranger = MagicMock()
    event_stranger.chat_id = 88888888
    event_stranger.get_chat = AsyncMock(return_value=MagicMock(id=88888888, username="stranger"))
    assert await listener._is_target_chat(event_stranger) is False


def test_listener_regex_and_deduplication():
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
    assert len(track_matches) == 2  # Regex finds both, listener deduplicates in loop
    assert track_matches[0][0] == "4cOdK2wGLETKBW3PvgPWqT"

    album_match = listener.album_re.search(text)
    assert album_match is not None
    assert album_match.group(1) == "37i9dQZF1DXc7FZ2VBjaeT"

    non_spotify = listener.non_spotify_re.search(text)
    assert non_spotify is not None
    assert non_spotify.group(0).rstrip(".,!?;:)>'\"") == "https://music.apple.com/us/album/test/12345"


# ==============================================================================
# 5. Spotify Client ID Validation & Cache Permissions
# ==============================================================================

def test_spotify_client_id_validation():
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

            assert client._is_valid_id("4cOdK2wGLETKBW3PvgPWqT") is True
            assert client._is_valid_id("invalid/id; injection") is False
            assert client._is_valid_id("") is False
            assert client._is_valid_id("short") is False

            # Check cache file permission
            assert stat.S_IMODE(os.stat(cache_path).st_mode) == 0o600


# ==============================================================================
# 6. File Watcher Thread-Safety & Re-Trigger
# ==============================================================================

@pytest.mark.asyncio
async def test_file_watcher_retrigger():
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
        assert db.is_track_synced("4cOdK2wGLETKBW3PvgPWqT") is True
        assert db.get_total_active_count() == 1
