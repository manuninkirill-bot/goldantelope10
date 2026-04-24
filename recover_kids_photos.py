#!/usr/bin/env python3
"""
Recover kids listing photos from the Telegram photo storage channel
using the Telethon user account session.
Downloads photos to static/kids_photos/ and updates listings_vietnam.json.
"""
import asyncio
import json
import os
import re
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger(__name__)

API_ID = int(os.environ.get('TELETHON_API_ID', '32881984'))
API_HASH = os.environ.get('TELETHON_API_HASH', '')
SESSION = 'telegram_user_session'
PHOTO_CHANNEL = -1003577636318  # Telethon needs int peer_id

LISTINGS_FILE = 'listings_vietnam.json'
PHOTO_DIR = 'static/kids_photos'
os.makedirs(PHOTO_DIR, exist_ok=True)


def load_listings():
    with open(LISTINGS_FILE, encoding='utf-8') as f:
        return json.load(f)


def save_listings(data):
    tmp = LISTINGS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LISTINGS_FILE)


async def recover():
    from telethon import TelegramClient
    from telethon.tl.types import MessageMediaPhoto

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    logger.info('Telethon connected')

    data = load_listings()
    kids = data.get('kids', [])

    # Build set of IDs that need photo recovery
    need_photo = {}
    for item in kids:
        fid = item.get('telegram_file_id')
        img = item.get('image_url', '') or ''
        # Needs recovery if image_url uses the revoked bot token or is empty
        if fid and ('api.telegram.org/file/bot8058224567' in img or not img):
            need_photo[item['id']] = item

    logger.info(f'Items needing photo recovery: {len(need_photo)}')

    if not need_photo:
        logger.info('Nothing to recover.')
        await client.disconnect()
        return

    # Iterate through the storage channel messages looking for photos
    recovered = 0
    checked = 0

    try:
        async for msg in client.iter_messages(PHOTO_CHANNEL, limit=2000):
            checked += 1
            if not msg.media or not isinstance(msg.media, MessageMediaPhoto):
                continue

            caption = msg.message or ''

            # Try to match by listing ID in caption
            matched_item = None
            for lid, item in list(need_photo.items()):
                # Look for the listing ID or title in the caption
                if lid in caption:
                    matched_item = item
                    match_key = lid
                    break
                title = item.get('title', '') or ''
                if title and title[:30] in caption:
                    matched_item = item
                    match_key = lid
                    break

            if matched_item:
                photo_path = os.path.join(PHOTO_DIR, f'{matched_item["id"]}.jpg')
                try:
                    await client.download_media(msg, file=photo_path)
                    local_url = f'/static/kids_photos/{matched_item["id"]}.jpg'
                    matched_item['image_url'] = local_url
                    matched_item['all_images'] = [local_url]
                    del need_photo[match_key]
                    recovered += 1
                    logger.info(f'  Recovered: {matched_item["id"]} → {local_url}')
                    if recovered % 5 == 0:
                        save_listings(data)
                except Exception as e:
                    logger.warning(f'  Download failed for {matched_item["id"]}: {e}')

            if checked % 100 == 0:
                logger.info(f'  Checked {checked} messages, recovered {recovered}, remaining {len(need_photo)}')

            if not need_photo:
                logger.info('All photos recovered!')
                break

    except Exception as e:
        logger.error(f'Error iterating channel: {e}')

    save_listings(data)
    logger.info(f'Done: {recovered} photos recovered. {len(need_photo)} not matched.')
    if need_photo:
        logger.info(f'Still missing: {list(need_photo.keys())}')

    await client.disconnect()


if __name__ == '__main__':
    asyncio.run(recover())
