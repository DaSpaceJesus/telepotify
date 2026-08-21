"""
Telegram Reporter Bot
Sends status cards, duplicate alerts, and interactive approval prompts to configured Telegram users.
"""

import aiohttp
import asyncio
import html
from typing import List, Dict, Optional, Callable

class TelegramReporter:
    def __init__(self, bot_token: str, notify_chat_ids: List[str], state_db):
        self.bot_token = bot_token
        self.notify_chat_ids = [cid.strip() for cid in notify_chat_ids if cid.strip()]
        self.state_db = state_db
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._session: Optional[aiohttp.ClientSession] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._approval_callback: Optional[Callable] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._polling_task:
            self._polling_task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    # --- Message Sending Methods ---

    async def send_track_added(self, track_info: Dict, total_count: int):
        """Sends a rich track added notification card to all configured users."""
        session = await self.get_session()
        title = html.escape(track_info.get("title", "Unknown Title"))
        artist = html.escape(track_info.get("artist", "Unknown Artist"))
        album = html.escape(track_info.get("album", ""))
        url = track_info.get("url", "")
        artwork = track_info.get("artwork_url", "")

        caption = (
            f"🎵 <b>Added to Playlist!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎧 <b>Track:</b> {title}\n"
            f"👤 <b>Artist:</b> {artist}\n"
        )
        if album:
            caption += f"💿 <b>Album:</b> {album}\n"
        caption += (
            f"📊 <b>Total in Playlist:</b> {total_count} tracks\n\n"
            f"🔗 <a href='{url}'>Open on Spotify</a>"
        )

        for chat_id in self.notify_chat_ids:
            try:
                if artwork:
                    # Send as photo with caption
                    payload = {
                        "chat_id": chat_id,
                        "photo": artwork,
                        "caption": caption,
                        "parse_mode": "HTML"
                    }
                    async with session.post(f"{self.api_url}/sendPhoto", json=payload) as resp:
                        if resp.status != 200:
                            # Fallback to text message if photo fails
                            await self._send_text(chat_id, caption)
                else:
                    await self._send_text(chat_id, caption)
            except Exception as e:
                print(f"[Reporter] Failed to send track added to {chat_id}: {e}")

    async def send_duplicate_alert(self, track_info: Dict):
        """Sends an alert that a song is already present in the playlist."""
        title = html.escape(track_info.get("title", "Track"))
        artist = html.escape(track_info.get("artist", "Artist"))
        url = track_info.get("url", "")
        
        text = (
            f"⚠️ <b>Already in Playlist</b>\n"
            f"<b>{title}</b> by <i>{artist}</i> is already added!\n"
        )
        if url:
            text += f"🔗 <a href='{url}'>View on Spotify</a>"

        for chat_id in self.notify_chat_ids:
            await self._send_text(chat_id, text)

    async def send_track_removed(self, track_info: Dict, total_count: int):
        """Sends an alert when a track is removed from the playlist."""
        title = html.escape(track_info.get("title", "Track"))
        artist = html.escape(track_info.get("artist", "Artist"))
        
        text = (
            f"🗑️ <b>Removed from Playlist</b>\n"
            f"Removed <b>{title}</b> by <i>{artist}</i>.\n"
            f"📊 <b>Total in Playlist:</b> {total_count} tracks"
        )
        for chat_id in self.notify_chat_ids:
            await self._send_text(chat_id, text)

    async def send_unsupported_link_alert(self, url: str):
        """Sends an alert when a non-Spotify link is detected."""
        escaped_url = html.escape(url)
        text = (
            f"⚠️ <b>Unsupported Music Link</b>\n"
            f"Detected non-Spotify link: <code>{escaped_url}</code>\n"
            f"<i>Only Spotify track/album links are automatically synced.</i>"
        )
        for chat_id in self.notify_chat_ids:
            await self._send_text(chat_id, text)

    async def send_approval_prompt(self, entity_info: Dict, approval_id: str):
        """Sends an interactive approval prompt with inline buttons to both users."""
        session = await self.get_session()
        entity_type = entity_info.get("type", "Collection").capitalize()
        title = html.escape(entity_info.get("title", "Unknown"))
        artist = html.escape(entity_info.get("artist", ""))
        count = entity_info.get("track_count", 0)
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
                if artwork:
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
                print(f"[Reporter] Failed to send approval prompt to {chat_id}: {e}")

        # Record message IDs in DB for dual-chat update
        self.state_db.create_pending_approval(
            approval_id=approval_id,
            entity_type=entity_info.get("type", "album"),
            entity_id=entity_info.get("entity_id", ""),
            title=entity_info.get("title", ""),
            artist=entity_info.get("artist", ""),
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
        async with session.post(f"{self.api_url}/sendMessage", json=payload) as resp:
            return await resp.json()

    async def _edit_message_caption(self, chat_id: str, message_id: int, new_caption: str):
        session = await self.get_session()
        # Try editing photo caption first, fallback to text edit
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": new_caption,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": []}  # Remove buttons
        }
        async with session.post(f"{self.api_url}/editMessageCaption", json=payload) as resp:
            if resp.status != 200:
                # Try editing as text
                payload_text = {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": new_caption,
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": []}
                }
                await session.post(f"{self.api_url}/editMessageText", json=payload_text)

    # --- Callback Query Polling for Inline Buttons ---

    def start_polling(self, on_approval_callback: Callable):
        """Starts background polling for button clicks."""
        self._approval_callback = on_approval_callback
        self._polling_task = asyncio.create_task(self._poll_updates())

    async def _poll_updates(self):
        offset = 0
        session = await self.get_session()
        print("[Reporter] Started Telegram button callback listener...")
        
        while True:
            try:
                url = f"{self.api_url}/getUpdates?offset={offset}&timeout=20&allowed_updates=[\"callback_query\"]"
                async with session.get(url, timeout=25) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for update in data.get("result", []):
                            offset = update["update_id"] + 1
                            if "callback_query" in update:
                                await self._handle_callback_query(update["callback_query"])
            except asyncio.CancelledError:
                break
            except Exception as e:
                await asyncio.sleep(3)

    async def _handle_callback_query(self, query: Dict):
        session = await self.get_session()
        query_id = query["id"]
        data = query.get("data", "")
        user = query.get("from", {})
        user_name = user.get("first_name", "Someone")
        
        # Acknowledge callback immediately to stop loading spinner in Telegram
        await session.post(f"{self.api_url}/answerCallbackQuery", json={"callback_query_id": query_id})

        if ":" not in data:
            return
        action, approval_id = data.split(":", 1)

        approval = self.state_db.get_pending_approval(approval_id)
        if not approval or approval.get("status") != "pending":
            return

        # Mark as resolved in DB
        status = "approved" if action == "approve" else "dismissed"
        updated = self.state_db.resolve_approval(approval_id, status, user_name)
        if not updated:
            return

        entity_title = html.escape(approval.get("title", "Collection"))
        count = approval.get("track_count", 0)
        
        if action == "approve":
            status_text = f"✅ <b>Approved by {html.escape(user_name)}</b>\nAdding {count} tracks to playlist..."
        else:
            status_text = f"❌ <b>Dismissed by {html.escape(user_name)}</b>"

        new_caption = (
            f"💿 <b>{approval.get('entity_type', 'Collection').capitalize()}:</b> {entity_title}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_text}"
        )

        # Update message in BOTH users' chats
        message_ids = approval.get("message_ids", {})
        for chat_id, msg_id in message_ids.items():
            try:
                await self._edit_message_caption(chat_id, msg_id, new_caption)
            except Exception as e:
                print(f"[Reporter] Error updating message {msg_id} in {chat_id}: {e}")

        # Trigger execution callback
        if self._approval_callback:
            await self._approval_callback(approval, action, user_name)
