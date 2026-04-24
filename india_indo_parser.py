"""
Background Telethon poller for India and Indonesia real estate groups.
Runs in a separate thread, polls groups every POLL_INTERVAL seconds,
downloads photos and saves listings to listings_india.json / listings_indonesia.json
"""

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger('india_indo_parser')
logging.basicConfig(level=logging.INFO)

# ── Telegram credentials (same account as HuggingFace Space) ────────────────
API_ID   = 34174007
API_HASH = 'b8b86f94083feb5ccbdf3c6672bc81b9'
SESSION  = '1BVtsOIQBu7Ccq4Z2upImnI504-Xu7QHRcTsBaMaYZwoYDDV_4o8O-gkV1ceZsOVWG6I7B1p6fQmhhLRcGG9bLbwfJLPZ7r0clsa75NvCRb8K92Rrvizd4ueDQ8soOY5JS8bCG-FHKXYtsSc-6MAdRJQ_KvelgjmDnOQSHAObhjZRYBQitE5v1SfNjqvu9PcYddSt_D0SgFvqBLYCJ9dXCmMkF6pNyVPY0FO2y7ndZOnlNknJMW4Pf9emN8VkNzjxODcvG-3c1LA_24jIttSUTykUlYvMHVrVwSptBEgXCic-Oc8vmZeoBxFFHiEJ16T-3-pPeldoI67Vmz51DIJRn-k4ui2sVkg='

POLL_INTERVAL = 600   # 10 minutes
PHOTOS_DIR    = 'static/channel_photos'
MSGS_PER_GROUP = 50   # fetch last N messages per group, take up to 10 with photo

GROUPS = {
    'india': {
        'chats': [
            'goa_arendaa', 'goa_rent_house', 'arenda_goa_gub',
            'goaRentAll', 'GoaRentl', 'myflats', 'goa_siolim_realty',
            'HousingBangalore', 'Arenda_Zhilya_Indiya', 'goa_appart',
            'House_for_rent_Goa', 'arendagoaarambol', 'homegoa',
        ],
        'file': 'listings_india.json',
        'default_city': 'Гоа',
    },
    'indonesia': {
        'chats': [
            'CHAT_BALI_REAL_ESTATE', 'bali_appart', 'onerealestatebali',
            'bali_arenda1', 'estetico_estate', 'kvartira_bali', 'rentbali_villa',
        ],
        'file': 'listings_indonesia.json',
        'default_city': 'Бали',
    },
}

EMOJI_RE = re.compile(
    "[\U00010000-\U0010ffff"
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\u2600-\u26FF\u2700-\u27BF"
    "]+", flags=re.UNICODE
)

status = {
    'running': False,
    'last_poll': None,
    'india_count': 0,
    'indonesia_count': 0,
    'errors': 0,
    'phase': 'idle',
}


def clean_text(text):
    if not text:
        return ''
    text = EMOJI_RE.sub('', text)
    text = re.sub(r'http\S+|@\S+|t\.me/\S+|#\S+', '', text, flags=re.I)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text


def extract_price(text, country='india'):
    if not text:
        return 0, ''
    currency_label = 'INR' if country == 'india' else 'IDR'
    # Match number followed by currency keywords
    m = re.search(
        r'(\d[\d\s,.]*)[\s]*(?:тыс\.?|тысяч|000)?\s*(?:руп|rупи|rupee|inr|idr|₹|\$|usd)',
        text, re.I
    )
    if not m:
        # Try: "15 000 / месяц" or "15 000 рупий"
        m = re.search(r'(\d[\d\s]{2,})\s*(?:/\s*мес|рупи|руп|руб)', text, re.I)
    if m:
        num_str = re.sub(r'[\s,.]', '', m.group(1))
        try:
            price = int(num_str)
            if 1000 <= price <= 100_000_000:
                formatted = f'{price:,}'.replace(',', ' ')
                return price, f'{formatted} {currency_label}'
        except:
            pass
    return 0, ''


