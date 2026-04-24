#!/usr/bin/env python3
import os, asyncio, json
from telethon import TelegramClient

API_ID = int(os.environ.get('TELETHON_API_ID', 0))
API_HASH = os.environ.get('TELETHON_API_HASH', '')
PHONE = os.environ.get('TELETHON_PHONE', '')

async def main():
    client = TelegramClient('goldantelope_user', API_ID, API_HASH)
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"ALREADY_AUTH:{me.first_name}")
        await client.disconnect()
        return
    result = await client.send_code_request(PHONE)
    with open('.phone_code_hash', 'w') as f:
        json.dump({'hash': result.phone_code_hash, 'phone': PHONE}, f)
    print("CODE_SENT")
    await client.disconnect()

asyncio.run(main())
