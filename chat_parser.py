import os
import json
import asyncio
from datetime import datetime
from telethon import TelegramClient

API_ID = int(os.environ.get('TELETHON_API_ID', 0))
API_HASH = os.environ.get('TELETHON_API_HASH', '')

CHAT_CHANNELS = [
    "phuket_ru", "Pkhuket_Chatx", "vmestenaphukete", "phuket_chat1",
    "bangkok_chat_znakomstva", "phangan_chat", "samui_chat", "chiangmai_chat",
    "svoi_thai_chat", "Tailand_chat2",
    "bali_chat", "Bali_chat_official", "chatotgleba", "bali_topchat",
    "kazakhbali", "networkingbali", "CHAT_BALI_REAL_ESTATE", "baly_chat"
]

def is_english_only(text):
    """Проверяет, полностью ли текст на английском"""
    for char in text:
        if ord(char) > 127 and char not in '.,!?-…()[]{}":;/\\ ':
            return False
    return True

def is_spam(text):
    """Проверяет, не является ли объявление спамом/промо"""
    if not text:
        return False
    spam_keywords = [
        'deriv.com', 'synthetic indices', 'trading account',
        'round-the-clock trading', 'forex', 'crypto trading',
        'kumpulan video viral', 'full video', 'join grup', 'klik link',
        'video-info-viral', 'join sekarang',
        'rent account', 'rent linkedin', 'rent facebook', 'make money',
        'passive income', 'rent out', 'advertising account',
        'grow your business', 'promote message', 'promotion packages',
        'reach more customers', 'boost visibility', 'drive engagement',
        'anda ingin sukses', 'ubah cara berfikir', 'positive thinking',
        'salam sukses', 'mulai sebelum orang',
        'notif sms', 'hak cipta hack', 'bootloader', 'fingerprint'
    ]
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in spam_keywords)

async def connect_with_retry(max_retries=3):
    """Подключение с retry логикой (для обхода database is locked)"""
    for attempt in range(max_retries):
        try:
            client = TelegramClient('goldantelope_user', API_ID, API_HASH)
            await client.start()
            return client
        except Exception as e:
            if 'database is locked' in str(e) and attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)  # 2, 4, 8 секунд
                print(f"⏳ База заблокирована, ожидаю {wait_time}сек...")
                await asyncio.sleep(wait_time)
            else:
                raise

async def parse_chats():
    try:
        client = await connect_with_retry()
    except Exception as e:
        print(f"❌ Не удалось подключиться: {str(e)[:100]}")
        return
    
    listings_file = "listings_chat.json"
    existing = []
    if os.path.exists(listings_file):
        try:
            with open(listings_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                existing = data
            elif isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list):
                        existing.extend(v)
        except Exception:
            existing = []

    existing_ids = {item['id'] for item in existing if isinstance(item, dict)}
    existing_texts = {item.get('description', '')[:150] for item in existing if isinstance(item, dict)}
    
    new_items = []
    
    total_skipped = 0
    for channel in CHAT_CHANNELS:
        try:
            entity = await client.get_entity(channel)
            # Берем последние 5 сообщений для более частых обновлений (1 в минуту)
            messages = await client.get_messages(entity, limit=5)
            
            for msg in messages:
                if not msg.text or len(msg.text) < 20:
                    continue
                
                if is_english_only(msg.text):
                    total_skipped += 1
                    continue
                
                if is_spam(msg.text):
                    continue
                
                item_id = f"{channel}_{msg.id}"
                if item_id in existing_ids:
                    continue
                if msg.text[:150] in existing_texts:
                    continue
                
                item = {
                    'id': item_id,
                    'category': 'chat',
                    'title': msg.text[:100],
                    'description': msg.text,
                    'date': msg.date.isoformat(),
                    'source_channel': f"@{channel}",
                    'message_id': msg.id,
                    'has_media': bool(msg.media),
                    'price': None
                }
                new_items.append(item)
            
            channel_count = len([i for i in new_items if i['source_channel'] == f'@{channel}'])
            if channel_count > 0:
                print(f"✓ @{channel}: +{channel_count}")
                # Сохраняем инкрементально после каждого канала
                all_items = existing + new_items
                with open(listings_file, 'w', encoding='utf-8') as f:
                    json.dump(all_items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            error_msg = str(e)[:50]
            if 'database is locked' not in error_msg:
                print(f"⚠️ @{channel}: {error_msg}")
        
        await asyncio.sleep(120)  # 2 минуты задержка между каналами (менее агрессивно)
    
    total_new = len(new_items)
    if total_new:
        all_items = existing + new_items
        with open(listings_file, 'w', encoding='utf-8') as f:
            json.dump(all_items, f, ensure_ascii=False, indent=2)
        print(f"💬 Цикл завершён: добавлено {total_new} новых сообщений")
        if total_skipped > 0:
            print(f"🚫 Отклонено англоязычных: {total_skipped}")
    else:
        print("💬 Новых сообщений нет")
        if total_skipped > 0:
            print(f"🚫 (найдено {total_skipped} англ., но они отклонены)")
    
    try:
        await client.disconnect()
    except:
        pass

if __name__ == '__main__':
    print(f"🔄 Парсинг чатов: {datetime.now().strftime('%H:%M:%S')}")
    asyncio.run(parse_chats())
