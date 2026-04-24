#!/usr/bin/env python3
import os, asyncio, json, sys
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

API_ID = int(os.environ.get('TELETHON_API_ID', 0))
API_HASH = os.environ.get('TELETHON_API_HASH', '')

async def main():
    code = sys.argv[1] if len(sys.argv) > 1 else ''
    password = sys.argv[2] if len(sys.argv) > 2 else ''

    with open('.phone_code_hash', 'r') as f:
        data = json.load(f)
    phone = data['phone']
    phone_hash = data['hash']

    client = TelegramClient('goldantelope_user', API_ID, API_HASH)
    await client.connect()
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_hash)
    except SessionPasswordNeededError:
        if not password:
            print("2FA_REQUIRED")
            await client.disconnect()
            return
        await client.sign_in(password=password)
    me = await client.get_me()
    print(f"SUCCESS:{me.first_name}:{me.username}")
    await client.disconnect()

asyncio.run(main())
