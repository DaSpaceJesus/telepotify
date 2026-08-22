"""
Master Orchestrator
Initializes database, Spotify client, file watcher, Telegram listener, and reporter bot.
"""

import os
import sys
import glob
import asyncio
import signal
from dotenv import load_dotenv

from state_db import StateDB
from spotify_client import SpotifySyncClient
from telegram_reporter import TelegramReporter
from file_watcher import PlaylistFileWatcher
from telegram_listener import TelegramChatListener

load_dotenv()

def secure_sensitive_files():
    """Enforces restrictive 0600 permissions on sensitive credentials and databases."""
    sensitive_patterns = [".env", ".cache-spotify", "*.db", "*.sqlite3", "*.session", "*.session-journal"]
    for pattern in sensitive_patterns:
        for file_path in glob.glob(pattern):
            try:
                os.chmod(file_path, 0o600)
            except OSError:
                pass

async def main():
    print("==================================================")
    print("   Automated Spotify Playlist Sync Service")
    print("==================================================")

    # 0. Enforce local credential security
    secure_sensitive_files()

    # 1. Load Configurations
    telegram_api_id = os.getenv("TELEGRAM_API_ID")
    telegram_api_hash = os.getenv("TELEGRAM_API_HASH")
    telegram_phone = os.getenv("TELEGRAM_PHONE")
    telegram_target_chat = os.getenv("TELEGRAM_TARGET_CHAT")
    
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    notify_chat_ids_raw = os.getenv("TELEGRAM_NOTIFY_CHAT_IDS", "")
    notify_chat_ids = [cid.strip() for cid in notify_chat_ids_raw.split(",") if cid.strip()]

    spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
    spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    spotify_redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    spotify_playlist_id = os.getenv("SPOTIFY_PLAYLIST_ID")

    links_file_path = os.getenv("LINKS_FILE_PATH", "spotify_links.txt")
    db_file_path = os.getenv("DB_FILE_PATH", "sync_history.db")

    # Strict Validation
    if not all([telegram_api_id, telegram_api_hash, telegram_phone, telegram_target_chat]):
        print("[Error] Missing Telegram userbot credentials in .env!")
        sys.exit(1)

    if not telegram_bot_token or not notify_chat_ids:
        print("[Error] Missing Telegram bot token or notify chat IDs in .env!")
        sys.exit(1)

    if not all([spotify_client_id, spotify_client_secret, spotify_playlist_id]):
        print("[Error] Missing Spotify Web API credentials in .env!")
        sys.exit(1)

    loop = asyncio.get_running_loop()

    # 2. Initialize State Database
    print(f"[Init] Initializing State Database at {db_file_path}...")
    state_db = StateDB(db_file_path)

    # 3. Initialize Spotify Client
    print("[Init] Authenticating with Spotify Web API...")
    spotify_client = SpotifySyncClient(
        client_id=spotify_client_id,
        client_secret=spotify_client_secret,
        redirect_uri=spotify_redirect_uri,
        playlist_id=spotify_playlist_id
    )

    # 4. Initialize Telegram Reporter Bot
    print("[Init] Initializing Telegram Reporter Bot...")
    telegram_reporter = TelegramReporter(
        bot_token=telegram_bot_token,
        notify_chat_ids=notify_chat_ids,
        state_db=state_db,
        spotify_client=spotify_client
    )

    # 5. Initialize File Watcher
    print("[Init] Initializing File Watcher & 2-Way Sync Engine...")
    file_watcher = PlaylistFileWatcher(
        links_file_path=links_file_path,
        spotify_client=spotify_client,
        state_db=state_db,
        telegram_reporter=telegram_reporter,
        loop=loop
    )

    # 6. Initial Sync & State Reconciliation
    print("[Init] Checking current Spotify playlist state...")
    try:
        existing_playlist_ids = spotify_client.get_playlist_track_ids()
        print(f"[Init] Found {len(existing_playlist_ids)} tracks currently in Spotify playlist.")

        # Ensure existing playlist tracks are indexed into SQLite
        batch_to_index = []
        for tid in existing_playlist_ids:
            if not state_db.is_track_synced(tid):
                info = spotify_client.get_track_info(tid)
                batch_to_index.append({
                    "track_id": tid,
                    "title": info.get("title", ""),
                    "artist": info.get("artist", ""),
                    "album": info.get("album", ""),
                    "artwork_url": info.get("artwork_url", ""),
                    "source": "initial_spotify_sync"
                })
        if batch_to_index:
            state_db.record_tracks_batch(batch_to_index)
            print(f"[Init] Indexed {len(batch_to_index)} existing playlist track(s) into database.")

        # Ensure spotify_links.txt contains all active playlist tracks
        current_file_track_ids = set(file_watcher.extract_track_ids_from_file())
        missing_links = [f"https://open.spotify.com/track/{tid}" for tid in existing_playlist_ids if tid not in current_file_track_ids]

        if missing_links:
            file_watcher.append_links(missing_links)
            print(f"[Init] Added {len(missing_links)} existing track links to {links_file_path}.")

        # Ensure any tracks in spotify_links.txt not yet in Spotify playlist are added
        tracks_to_add_to_spotify = [tid for tid in file_watcher.extract_track_ids_from_file() if tid not in set(existing_playlist_ids)]
        if tracks_to_add_to_spotify:
            print(f"[Init] Adding {len(tracks_to_add_to_spotify)} initial track(s) from {links_file_path} to Spotify playlist...")
            spotify_client.add_tracks(tracks_to_add_to_spotify)
            batch_added = []
            for tid in tracks_to_add_to_spotify:
                info = spotify_client.get_track_info(tid)
                batch_added.append({
                    "track_id": tid,
                    "title": info.get("title", ""),
                    "artist": info.get("artist", ""),
                    "album": info.get("album", ""),
                    "artwork_url": info.get("artwork_url", ""),
                    "source": "initial_file_seed"
                })
            if batch_added:
                state_db.record_tracks_batch(batch_added)
            print(f"[Init] Successfully added and indexed {len(tracks_to_add_to_spotify)} track(s) to Spotify playlist.")

    except Exception as e:
        print(f"[Init] Warning during initial playlist check: {e}")

    file_watcher.start()

    # 7. Define Approval Callback for Albums/Playlists
    async def on_collection_approval(approval: dict, action: str, user_name: str):
        if action == "approve":
            track_ids = approval.get("track_ids", [])
            print(f"[Main] Adding {len(track_ids)} tracks from approved collection...")
            urls = [f"https://open.spotify.com/track/{tid}" for tid in track_ids]
            # Appending to file triggers the file watcher to sync and notify
            file_watcher.append_links(urls)

    telegram_reporter.start_polling(on_collection_approval)

    # 8. Initialize & Start Telegram Listener Userbot
    listener = TelegramChatListener(
        api_id=int(telegram_api_id),
        api_hash=telegram_api_hash,
        phone=telegram_phone,
        target_chat=telegram_target_chat,
        file_watcher=file_watcher,
        spotify_client=spotify_client,
        telegram_reporter=telegram_reporter,
        state_db=state_db
    )

    await listener.start()
    secure_sensitive_files()
    print("\n[READY] Automated Spotify Sync Service is fully operational 24/7!")
    print("Listening for messages and watching file changes. Press Ctrl+C to stop.\n")

    # Keep running until disconnected or stopped
    try:
        await listener.run_until_disconnected()
    finally:
        file_watcher.stop()
        await telegram_reporter.close()
        secure_sensitive_files()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n[Main] Service stopped gracefully.")

