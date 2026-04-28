"""
Получаем file_id для всех фото из @bikeparsing_vn через Telethon,
затем через Bot API getFile конвертируем в file_path,
обновляем listings_vietnam.json: tg_file_ids + image_url = /tg_file/<file_id>
"""
import os, json, asyncio, requests, time

TELETHON_API_ID   = int(os.environ.get('TELETHON_API_ID', '32881984'))
TELETHON_API_HASH = os.environ.get('TELETHON_API_HASH', 'd2588f09dfbc5103ef77ef21c07dbf8b')
TELETHON_SESSION  = os.environ.get('TELETHON_SESSION', '')
BOT_TOKEN         = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
CHANNEL           = 'bikeparsing_vn'

async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import MessageMediaPhoto
    from telethon.utils import pack_bot_file_id

    if not TELETHON_SESSION:
        print('TELETHON_SESSION not set')
        return

    client = TelegramClient(StringSession(TELETHON_SESSION), TELETHON_API_ID, TELETHON_API_HASH)
    await client.start()
    print('Telethon connected')

    # Load existing transport data to find which msg_ids we need
    with open('listings_vietnam.json') as f:
        d = json.load(f)
    trans = d.get('transport', [])
    need_ids = {}
    for t in trans:
        rid = t.get('id', '')
        if rid.startswith(f'{CHANNEL}_'):
            try:
                mid = int(rid.split('_')[-1])
                need_ids[mid] = t
            except:
                pass
    print(f'Need file_ids for {len(need_ids)} transport items from @{CHANNEL}')

    # Fetch messages from channel
    entity = await client.get_entity(f'@{CHANNEL}')
    photo_map = {}  # msg_id -> [bot_file_id, ...]

    async for msg in client.iter_messages(entity, limit=None):
        if msg.id not in need_ids:
            continue
        if not msg.media:
            continue
        file_ids = []
        if isinstance(msg.media, MessageMediaPhoto):
            try:
                bot_fid = pack_bot_file_id(msg.media.photo)
                file_ids.append(bot_fid)
            except Exception as e:
                print(f'  pack error msg {msg.id}: {e}')
        # Also check grouped album
        if not file_ids and hasattr(msg, 'grouped_id') and msg.grouped_id:
            pass  # will catch in separate pass
        if file_ids:
            photo_map[msg.id] = file_ids
        if len(photo_map) % 20 == 0 and len(photo_map) > 0:
            print(f'  got {len(photo_map)}/{len(need_ids)} photos...')

    await client.disconnect()
    print(f'Fetched file_ids for {len(photo_map)}/{len(need_ids)} messages')

    # Now resolve file_ids to file_paths via Bot API and update listings
    if not BOT_TOKEN:
        print('No BOT_TOKEN - will store tg_file_ids only (proxy will resolve on demand)')

    # Load or init file_path cache
    cache_file = 'tg_file_paths_cache.json'
    try:
        with open(cache_file) as f:
            fp_cache = json.load(f)
    except:
        fp_cache = {}

    updated = 0
    for t in d['transport']:
        rid = t.get('id', '')
        if not rid.startswith(f'{CHANNEL}_'):
            continue
        try:
            mid = int(rid.split('_')[-1])
        except:
            continue
        if mid not in photo_map:
            continue
        file_ids = photo_map[mid]
        # Store tg_file_ids
        t['tg_file_ids'] = file_ids
        # Resolve to file_paths if bot token available
        images = []
        for fid in file_ids:
            if BOT_TOKEN:
                fp = fp_cache.get(fid)
                if not fp:
                    try:
                        r = requests.get(
                            f'https://api.telegram.org/bot{BOT_TOKEN}/getFile',
                            params={'file_id': fid}, timeout=10
                        )
                        if r.status_code == 200 and r.json().get('ok'):
                            fp = r.json()['result']['file_path']
                            fp_cache[fid] = fp
                        time.sleep(0.05)
                    except Exception as e:
                        print(f'  getFile error {fid}: {e}')
                if fp:
                    images.append(f'https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}')
                    continue
            images.append(f'/tg_file/{fid}')
        if images:
            t['image_url'] = images[0]
            t['all_images'] = images
            t['photos'] = images
            t['has_media'] = True
            updated += 1

    print(f'Updated {updated} transport items')

    # Save cache
    with open(cache_file, 'w') as f:
        json.dump(fp_cache, f, separators=(',', ':'))

    # Save listings
    tmp = 'listings_vietnam.json.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, 'listings_vietnam.json')
    print('Saved listings_vietnam.json')
    print(f'file_paths cached: {len(fp_cache)}')

if __name__ == '__main__':
    asyncio.run(main())
