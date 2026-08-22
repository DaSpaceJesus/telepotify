"""
Telegram Chat Listener (Telethon Userbot)
Monitors direct messages for Spotify tracks, albums, playlists, and non-Spotify music links.
"""

import re
import time
import os
import asyncio
import aiohttp
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
        self.api_id = int(api_id)
        self.api_hash = str(api_hash).strip()
        self.phone = str(phone).strip()
        self.target_chat_raw = str(target_chat).strip()
        self.target_chat_clean_username = self.target_chat_raw.lstrip("@").lower()
        self.session_name = session_name
        self.file_watcher = file_watcher
        self.spotify_client = spotify_client
        self.telegram_reporter = telegram_reporter
        self.state_db = state_db
        
        self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)
        self._target_entity = None
        self._target_chat_ids = set()

        # Build set of target ID representations if numeric
        try:
            numeric_id = int(self.target_chat_raw)
            self._target_chat_ids.add(numeric_id)
            self._target_chat_ids.add(str(numeric_id))
            # Also handle channel/supergroup -100 prefix variations
            if str(numeric_id).startswith("-100"):
                stripped = int(str(numeric_id)[4:])
                self._target_chat_ids.add(stripped)
                self._target_chat_ids.add(str(stripped))
            else:
                prefixed = int(f"-100{abs(numeric_id)}")
                self._target_chat_ids.add(prefixed)
                self._target_chat_ids.add(str(prefixed))
        except ValueError:
            pass

        # Regex Patterns (strictly bounded to prevent ReDoS / garbage matches)
        self.track_re = re.compile(r'https?://(?:open|play)\.spotify\.com/track/([a-zA-Z0-9]{15,30})|spotify:track:([a-zA-Z0-9]{15,30})')
        self.album_re = re.compile(r'https?://(?:open|play)\.spotify\.com/album/([a-zA-Z0-9]{15,30})')
        self.playlist_re = re.compile(r'https?://(?:open|play)\.spotify\.com/playlist/([a-zA-Z0-9]{15,30})')
        self.short_re = re.compile(r'https?://(?:open\.spotify\.com/s/|spotify\.link/|spotify\.app\.link/)[a-zA-Z0-9_-]+')
        self.non_spotify_re = re.compile(r'https?://(?:music\.apple\.com|youtu\.be|(?:www\.)?youtube\.com|soundcloud\.com)/[^\s]+')

    def _secure_session_file(self):
        """Enforces 0600 permissions on Telethon session file."""
        session_file = f"{self.session_name}.session"
        if os.path.exists(session_file):
            try:
                os.chmod(session_file, 0o600)
            except OSError as e:
                print(f"[TelegramListener] Warning: Could not set 0600 on {session_file}: {e}")

    async def start(self):
        """Starts the Telethon client and attaches message listeners."""
        print("[TelegramListener] Connecting to Telegram User Client...")
        await self.client.start(phone=self.phone)
        self._secure_session_file()
        print("[TelegramListener] Logged in successfully!")

        # Resolve target chat entity
        try:
            lookup = int(self.target_chat_raw) if self.target_chat_raw.lstrip("-").isdigit() else self.target_chat_raw
            self._target_entity = await self.client.get_entity(lookup)
            if self._target_entity:
                self._target_chat_ids.add(self._target_entity.id)
                self._target_chat_ids.add(str(self._target_entity.id))
                name = getattr(self._target_entity, 'title', None) or getattr(self._target_entity, 'first_name', self.target_chat_raw)
                print(f"[TelegramListener] Strictly monitoring target chat: {name} (ID: {self._target_entity.id})")
        except Exception as e:
            print(f"[TelegramListener] Warning: Could not resolve entity '{self.target_chat_raw}' at init ({e}). Will enforce runtime chat ID filters.")

        @self.client.on(events.NewMessage(chats=self._target_entity if self._target_entity else None))
        async def handler(event):
            if not await self._is_target_chat(event):
                return

            message_text = event.raw_text or ""
            if not message_text.strip():
                return

            await self._process_message(message_text)

        print("[TelegramListener] Listening for music links...")

    async def _is_target_chat(self, event) -> bool:
        """Strictly validates whether the event originated from the authorized target chat."""
        if self._target_entity and event.chat_id == self._target_entity.id:
            return True

        if event.chat_id in self._target_chat_ids or str(event.chat_id) in self._target_chat_ids:
            return True

        try:
            chat = await event.get_chat()
            if not chat:
                return False
            
            chat_id = getattr(chat, 'id', None)
            if chat_id and (chat_id in self._target_chat_ids or str(chat_id) in self._target_chat_ids):
                return True

            username = getattr(chat, 'username', '')
            if username and username.lower() == self.target_chat_clean_username:
                return True
        except Exception:
            return False

        return False

    async def run_until_disconnected(self):
        await self.client.run_until_disconnected()

    async def _resolve_short_url(self, url: str) -> str:
        """Follows HTTP redirects and extracts canonical URLs for shortened Spotify links."""
        clean_url = url.rstrip(".,!?;:)>'\"")
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        timeout = aiohttp.ClientTimeout(total=8)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(clean_url, allow_redirects=True) as resp:
                    if resp.status < 400:
                        resolved = str(resp.url)
                        # If redirect gave a direct Spotify entity URL, return immediately
                        if "/track/" in resolved or "/album/" in resolved or "/playlist/" in resolved:
                            print(f"[TelegramListener] Resolved short link (302 redirect): {clean_url} -> {resolved}")
                            return resolved

                        # Fallback: Parse HTML meta tags (og:url / canonical) if Spotify returns an interstitial page
                        html_body = await resp.text()
                        og_match = re.search(r'(?:property|name)=["\'](?:og:url|twitter:url)["\']\s+content=["\']([^"\']+)["\']', html_body, re.IGNORECASE)
                        if og_match and ("/track/" in og_match.group(1) or "/album/" in og_match.group(1) or "/playlist/" in og_match.group(1)):
                            canonical_url = og_match.group(1)
                            print(f"[TelegramListener] Resolved short link (HTML meta): {clean_url} -> {canonical_url}")
                            return canonical_url

                        print(f"[TelegramListener] Resolved short link: {clean_url} -> {resolved}")
                        return resolved
        except Exception as e:
            print(f"[TelegramListener] Warning: Failed to resolve short URL {clean_url}: {e}")
        return clean_url

    async def _process_message(self, text: str):
        # Check if auto-sync is currently active
        if not self.state_db.is_sync_enabled():
            print("[TelegramListener] ⏸️ Auto-sync is currently PAUSED. Skipping incoming music link.")
            return

        # 0. Expand and resolve any Spotify short links (e.g. open.spotify.com/s/..., spotify.link/...)
        short_matches = self.short_re.findall(text)
        if short_matches:
            resolved_urls = await asyncio.gather(*[self._resolve_short_url(u) for u in short_matches])
            for short_u, resolved_u in zip(short_matches, resolved_urls):
                text = text.replace(short_u, resolved_u)

        # 1. Check for single/multiple Spotify tracks
        track_matches = self.track_re.findall(text)
        if track_matches:
            new_urls_to_append = []
            seen_in_msg = set()
            for m in track_matches:
                track_id = m[0] or m[1]
                if not track_id or track_id in seen_in_msg:
                    continue
                seen_in_msg.add(track_id)

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
            raw_url = non_spotify_match.group(0).rstrip(".,!?;:)>'\"")
            print(f"[TelegramListener] Non-Spotify link detected: {raw_url}")
            await self.telegram_reporter.send_unsupported_link_alert(raw_url)

