import asyncio
import time
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from telethon.errors import FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError

API_ID   = 36461704
API_HASH = '57fd0ec8dc0e2786420c4e78a8d1c5d4'
SESSION  = '1BVtsOGgBu1NtXFxcOfu7w3Tk24TwGKR6CCwV2IBT1j-h8NgDvjUJ1mEYCG7BybItWhceFcQk-H_okPSeyEFyNfgoLfE7cUsT02Xdvz8aqMR3vE9YWljLAm9kfqhH7arzL7daf2v1HP-uWRalFZOWny6vcfSeJNE5epcoj-twS6FmNhY9bNBIQgjs8EnXmXLgKU0iYnO4ouXhCl48GjRgR8qxz_lJmXt_aBNL64aDeW7Bz9-T70xcSOl0GwFqzCrBWbZScnfm-C0li9UlGwHtlFurxf85qUvyykAHrvhZ9Tn12imqWZyMNzwdBrDED8mdvKxg8-P1Ni4_ld3H2_DWUfUzoToLnEc='

DEST = '@razvlecheniyavietnam'
LIMIT = 10

SOURCES = [
    'nyachang_ru', 'T2TNhaTrangEvents', 'nhatrang_tusa_afisha',
    'afisha_nhatrang', 'danang_afisha', 'danangpals', 'nhatrang_affiche',
    'introconcertvn', 'familyday_nt_events', 'hoshimin_afisha',
    'nyachangafisha', 'svoidanang', 'afishaVietnam', 'afisha_vietnama',
    'vietnam_afisha', 'afisha_phuquoc', 'nha_trang_tusa',
    'nhatrang_afisha', 'party_danang',
]

async def main():
    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.connect()
    me = await client.get_me()
    print(f'✅ Авторизован как {me.first_name} (@{me.username})')

    total_sent = 0
    total_failed = 0

    for src in SOURCES:
        print(f'\n📡 Канал @{src}...')
        try:
            messages = await client.get_messages(f'@{src}', limit=LIMIT)
            # Reverse to send in chronological order
            messages = list(reversed(messages))
            sent = 0
            for msg in messages:
                if not msg.text and not msg.media:
                    continue
                try:
                    await client.forward_messages(DEST, msg)
                    sent += 1
                    total_sent += 1
                    print(f'  ✉️  msg_id={msg.id} переслано')
                    await asyncio.sleep(2)
                except FloodWaitError as e:
                    print(f'  ⏳ FloodWait {e.seconds}s, ждём...')
                    await asyncio.sleep(e.seconds + 2)
                    try:
                        await client.forward_messages(DEST, msg)
                        sent += 1
                        total_sent += 1
                    except Exception as e2:
                        print(f'  ❌ Повторная ошибка: {e2}')
                        total_failed += 1
                except Exception as e:
                    print(f'  ❌ Ошибка msg {msg.id}: {e}')
                    total_failed += 1
            print(f'  ✅ @{src}: отправлено {sent}/{len(messages)}')
        except (ChannelPrivateError, UsernameNotOccupiedError) as e:
            print(f'  ⚠️  @{src} недоступен: {e}')
            total_failed += 1
        except Exception as e:
            print(f'  ❌ @{src} ошибка: {e}')
            total_failed += 1
        
        await asyncio.sleep(3)

    await client.disconnect()
    print(f'\n🏁 Готово! Отправлено: {total_sent}, ошибок: {total_failed}')

asyncio.run(main())
