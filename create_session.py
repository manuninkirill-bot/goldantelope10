#!/usr/bin/env python3
"""
Запустите этот скрипт в Shell-терминале Replit:
  python create_session.py

Он отправит код подтверждения на ваш Telegram и создаст
файл goldantelope_user.session для работы чат-парсера.
"""
import os
import asyncio
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

API_ID = int(os.environ.get('TELETHON_API_ID', 0))
API_HASH = os.environ.get('TELETHON_API_HASH', '')
PHONE = os.environ.get('TELETHON_PHONE', '')

async def main():
    if not API_ID or not API_HASH:
        print("❌ TELETHON_API_ID и TELETHON_API_HASH не заданы!")
        return

    if not PHONE:
        print("❌ TELETHON_PHONE не задан!")
        return

    print(f"📱 Создание сессии для номера: {PHONE}")
    print(f"🔑 API ID: {API_ID}")

    client = TelegramClient('goldantelope_user', API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"✅ Уже авторизован как: {me.first_name} (@{me.username})")
        await client.disconnect()
        return

    await client.send_code_request(PHONE)
    print("📨 Код подтверждения отправлен в Telegram!")

    code = input("Введите код из Telegram: ").strip()

    try:
        await client.sign_in(PHONE, code)
    except SessionPasswordNeededError:
        password = input("Введите пароль двухфакторной аутентификации: ").strip()
        await client.sign_in(password=password)

    me = await client.get_me()
    print(f"✅ Авторизован как: {me.first_name} (@{me.username})")
    print(f"✅ Файл goldantelope_user.session создан!")
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