def load_json(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {
            'restaurants': [], 'tours': [], 'entertainment': [],
            'transport': [], 'real_estate': [], 'exchange': [],
            'kids': [], 'visas': [], 'marketplace': [],
            'photoshoots': [], 'medicine': [], 'chat': []
        }


def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_existing_ids(data):
    ids = set()
    for item in data.get('real_estate', []):
        ids.add(item.get('id', ''))
    return ids


async def download_photo(client, msg):
    buf = io.BytesIO()
    await client.download_media(msg.media, file=buf)
    buf.seek(0)
    return buf.read()


def save_photo(data_bytes, source, msg_id):
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    filename = f'{source}_{msg_id}.jpg'
    path = os.path.join(PHOTOS_DIR, filename)
    with open(path, 'wb') as f:
        f.write(data_bytes)
    return f'/static/channel_photos/{filename}'


def build_listing(msg, source_group, country, default_city, photo_url, all_photos):
    raw_text = msg.text or msg.message or ''
    cleaned = clean_text(raw_text)
    price, price_display = extract_price(raw_text, country=country)

    msg_id = msg.id
    listing_id = f'{source_group}_{msg_id}'
    date_str = msg.date.isoformat() if msg.date else datetime.now(timezone.utc).isoformat()

    title = cleaned[:80] + ('...' if len(cleaned) > 80 else '') if cleaned else f'Объявление @{source_group}'

    city_map = {
        'india':     {'city': default_city, 'city_ru': default_city},
        'indonesia': {'city': default_city, 'city_ru': default_city},
    }

    return {
        'id':            listing_id,
        'title':         title,
        'description':   cleaned,
        'text':          raw_text,
        'price':         price,
        'price_display': price_display,
        'city':          city_map[country]['city'],
        'city_ru':       city_map[country]['city_ru'],
        'date':          date_str,
        'contact':       f'@{source_group}',
        'contact_name':  source_group,
        'source_group':  source_group,
        'telegram':      f'https://t.me/{source_group}',
        'telegram_link': f'https://t.me/{source_group}/{msg_id}',
        'image_url':     photo_url,
        'all_images':    all_photos,
        'photos':        all_photos,
        'status':        'active',
        'country':       country,
        'message_id':    msg_id,
        'has_media':     True,
        'category':      'real_estate',
    }


async def poll_once(client):
    from telethon.tl.types import MessageMediaPhoto
    from telethon.tl.functions.channels import JoinChannelRequest

    for country, cfg in GROUPS.items():
        filepath = cfg['file']
        data = load_json(filepath)
        existing_ids = get_existing_ids(data)
        new_listings = []

        for chat in cfg['chats']:
            try:
                status['phase'] = f'Polling @{chat} ({country})'
                await client(JoinChannelRequest(chat))
                await asyncio.sleep(1)

                msgs = await client.get_messages(chat, limit=MSGS_PER_GROUP)
                photo_msgs = [m for m in msgs if isinstance(getattr(m, 'media', None), MessageMediaPhoto)][:10]

                # Group albums by grouped_id
                album_groups = {}
                singles = []
                for m in reversed(photo_msgs):
                    if m.grouped_id:
                        album_groups.setdefault(m.grouped_id, []).append(m)
                    else:
                        singles.append(m)

                # Process albums
                for gid, album_msgs in album_groups.items():
                    first = album_msgs[0]
                    lid = f'{chat}_{first.id}'
                    if lid in existing_ids:
                        continue

                    all_photos = []
                    primary_url = ''
                    for i, m in enumerate(album_msgs):
                        try:
                            photo_bytes = await download_photo(client, m)
                            url = save_photo(photo_bytes, chat, m.id)
                            all_photos.append(url)
                            if i == 0:
                                primary_url = url
                        except Exception as e:
                            logger.warning(f'Photo download fail {chat}/{m.id}: {e}')

                    if not primary_url:
                        continue

                    listing = build_listing(first, chat, country, cfg['default_city'], primary_url, all_photos)
                    new_listings.append(listing)
                    existing_ids.add(lid)
                    await asyncio.sleep(0.5)

                # Process single photos
                for m in singles:
                    lid = f'{chat}_{m.id}'
                    if lid in existing_ids:
                        continue

                    try:
                        photo_bytes = await download_photo(client, m)
                        url = save_photo(photo_bytes, chat, m.id)
                    except Exception as e:
                        logger.warning(f'Photo download fail {chat}/{m.id}: {e}')
                        continue

                    listing = build_listing(m, chat, country, cfg['default_city'], url, [url])
                    new_listings.append(listing)
                    existing_ids.add(lid)
                    await asyncio.sleep(0.5)

                if new_listings:
                    logger.info(f'[{country}] @{chat}: +{len(new_listings)} new listings so far')

                await asyncio.sleep(2)

            except Exception as e:
                logger.warning(f'[{country}] @{chat}: {e}')
                status['errors'] += 1
                await asyncio.sleep(2)

        if new_listings:
            # Prepend new listings, keep max 500
            data['real_estate'] = new_listings + data['real_estate']
            data['real_estate'] = data['real_estate'][:500]
            save_json(filepath, data)
            cnt = len(new_listings)
            status[f'{country}_count'] += cnt
            logger.info(f'[{country}] Saved {cnt} new real estate listings')

            # Invalidate Flask data cache
            try:
                from app import data_cache
                data_cache.pop(country, None)
            except Exception:
                pass


async def run_loop():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    status['running'] = True
    status['phase'] = 'Connecting...'
    logger.info('India/Indo parser: connecting to Telegram...')

    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH,
                            system_version='4.16.30-vxCUSTOM')
    await client.start()
    me = await client.get_me()
    logger.info(f'India/Indo parser connected as {me.phone}')
    status['phase'] = 'running'

    while True:
        try:
            logger.info('India/Indo parser: starting poll cycle...')
            await poll_once(client)
            status['last_poll'] = datetime.now(timezone.utc).isoformat()
            logger.info(f'India/Indo parser: poll done. India={status["india_count"]}, Indo={status["indonesia_count"]}')
        except Exception as e:
            logger.error(f'India/Indo parser poll error: {e}')
            status['errors'] += 1

        status['phase'] = f'sleeping {POLL_INTERVAL}s'
        await asyncio.sleep(POLL_INTERVAL)


def start_parser():
    """Call this from app.py to start the background parser thread."""
    def _thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_loop())
        except Exception as e:
            logger.error(f'India/Indo parser thread crashed: {e}')
            status['running'] = False
            status['phase'] = f'crashed: {e}'

    t = threading.Thread(target=_thread, daemon=True, name='india_indo_parser')
    t.start()
    logger.info('India/Indo parser thread started')
    return t
