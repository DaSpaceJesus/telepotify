"""
Telegram Reporter Bot
Sends status cards, duplicate alerts, and interactive approval prompts to configured Telegram users.
"""

import aiohttp
import asyncio
import html
import json
import re
from typing import List, Dict, Optional, Callable

class TelegramReporter:
    def __init__(self, bot_token: str, notify_chat_ids: List[str], state_db, spotify_client=None):
        self.bot_token = bot_token.strip() if bot_token else ""
        # Store authorized chat IDs as clean string set for O(1) authorization checks
        self.notify_chat_ids = [str(cid).strip() for cid in notify_chat_ids if str(cid).strip()]
        self._authorized_ids = set(self.notify_chat_ids)
        self.state_db = state_db
        self.spotify_client = spotify_client
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._session: Optional[aiohttp.ClientSession] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._approval_callback: Optional[Callable] = None
        self._action_pattern = re.compile(r'^(approve|dismiss):([a-zA-Z0-9_-]+)$')

    def _redact(self, text: str) -> str:
        """Redacts sensitive bot token from error logs and exception messages."""
        if not text:
            return ""
        if self.bot_token:
            return str(text).replace(self.bot_token, "[REDACTED_BOT_TOKEN]")
        return str(text)

    def _is_safe_url(self, url: str) -> bool:
        """Validates that a URL is a legitimate HTTPS/HTTP web link."""
        if not url or not isinstance(url, str):
            return False
        return url.startswith("https://") or url.startswith("http://")

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._session and not self._session.closed:
            await self._session.close()

    # --- Message Sending Methods ---

    async def send_track_added(self, track_info: Dict, total_count: int):
        """Sends a rich track added notification card to all configured users."""
        session = await self.get_session()
        title = html.escape(str(track_info.get("title", "Unknown Title")), quote=True)
        artist = html.escape(str(track_info.get("artist", "Unknown Artist")), quote=True)
        album = html.escape(str(track_info.get("album", "")), quote=True)
        raw_url = track_info.get("url", "")
        artwork = track_info.get("artwork_url", "")

        caption = (
            f"🎵 <b>Added to Playlist!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎧 <b>Track:</b> {title}\n"
            f"👤 <b>Artist:</b> {artist}\n"
        )
        if album:
            caption += f"💿 <b>Album:</b> {album}\n"
        caption += f"📊 <b>Total in Playlist:</b> {int(total_count)} tracks\n\n"

        if self._is_safe_url(raw_url):
            caption += f"🔗 <a href='{html.escape(raw_url, quote=True)}'>Open on Spotify</a>"

        for chat_id in self.notify_chat_ids:
            try:
                if artwork and self._is_safe_url(artwork):
                    payload = {
                        "chat_id": chat_id,
                        "photo": artwork,
                        "caption": caption,
                        "parse_mode": "HTML"
                    }
                    async with session.post(f"{self.api_url}/sendPhoto", json=payload) as resp:
                        if resp.status == 429:
                            data = await resp.json()
                            retry_after = data.get("parameters", {}).get("retry_after", 2)
                            await asyncio.sleep(retry_after)
                            await session.post(f"{self.api_url}/sendPhoto", json=payload)
                        elif resp.status != 200:
                            # Fallback to text message if photo fails
                            await self._send_text(chat_id, caption)
                else:
                    await self._send_text(chat_id, caption)
            except Exception as e:
                print(f"[Reporter] Failed to send track added to {chat_id}: {self._redact(str(e))}")
            await asyncio.sleep(0.05)  # Polite delay to prevent burst flooding

    async def send_duplicate_alert(self, track_info: Dict):
        """Sends an alert that a song is already present in the playlist."""
        title = html.escape(str(track_info.get("title", "Track")), quote=True)
        artist = html.escape(str(track_info.get("artist", "Artist")), quote=True)
        raw_url = track_info.get("url", "")
        
        text = (
            f"⚠️ <b>Already in Playlist</b>\n"
            f"<b>{title}</b> by <i>{artist}</i> is already added!\n"
        )
        if self._is_safe_url(raw_url):
            text += f"🔗 <a href='{html.escape(raw_url, quote=True)}'>View on Spotify</a>"

        for chat_id in self.notify_chat_ids:
            await self._send_text(chat_id, text)
            await asyncio.sleep(0.05)

    async def send_track_removed(self, track_info: Dict, total_count: int):
        """Sends an alert when a track is removed from the playlist."""
        title = html.escape(str(track_info.get("title", "Track")), quote=True)
        artist = html.escape(str(track_info.get("artist", "Artist")), quote=True)
        
        text = (
            f"🗑️ <b>Removed from Playlist</b>\n"
            f"Removed <b>{title}</b> by <i>{artist}</i>.\n"
            f"📊 <b>Total in Playlist:</b> {int(total_count)} tracks"
        )
        for chat_id in self.notify_chat_ids:
            await self._send_text(chat_id, text)
            await asyncio.sleep(0.05)

    async def send_unsupported_link_alert(self, url: str):
        """Sends an alert when a non-Spotify link is detected."""
        escaped_url = html.escape(str(url), quote=True)
        text = (
            f"⚠️ <b>Unsupported Music Link</b>\n"
            f"Detected non-Spotify link: <code>{escaped_url}</code>\n"
            f"<i>Only Spotify track/album links are automatically synced.</i>"
        )
        for chat_id in self.notify_chat_ids:
            await self._send_text(chat_id, text)
            await asyncio.sleep(0.05)

    async def send_approval_prompt(self, entity_info: Dict, approval_id: str):
        """Sends an interactive approval prompt with inline buttons to authorized users."""
        session = await self.get_session()
        raw_entity_type = entity_info.get("type", "Collection")
        entity_type = html.escape(str(raw_entity_type).capitalize(), quote=True)
        title = html.escape(str(entity_info.get("title", "Unknown")), quote=True)
        artist = html.escape(str(entity_info.get("artist", "")), quote=True)
        count = int(entity_info.get("track_count", 0))
        artwork = entity_info.get("artwork_url", "")

        text = (
            f"💿 <b>{entity_type} Link Detected!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📂 <b>{entity_type}:</b> {title}\n"
        )
        if artist:
            text += f"👤 <b>Artist/Curator:</b> {artist}\n"
        text += (
            f"🔢 <b>Tracks:</b> {count} tracks\n\n"
            f"❓ <i>Would you like to add all {count} tracks to the playlist?</i>"
        )

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": f"✅ Add All {count} Tracks", "callback_data": f"approve:{approval_id}"},
                    {"text": "❌ Dismiss", "callback_data": f"dismiss:{approval_id}"}
                ]
            ]
        }

        sent_messages = {}
        for chat_id in self.notify_chat_ids:
            try:
                if artwork and self._is_safe_url(artwork):
                    payload = {
                        "chat_id": chat_id,
                        "photo": artwork,
                        "caption": text,
                        "parse_mode": "HTML",
                        "reply_markup": keyboard
                    }
                    async with session.post(f"{self.api_url}/sendPhoto", json=payload) as resp:
                        res_json = await resp.json()
                        if resp.status == 200 and res_json.get("ok"):
                            sent_messages[chat_id] = res_json["result"]["message_id"]
                        elif resp.status != 200:
                            # Fallback to text
                            payload_text = {
                                "chat_id": chat_id,
                                "text": text,
                                "parse_mode": "HTML",
                                "reply_markup": keyboard
                            }
                            async with session.post(f"{self.api_url}/sendMessage", json=payload_text) as tresp:
                                tres_json = await tresp.json()
                                if tresp.status == 200 and tres_json.get("ok"):
                                    sent_messages[chat_id] = tres_json["result"]["message_id"]
                else:
                    payload = {
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "reply_markup": keyboard
                    }
                    async with session.post(f"{self.api_url}/sendMessage", json=payload) as resp:
                        res_json = await resp.json()
                        if resp.status == 200 and res_json.get("ok"):
                            sent_messages[chat_id] = res_json["result"]["message_id"]
            except Exception as e:
                print(f"[Reporter] Failed to send approval prompt to {chat_id}: {self._redact(str(e))}")
            await asyncio.sleep(0.05)

        # Record message IDs in DB for dual-chat update
        self.state_db.create_pending_approval(
            approval_id=approval_id,
            entity_type=str(raw_entity_type),
            entity_id=str(entity_info.get("entity_id", "")),
            title=str(entity_info.get("title", "")),
            artist=str(entity_info.get("artist", "")),
            track_count=count,
            track_ids=entity_info.get("track_ids", []),
            message_ids=sent_messages
        )

    # --- Private Helpers ---

    async def _send_text(self, chat_id: str, text: str):
        session = await self.get_session()
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        try:
            async with session.post(f"{self.api_url}/sendMessage", json=payload) as resp:
                if resp.status == 429:
                    data = await resp.json()
                    retry_after = data.get("parameters", {}).get("retry_after", 2)
                    await asyncio.sleep(retry_after)
                    async with session.post(f"{self.api_url}/sendMessage", json=payload) as retry_resp:
                        return await retry_resp.json()
                return await resp.json()
        except Exception as e:
            print(f"[Reporter] Error sending text to {chat_id}: {self._redact(str(e))}")
            return None

    async def _edit_message_caption(self, chat_id: str, message_id: int, new_caption: str):
        session = await self.get_session()
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": new_caption,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": []}  # Remove buttons
        }
        try:
            async with session.post(f"{self.api_url}/editMessageCaption", json=payload) as resp:
                if resp.status != 200:
                    # Fallback to editing as text message
                    payload_text = {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": new_caption,
                        "parse_mode": "HTML",
                        "reply_markup": {"inline_keyboard": []}
                    }
                    await session.post(f"{self.api_url}/editMessageText", json=payload_text)
        except Exception as e:
            print(f"[Reporter] Error editing message {message_id} in {chat_id}: {self._redact(str(e))}")

    # --- Callback Query Polling for Inline Buttons ---

    def start_polling(self, on_approval_callback: Callable):
        """Starts background polling for button clicks."""
        self._approval_callback = on_approval_callback
        self._polling_task = asyncio.create_task(self._poll_updates())

    async def _register_bot_commands(self):
        """Registers official slash commands menu in Telegram for autocompletion and the [Menu] button."""
        session = await self.get_session()
        commands = [
            {"command": "status", "description": "📊 View sync status & playlist track count"},
            {"command": "off", "description": "⏸️ Pause automatic sync"},
            {"command": "on", "description": "▶️ Resume automatic sync"},
            {"command": "help", "description": "❓ Show help guide & commands"}
        ]
        try:
            payload = {"commands": commands}
            async with session.post(f"{self.api_url}/setMyCommands", json=payload) as resp:
                data = await resp.json()
                if data.get("ok"):
                    print("[Reporter] Registered official bot commands menu with Telegram.")
        except Exception as e:
            print(f"[Reporter] Warning: Could not register bot commands: {self._redact(str(e))}")

    async def _poll_updates(self):
        offset = 0
        session = await self.get_session()
        print("[Reporter] Started Telegram command & button callback listener with authorization enforcement...")
        
        # Register official menu commands in Telegram UI
        await self._register_bot_commands()
        
        while True:
            try:
                params = {
                    "offset": offset,
                    "timeout": 20,
                    "allowed_updates": json.dumps(["callback_query", "message"])
                }
                timeout = aiohttp.ClientTimeout(total=30)
                async with session.get(f"{self.api_url}/getUpdates", params=params, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for update in data.get("result", []):
                            offset = update["update_id"] + 1
                            if "callback_query" in update:
                                await self._handle_callback_query(update["callback_query"])
                            elif "message" in update:
                                await self._handle_bot_command(update["message"])
                    elif resp.status == 409:
                        print("[Reporter] Warning: getUpdates conflict (another instance or webhook active). Backing off 5s...")
                        await asyncio.sleep(5)
                    elif resp.status == 429:
                        data = await resp.json()
                        retry_after = data.get("parameters", {}).get("retry_after", 5)
                        await asyncio.sleep(retry_after)
                    else:
                        await asyncio.sleep(2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Reporter] Polling error: {self._redact(str(e))}")
                await asyncio.sleep(3)

    async def _handle_bot_command(self, message: Dict):
        """Processes incoming slash commands sent to @telepotifybot with strict user authorization."""
        sender = message.get("from", {})
        sender_id = str(sender.get("id", "")).strip()
        sender_name = sender.get("first_name", "User")
        chat_id = str(message.get("chat", {}).get("id", "")).strip()
        text = str(message.get("text", "")).strip()

        if not text.startswith("/"):
            return

        # --- SECURITY CHECK: Enforce User Authorization (Only Kasra & Anna) ---
        if not sender_id or sender_id not in self._authorized_ids:
            print(f"[Reporter] ⛔ Security: Unauthorized command attempt blocked from user ID {sender_id} ({sender_name})")
            await self._send_text(chat_id, "⛔ <b>Access Denied</b>\nYou are not authorized to manage Telepotify.")
            return

        cmd = text.split()[0].lower().split("@")[0]
        escaped_name = html.escape(str(sender_name), quote=True)

        if cmd in ("/off", "/pause", "/stop"):
            self.state_db.set_sync_enabled(False)
            print(f"[Reporter] ⏸️ Sync paused by {sender_name} ({sender_id})")
            alert_text = (
                f"⏸️ <b>Telepotify Paused</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Auto-sync has been paused by <b>{escaped_name}</b>.\n"
                f"New music links sent in chat will not be added to Spotify.\n\n"
                f"<i>Send /on or /resume to resume syncing anytime.</i>"
            )
            for cid in self.notify_chat_ids:
                await self._send_text(cid, alert_text)

        elif cmd in ("/on", "/resume"):
            self.state_db.set_sync_enabled(True)
            print(f"[Reporter] ▶️ Sync resumed by {sender_name} ({sender_id})")
            alert_text = (
                f"▶️ <b>Telepotify Active</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Auto-sync has been resumed by <b>{escaped_name}</b>.\n"
                f"Music links sent in your chat will now be automatically synchronized!"
            )
            for cid in self.notify_chat_ids:
                await self._send_text(cid, alert_text)

        elif cmd in ("/status", "/info"):
            is_enabled = self.state_db.is_sync_enabled()
            status_badge = "🟢 <b>Active (Syncing ON)</b>" if is_enabled else "🔴 <b>Paused (Syncing OFF)</b>"
            total = self.spotify_client.get_playlist_total() if self.spotify_client else self.state_db.get_total_active_count()
            status_text = (
                f"📊 <b>Telepotify Status</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Status:</b> {status_badge}\n"
                f"• <b>Tracks in Playlist:</b> {total}\n"
                f"• <b>Authorized Admins:</b> You & Anna\n\n"
                f"⚙️ <b>Commands:</b>\n"
                f"• /on — Enable auto-sync\n"
                f"• /off — Pause auto-sync\n"
                f"• /status — View sync status\n"
                f"• /help — Show help menu"
            )
            await self._send_text(chat_id, status_text)

        elif cmd in ("/start", "/help"):
            is_enabled = self.state_db.is_sync_enabled()
            status_badge = "🟢 Active" if is_enabled else "🔴 Paused"
            help_text = (
                f"👋 <b>Hello, {escaped_name}!</b>\n\n"
                f"I automatically synchronize Spotify music links between you and Anna.\n\n"
                f"📊 <b>Current Status:</b> {status_badge}\n\n"
                f"⚙️ <b>Commands:</b>\n"
                f"• /status — Check live sync status & track count\n"
                f"• /off — Temporarily pause auto-sync\n"
                f"• /on — Resume auto-sync\n"
                f"• /help — Show this help menu"
            )
            await self._send_text(chat_id, help_text)

    async def _handle_callback_query(self, query: Dict):
        session = await self.get_session()
        query_id = query.get("id", "")
        data = str(query.get("data", "")).strip()
        user = query.get("from", {})
        user_id = str(user.get("id", "")).strip()
        user_name = user.get("first_name", "User")

        # --- SECURITY CHECK: Enforce User Authorization ---
        if not user_id or user_id not in self._authorized_ids:
            print(f"[Reporter] ⛔ Security: Unauthorized button interaction blocked from user ID {user_id} ({user_name})")
            try:
                await session.post(f"{self.api_url}/answerCallbackQuery", json={
                    "callback_query_id": query_id,
                    "text": "⛔ Access Denied: You are not authorized to approve or dismiss tracks.",
                    "show_alert": True
                })
            except Exception:
                pass
            return

        # Acknowledge callback immediately to stop loading spinner in Telegram
        try:
            await session.post(f"{self.api_url}/answerCallbackQuery", json={"callback_query_id": query_id})
        except Exception:
            pass

        # Validate callback_data format
        match = self._action_pattern.match(data)
        if not match:
            return
        action, approval_id = match.group(1), match.group(2)

        approval = self.state_db.get_pending_approval(approval_id)
        if not approval or approval.get("status") != "pending":
            return

        # Mark as resolved in DB atomically
        status = "approved" if action == "approve" else "dismissed"
        updated = self.state_db.resolve_approval(approval_id, status, user_name)
        if not updated:
            return

        entity_type_escaped = html.escape(str(approval.get('entity_type', 'Collection')).capitalize(), quote=True)
        entity_title = html.escape(str(approval.get("title", "Collection")), quote=True)
        count = int(approval.get("track_count", 0))
        escaped_user = html.escape(str(user_name), quote=True)
        
        if action == "approve":
            status_text = f"✅ <b>Approved by {escaped_user}</b>\nAdding {count} tracks to playlist..."
        else:
            status_text = f"❌ <b>Dismissed by {escaped_user}</b>"

        new_caption = (
            f"💿 <b>{entity_type_escaped}:</b> {entity_title}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_text}"
        )

        # Update message in BOTH users' chats
        message_ids = approval.get("message_ids", {})
        for chat_id, msg_id in message_ids.items():
            try:
                await self._edit_message_caption(chat_id, msg_id, new_caption)
            except Exception as e:
                print(f"[Reporter] Error updating message {msg_id} in {chat_id}: {self._redact(str(e))}")

        # Trigger execution callback
        if self._approval_callback:
            try:
                await self._approval_callback(approval, action, user_name)
            except Exception as e:
                print(f"[Reporter] Error in approval callback: {self._redact(str(e))}")

