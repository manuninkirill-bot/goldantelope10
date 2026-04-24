import os
import json
import re
import asyncio
import requests
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest

API_ID = int(os.environ.get('TELETHON_API_ID', 0))
API_HASH = os.environ.get('TELETHON_API_HASH', '')

def classify_message(text, channel_category):
    text_lower = text.lower()
    if channel_category and channel_category != 'chat':
        return channel_category
    return 'chat'

def is_english_only(text):
    for char in text:
        if ord(char) > 127 and char not in '.,!?-…()[]{}":;/\\ ':
            return False
    return True

async def parse_channel(client, channel_username, category, limit=25):
    """Parse channel - менее агрессивный режим"""
    listings = []
    skipped_english = 0
    try:
        entity = await client.get_entity(channel_username)
        messages = await client.get_messages(entity, limit=limit)
        for msg in messages:
            if not msg.text or len(msg.text) < 20:
                continue
            if is_english_only(msg.text):
                skipped_english += 1
                continue
            
            if is_spam(msg.text):
                continue
            
            detected_category = classify_message(msg.text, category)
            item = {
                'id': f"{channel_username}_{msg.id}",
                'category': detected_category,
                'title': msg.text[:100],
                'description': msg.text,
                'date': msg.date.isoformat(),
                'source_channel': f"@{channel_username}",
                'message_id': msg.id,
                'image_url': None,
                'image_hash': None,
                'has_media': bool(msg.media),
                'price': None
            }
            listings.append(item)
    except Exception as e:
        pass
    return listings

async def parse_vietnam():
    """Парсер Вьетнама с долгими задержками"""
    print("🇻🇳 Запуск парсера Вьетнама (АГРЕССИВНЫЙ режим)...")
    
    with open('vietnam_channels.json', 'r', encoding='utf-8') as f:
        channels_config = json.load(f)
    
    try:
        client = TelegramClient('goldantelope_user', API_ID, API_HASH)
        await client.connect()
    except:
        return
    
    if not await client.is_user_authorized():
        print("❌ Сессия не авторизована!")
        return
    
    me = await client.get_me()
    print(f"✅ Авторизован как: {me.first_name}")
    
    # Загрузить существующие данные
    existing_data = {}
    existing_ids = set()
    try:
        with open('listings_vietnam.json', 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
            for cat, items in existing_data.items():
                if isinstance(items, list):
                    for item in items:
                        existing_ids.add(item.get('id'))
    except:
        pass
    
    channels_to_parse = []
    for cat_key, channel_list in channels_config.get('channels', {}).items():
        for channel in channel_list:
            channels_to_parse.append((channel, cat_key))
    
    print(f"📋 Найдено {len(channels_to_parse)} каналов")
    print(f"⏱️  Режим: АГРЕССИВНЫЙ (1.5 сек между каналами)")
    print(f"📦 Существующих объявлений: {len(existing_ids)}")
    
    new_count = 0
    total_parsed = 0
    
    for i, (channel, category) in enumerate(channels_to_parse):
        try:
            listings = await parse_channel(client, channel, category, limit=50)
            total_parsed += len(listings)
            
            # Добавить только новые
            for item in listings:
                if item['id'] not in existing_ids:
                    cat = item['category']
                    if cat not in existing_data:
                        existing_data[cat] = []
                    existing_data[cat].insert(0, item)
                    existing_ids.add(item['id'])
                    new_count += 1
            
            if listings:
                print(f"  [{i+1}/{len(channels_to_parse)}] @{channel}: {len(listings)} шт")
        except:
            pass
        
        await asyncio.sleep(1.5)
    
    with open('listings_vietnam.json', 'w', encoding='utf-8') as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)
    
    total_now = sum(len(v) for v in existing_data.values() if isinstance(v, list))
    print(f"")
    print(f"📊 ИТОГО:")
    print(f"   Пропарсено: {total_parsed}")
    print(f"   ✨ НОВЫХ: {new_count}")
    print(f"   📦 Всего в базе: {total_now}")
    
    try:
        await client.disconnect()
    except:
        pass

if __name__ == '__main__':
    print(f"🔄 Auto Parser: {datetime.now().strftime('%H:%M:%S')}")
    print("🔥 РЕЖИМ: Агрессивный (50 сообщений, 1.5 сек)")
    asyncio.run(parse_vietnam())
    print("✅ Завершено!\n")


def is_spam(text):
    """Проверяет, не является ли объявление спамом/промо"""
    if not text:
        return False
    
    spam_keywords = [
        'deriv.com', 'synthetic indices', 'trading account',
        'round-the-clock trading', 'forex', 'crypto trading',
        'click here', 'open account', 'sign up', 'register now',
        'жми сюда', 'заработок', 'быстрый доход', 'гарантированный',
        'скам', 'опасно',
        'kumpulan video viral', 'full video', 'join grup', 'klik link',
        'video-info-viral', 'join sekarang',
        'rent account', 'rent linkedin', 'rent facebook', 'make money',
        'passive income', 'rent out', 'advertising account', 'payment proof',
        'binance usdt', 'grow your business', 'promote message', 'promotion packages',
        'reach more customers', 'boost visibility', 'active groups', 'drive engagement',
        'anda ingin sukses', 'ubah cara berfikir', 'positive thinking', 'pilihan itu selalu ada',
        'salam sukses', 'mulai sebelum orang',
        'notif sms', 'hak cipta hack', 'bootloader', 'fingerprint', 'manufacturer',
        'chat id of this chat'
    ]
    
    text_lower = text.lower()
    for keyword in spam_keywords:
        if keyword in text_lower:
            return True
    return False

