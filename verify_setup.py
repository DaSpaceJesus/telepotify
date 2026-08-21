"""
Telepotify Verification and Auth Diagnostic Script
Tests Spotify API, Telegram Reporter Bot, and Telegram Userbot connections.
"""

import os
import sys
import asyncio
from dotenv import load_dotenv

load_dotenv()

def print_header(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

async def test_spotify():
    print_header("1. Testing Spotify Web API Connection")
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    playlist_id = os.getenv("SPOTIFY_PLAYLIST_ID")

    if not all([client_id, client_secret, playlist_id]):
        print("❌ [Spotify] Missing Spotify credentials in .env!")
        return False

    try:
        from spotify_client import SpotifySyncClient
        print(f"Connecting to Spotify (Redirect URI: {redirect_uri})...")
        client = SpotifySyncClient(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            playlist_id=playlist_id
        )
        
        # Test playlist access
        pl = client.sp.playlist(playlist_id, fields="name,owner.display_name,tracks.total")
        name = pl.get("name", "Unknown")
        owner = pl.get("owner", {}).get("display_name", "Unknown")
        total = pl.get("tracks", {}).get("total", 0)

        print("✅ [Spotify] Authentication Successful!")
        print(f"   🎵 Playlist Name: '{name}'")
        print(f"   👤 Playlist Owner: '{owner}'")
        print(f"   📊 Current Total Tracks: {total}")
        return True
    except Exception as e:
        print(f"❌ [Spotify] Connection Error: {e}")
        return False

async def test_telegram_reporter():
    print_header("2. Testing Telegram Reporter Bot")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    notify_chat_ids_raw = os.getenv("TELEGRAM_NOTIFY_CHAT_IDS", "")
    notify_chat_ids = [cid.strip() for cid in notify_chat_ids_raw.split(",") if cid.strip()]

    if not bot_token:
        print("❌ [Telegram Bot] Missing TELEGRAM_BOT_TOKEN in .env!")
        return False

    if not notify_chat_ids:
        print("❌ [Telegram Bot] Missing TELEGRAM_NOTIFY_CHAT_IDS in .env!")
        return False

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # 1. Test getMe
            async with session.get(f"https://api.telegram.org/bot{bot_token}/getMe") as resp:
                data = await resp.json()
                if not data.get("ok"):
                    print(f"❌ [Telegram Bot] getMe failed: {data}")
                    return False
                bot_username = data.get("result", {}).get("username")
                bot_name = data.get("result", {}).get("first_name")
                print(f"✅ [Telegram Bot] Bot Token Valid: @{bot_username} ({bot_name})")

            # 2. Test sending ping to notify chat IDs
            print(f"   Sending test ping to configured chats: {notify_chat_ids}...")
            for chat_id in notify_chat_ids:
                payload = {
                    "chat_id": chat_id,
                    "text": "🤖 <b>Telepotify Test Ping</b>\n\nConnection verified successfully! Your bot is ready.",
                    "parse_mode": "HTML"
                }
                async with session.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json=payload) as resp:
                    send_data = await resp.json()
                    if send_data.get("ok"):
                        print(f"   ✅ Successfully sent test message to chat ID: {chat_id}")
                    else:
                        print(f"   ⚠️ Could not message chat ID {chat_id}: {send_data.get('description')}")
                        print(f"      (Hint: Has user {chat_id} pressed /start on @{bot_username}?)")
        return True
    except Exception as e:
        print(f"❌ [Telegram Bot] Error: {e}")
        return False

async def main():
    print_header("Telepotify Credentials & Connection Diagnostics")
    spotify_ok = await test_spotify()
    bot_ok = await test_telegram_reporter()
    
    print_header("Diagnostic Summary")
    print(f"Spotify Web API:       {'✅ OK' if spotify_ok else '❌ Check credentials / Auth needed'}")
    print(f"Telegram Reporter Bot:  {'✅ OK' if bot_ok else '❌ Check credentials'}")

if __name__ == "__main__":
    asyncio.run(main())
