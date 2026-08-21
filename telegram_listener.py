"""
Telegram Chat Listener (Telethon Userbot)
Monitors direct messages for Spotify tracks, albums, playlists, and non-Spotify music links.
"""

import re
import time
import os
import asyncio
from telethon import TelegramClient, events
from typing import List, Optional

class TelegramChatListener:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        phone: str,
        target_chat: str,
        file_watcher,
        spotify_client,
        telegram_reporter,
        state_db,
        session_name: str = "telegram_userbot"
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.target_chat = target_chat
        self.file_watcher = file_watcher
        self.spotify_client = spotify_client
        self.telegram_reporter = telegram_reporter
        self.state_db = state_db
        
        self.client = TelegramClient(session_name, self.api_id, self.api_hash)

        # Regex Patterns
        self.track_re = re.compile(r'https?://(?:open|play)\.spotify\.com/track/([a-zA-Z0-9]+)|spotify:track:([a-zA-Z0-9]+)')
        self.album_re = re.compile(r'https?://(?:open|play)\.spotify\.com/album/([a-zA-Z0-9]+)')
        self.playlist_re = re.compile(r'https?://(?:open|play)\.spotify\.com/playlist/([a-zA-Z0-9]+)')
        self.non_spotify_re = re.compile(r'https?://(?:music\.apple\.com|youtu\.be|(?:www\.)?youtube\.com|soundcloud\.com)/[^\s]+')

    async def start(self):
        """Starts the Telethon client and attaches message listeners."""
        print("[TelegramListener] Connecting to Telegram User Client...")
        await self.client.start(phone=self.phone)
        print("[TelegramListener] Logged in successfully!")

        # Resolve target chat entity
        try:
            target_entity = await self.client.get_entity(self.target_chat)
            target_id = target_entity.id
            print(f"[TelegramListener] Monitoring chat: {getattr(target_entity, 'title', None) or getattr(target_entity, 'first_name', self.target_chat)} (ID: {target_id})")
        except Exception as e:
            print(f"[TelegramListener] Warning: Could not resolve entity '{self.target_chat}' directly ({e}). Will match on chat incoming IDs.")
            target_entity = None

        @self.client.on(events.NewMessage(chats=target_entity if target_entity else None))
        async def handler(event):
            # If target_entity wasn't resolved by object, check peer ID
            if not target_entity:
                chat = await event.get_chat()
                chat_str = str(getattr(chat, 'id', ''))
                user_name = str(getattr(chat, 'username', ''))
                if self.target_chat not in (chat_str, user_name):
                    return

            message_text = event.raw_text or ""
            if not message_text:
                return

            await self._process_message(message_text)

        print("[TelegramListener] Listening for music links...")

    async def run_until_disconnected(self):
        await self.client.run_until_disconnected()

    async def _process_message(self, text: str):
        # 1. Check for single/multiple Spotify tracks
        track_matches = self.track_re.findall(text)
        if track_matches:
            new_urls_to_append = []
            for m in track_matches:
                track_id = m[0] or m[1]
                if not track_id:
                    continue

                # Check if duplicate
                if self.state_db.is_track_synced(track_id):
                    print(f"[TelegramListener] Duplicate track detected: {track_id}")
                    track_info = self.spotify_client.get_track_info(track_id)
                    await self.telegram_reporter.send_duplicate_alert(track_info)
                else:
                    clean_url = f"https://open.spotify.com/track/{track_id}"
                    new_urls_to_append.append(clean_url)

            if new_urls_to_append:
                print(f"[TelegramListener] Appending {len(new_urls_to_append)} link(s) to spotify_links.txt...")
                self.file_watcher.append_links(new_urls_to_append)

        # 2. Check for Spotify Album link
        album_match = self.album_re.search(text)
        if album_match:
            album_id = album_match.group(1)
            print(f"[TelegramListener] Album detected: {album_id}")
            album_info = self.spotify_client.get_album_info(album_id)
            if album_info and album_info.get("track_count", 0) > 0:
                approval_id = f"album_{album_id}_{int(time.time())}"
                await self.telegram_reporter.send_approval_prompt(album_info, approval_id)

        # 3. Check for Spotify Playlist link
        playlist_match = self.playlist_re.search(text)
        if playlist_match:
            playlist_id = playlist_match.group(1)
            # Avoid re-adding the target playlist itself
            if playlist_id != self.spotify_client.playlist_id:
                print(f"[TelegramListener] External playlist detected: {playlist_id}")
                pl_info = self.spotify_client.get_source_playlist_info(playlist_id)
                if pl_info and pl_info.get("track_count", 0) > 0:
                    approval_id = f"playlist_{playlist_id}_{int(time.time())}"
                    await self.telegram_reporter.send_approval_prompt(pl_info, approval_id)

        # 4. Check for Non-Spotify Music link
        non_spotify_match = self.non_spotify_re.search(text)
        if non_spotify_match and not track_matches and not album_match and not playlist_match:
            url = non_spotify_match.group(0)
            print(f"[TelegramListener] Non-Spotify link detected: {url}")
            await self.telegram_reporter.send_unsupported_link_alert(url)
