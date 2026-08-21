"""
Telethon Userbot Initial Interactive Login Script
Logs in with your phone number and generates telegram_userbot.session.
"""

import os
import sys
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

async def login_userbot():
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE")
    target_chat = os.getenv("TELEGRAM_TARGET_CHAT")

    if not all([api_id, api_hash, phone]):
        print("❌ Missing TELEGRAM_API_ID, TELEGRAM_API_HASH, or TELEGRAM_PHONE in .env!")
        return

    print("==================================================")
    print("  Telegram Userbot Login (Telethon)")
    print("==================================================")
    print(f"Connecting with Phone: {phone}")
    
    client = TelegramClient("telegram_userbot", int(api_id), api_hash)
    await client.start(phone=phone)
    
    me = await client.get_me()
    print("\n✅ [Telethon] Userbot Logged in successfully!")
    print(f"   👤 Logged in as: {me.first_name} {me.last_name or ''} (@{me.username or 'No Username'}, ID: {me.id})")
    
    # Test resolving target chat
    if target_chat:
        print(f"\n🔍 Testing target chat resolution: '{target_chat}'...")
        try:
            entity = await client.get_entity(target_chat)
            name = getattr(entity, 'title', None) or getattr(entity, 'first_name', target_chat)
            print(f"✅ Target chat resolved: {name} (ID: {entity.id})")
        except Exception as e:
            print(f"⚠️ Target chat note: Could not resolve '{target_chat}' directly ({e}).")
            print("   Make sure the target chat is either Anna's username, her numeric ID, or a phone number in your contacts.")
            
    await client.disconnect()
    print("\n🎉 Telethon session file saved to 'telegram_userbot.session'.")

if __name__ == "__main__":
    asyncio.run(login_userbot())
