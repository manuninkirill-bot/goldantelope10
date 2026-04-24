import json
import os
import re
import time
import requests

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHANNEL = '@restoranvietnam'
PROGRESS_FILE = 'post_progress.json'
DELAY = 5  # seconds between posts


def clean_title(title):
    t = re.sub(r'^[\U0001F300-\U0001FFFF\u2600-\u26FF\u2700-\u27BF\s]+', '', title)
    t = re.sub(r'^РЕСТОРАН:\s*', '', t)
    t = re.sub(r'^НАЗВАНИЕ:\s*', '', t)
    t = re.sub(r'\s*сапфир.*', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\[.*?\]|\(.*?\)', '', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t


def load_restaurants():
    with open('listings_vietnam.json', encoding='utf-8') as f:
        data = json.load(f)
    result = []
    for item in data['restaurants']:
        if item['title'] == 'Channel created':
            continue
        desc = item.get('description', '')
        if len(desc) < 80:
            continue
        photos = item.get('photos') or item.get('images') or []
        if not photos:
            continue
        result.append({
            'id': item['id'],
            'title': clean_title(item['title']),
            'description': desc,
            'photos': photos[:10],
        })
    return result


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {'posted_ids': [], 'tg_data': {}}


def save_progress(progress):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def download_photo(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        r = requests.get(url, timeout=25, headers=headers)
        if r.status_code == 200 and len(r.content) > 1000:
            return r.content
        print(f"    Download failed: status={r.status_code} size={len(r.content)}")
    except Exception as e:
        print(f"    Download error: {e}")
    return None


def send_tg_request(method, data=None, files=None):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/{method}'
    for attempt in range(3):
        try:
            if files:
                r = requests.post(url, data=data, files=files, timeout=60)
            else:
                r = requests.post(url, json=data, timeout=30)
            result = r.json()
            if result.get('ok'):
                return result
            err = result.get('description', '')
            # FloodWait
            if 'Too Many Requests' in err:
                m = re.search(r'(\d+)', err)
                wait = int(m.group(1)) + 5 if m else 40
                print(f"    FloodWait {wait}s...")
                time.sleep(wait)
                continue
            print(f"    TG error ({attempt+1}/3): {err}")
            time.sleep(3)
        except Exception as e:
            print(f"    Request error ({attempt+1}/3): {e}")
            time.sleep(5)
    return None


def post_restaurant(restaurant):
    """Post restaurant album to channel. Returns dict with message_id and file_ids, or None."""
    title = restaurant['title']
    caption = f"<b>🍽 {title}</b>\n\n{restaurant['description']}"
    photo_urls = restaurant['photos']

    # Download photos one by one to save memory
    imgs = []
    for url in photo_urls:
        img = download_photo(url)
        if img:
            imgs.append(img)
        time.sleep(0.3)

    if not imgs:
        print(f"  ✗ No photos downloaded")
        return None

    print(f"  Downloaded {len(imgs)}/{len(photo_urls)} photos", flush=True)

    if len(imgs) == 1:
        # Single photo
        result = send_tg_request('sendPhoto', data={
            'chat_id': CHANNEL,
            'caption': caption[:1024],
            'parse_mode': 'HTML'
        }, files={'photo': ('photo.jpg', imgs[0], 'image/jpeg')})
        imgs.clear()
        if result:
            msg = result['result']
            photo = msg.get('photo', [])
            file_id = max(photo, key=lambda x: x.get('file_size', 0))['file_id'] if photo else None
            return {'message_id': msg['message_id'], 'file_ids': [file_id] if file_id else []}
    else:
        # Media group
        files = {}
        media = []
        for i, img_bytes in enumerate(imgs):
            k = f'photo{i}'
            files[k] = (f'{k}.jpg', img_bytes, 'image/jpeg')
            entry = {'type': 'photo', 'media': f'attach://{k}'}
            if i == 0:
                entry['caption'] = caption[:1024]
                entry['parse_mode'] = 'HTML'
            media.append(entry)

        result = send_tg_request('sendMediaGroup', data={
            'chat_id': CHANNEL,
            'media': json.dumps(media)
        }, files=files)
        imgs.clear()
        files.clear()

        if result:
            messages = result['result']
            file_ids = []
            first_msg_id = messages[0]['message_id'] if messages else None
            for msg in messages:
                photo = msg.get('photo', [])
                if photo:
                    fid = max(photo, key=lambda x: x.get('file_size', 0))['file_id']
                    file_ids.append(fid)
            return {'message_id': first_msg_id, 'file_ids': file_ids}

    return None


def update_json_with_tg_data(tg_data):
    """Update listings_vietnam.json with Telegram file_ids and message links."""
    with open('listings_vietnam.json', encoding='utf-8') as f:
        data = json.load(f)

    updated = 0
    for item in data['restaurants']:
        rid = item.get('id')
        if rid not in tg_data:
            continue
        info = tg_data[rid]
        msg_id = info.get('message_id')
        file_ids = info.get('file_ids', [])

        # Set telegram link to the channel post
        if msg_id:
            item['telegram_link'] = f'https://t.me/restoranvietnam/{msg_id}'

        # Update photos to use Telegram file_ids (stored as tg:// for API retrieval)
        if file_ids:
            item['tg_file_ids'] = file_ids
            # Keep GitHub as fallback in photos, primary image from TG
            item['image_url'] = f'tg://{file_ids[0]}'

        updated += 1

    with open('listings_vietnam.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Sync to listings_data.json
    with open('listings_data.json', encoding='utf-8') as f:
        main_data = json.load(f)

    vn_by_id = {r['id']: r for r in data['restaurants']}
    for r in main_data['vietnam']['restaurants']:
        rid = r.get('id')
        if rid and rid in vn_by_id and rid in tg_data:
            src = vn_by_id[rid]
            r['telegram_link'] = src.get('telegram_link', r.get('telegram_link'))
            if src.get('tg_file_ids'):
                r['tg_file_ids'] = src['tg_file_ids']
                r['image_url'] = src.get('image_url', r.get('image_url'))

    with open('listings_data.json', 'w', encoding='utf-8') as f:
        json.dump(main_data, f, ensure_ascii=False, indent=2)

    print(f"Updated {updated} restaurants in JSON files")


def main():
    if not BOT_TOKEN:
        print('ERROR: TELEGRAM_BOT_TOKEN not set')
        return

    restaurants = load_restaurants()
    progress = load_progress()
    posted_ids = set(progress['posted_ids'])
    tg_data = progress.get('tg_data', {})

    to_post = [r for r in restaurants if r['id'] not in posted_ids]
    print(f'Total: {len(restaurants)} | Already posted: {len(posted_ids)} | To post: {len(to_post)}', flush=True)

    for i, r in enumerate(to_post):
        print(f"[{i+1}/{len(to_post)}] {r['title'][:50]}  ({len(r['photos'])} photos)", flush=True)

        info = post_restaurant(r)
        if info:
            posted_ids.add(r['id'])
            tg_data[r['id']] = info
            progress['posted_ids'] = list(posted_ids)
            progress['tg_data'] = tg_data
            save_progress(progress)
            print(f"  ✓ OK  msg_id={info.get('message_id')}  files={len(info.get('file_ids', []))}", flush=True)
        else:
            print(f"  → Skipped (no result)", flush=True)

        time.sleep(DELAY)

    # After all posts — update JSON with Telegram data
    print(f"\nAll posted. Updating JSON with TG links...", flush=True)
    update_json_with_tg_data(tg_data)
    print(f"Done! Total posted: {len(posted_ids)}")


if __name__ == '__main__':
    main()
