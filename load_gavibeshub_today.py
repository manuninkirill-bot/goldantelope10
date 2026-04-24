"""
Загружает посты из @media_vn в entertainment (Vietnam).
Использует Bot API для получения полного текста + og:image CDN URL для фото.
"""
import os, re, json, time, requests
from datetime import datetime, timezone
from html import unescape

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHANNEL = 'media_vn'
LISTINGS_FILE = 'listings_vietnam.json'
INDEX_FILE = 'file_id_index.json'

HEADERS = {'User-Agent': 'TelegramBot (like TwitterBot)'}


def scrape_post(msg_id: int) -> dict | None:
    """Скрейпит мета-данные одного поста через og: теги."""
    try:
        r = requests.get(f'https://t.me/{CHANNEL}/{msg_id}', headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        html = r.text
        title_m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
        desc_m  = re.search(r'<meta property="og:description" content="([^"]*)"', html)
        img_m   = re.search(r'<meta property="og:image" content="([^"]*)"', html)
        title = unescape(title_m.group(1)) if title_m else ''
        desc  = unescape(desc_m.group(1))  if desc_m  else ''
        img   = img_m.group(1)             if img_m   else ''

        if not desc or 'You can view and join' in desc:
            return None

        return {'id': msg_id, 'title': title, 'desc': desc, 'img': img}
    except Exception as e:
        print(f'  scrape_post({msg_id}) error: {e}')
        return None


def get_file_id_via_bot(msg_id: int) -> str | None:
    """Получает file_id фото через Bot API (copyMessage -> temporary storage).
    Использует getUpdates с конкретным message_id — не работает напрямую,
    поэтому возвращаем None и полагаемся на CDN URL."""
    return None


def load_existing_ids() -> set:
    """Возвращает множество уже имеющихся ID постов из entertainment."""
    try:
        with open(LISTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        ent = data.get('entertainment', [])
        ids = set()
        for it in ent:
            if isinstance(it, dict):
                # Учитываем оба поля
                if it.get('listing_id'):
                    ids.add(it['listing_id'])
                if it.get('id'):
                    ids.add(it['id'])
                mid = it.get('message_id')
                if mid and it.get('source_group','').lower() == 'media_vn':
                    ids.add(f'media_vn_{mid}')
        return ids
    except Exception:
        return set()


def build_listing(post: dict) -> dict:
    """Строит объект листинга из данных поста."""
    msg_id = post['id']
    text = post['desc']
    img_url = post['img']

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    title = lines[0][:120] if lines else 'Развлечение'
    description = '\n'.join(lines[1:]) if len(lines) > 1 else text

    # Определяем город из текста
    city = 'Вьетнам'
    text_lower = text.lower()
    if any(k in text_lower for k in ['нячанг', 'nha trang', 'nhatrang', 'нячан']):
        city = 'Нячанг'
    elif any(k in text_lower for k in ['дананг', 'da nang', 'danang']):
        city = 'Дананг'
    elif any(k in text_lower for k in ['хошимин', 'ho chi minh', 'saigon', 'сайгон']):
        city = 'Хошимин'
    elif any(k in text_lower for k in ['ханой', 'ha noi', 'hanoi']):
        city = 'Ханой'
    elif any(k in text_lower for k in ['фукуок', 'phu quoc', 'фу куок']):
        city = 'Фукуок'
    elif any(k in text_lower for k in ['хойан', 'hoi an', 'hoian']):
        city = 'Хойан'
    elif any(k in text_lower for k in ['далат', 'da lat', 'dalat']):
        city = 'Далат'

    # Фото: используем CDN URL напрямую (или прокси /tg_img/)
    photos = []
    if img_url:
        # Приоритет: прокси /tg_img/ если есть file_id в индексе
        idx = {}
        try:
            with open(INDEX_FILE, 'r') as f:
                idx = json.load(f)
        except Exception:
            pass
        if f'media_vn_{msg_id}' in idx:
            photos = [f'/tg_img/media_vn/{msg_id}']
        else:
            # Используем CDN URL напрямую
            photos = [img_url]

    tg_link = f'https://t.me/{CHANNEL}/{msg_id}'
    now = datetime.now(timezone.utc).isoformat()

    return {
        'id': f'media_vn_{msg_id}',
        'listing_id': f'media_vn_{msg_id}',
        'title': title,
        'description': description,
        'text': text,
        'price': 0,
        'price_display': '',
        'city': city,
        'city_ru': city,
        'date': now,
        'contact': f'@{CHANNEL}',
        'contact_name': CHANNEL,
        'source_group': CHANNEL,
        'source_channel': CHANNEL,
        'telegram': f'https://t.me/{CHANNEL}',
        'telegram_link': tg_link,
        'image_url': photos[0] if photos else '',
        'all_images': photos,
        'photos': photos,
        'status': 'active',
        'country': 'vietnam',
        'message_id': msg_id,
        'has_media': bool(photos),
        'category': 'entertainment',
        'listing_type': 'entertainment',
    }


def add_to_listings(item: dict) -> bool:
    """Атомарно добавляет листинг в listings_vietnam.json."""
    try:
        with open(LISTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = {}

    if 'entertainment' not in data:
        data['entertainment'] = []

    ent = data['entertainment']
    existing_ids = set()
    for it in ent:
        if isinstance(it, dict):
            if it.get('listing_id'):
                existing_ids.add(it['listing_id'])
            if it.get('id'):
                existing_ids.add(it['id'])

    if item['listing_id'] in existing_ids or item['id'] in existing_ids:
        return False

    ent.insert(0, item)
    data['entertainment'] = ent

    tmp = LISTINGS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LISTINGS_FILE)
    return True


def main():
    if not BOT_TOKEN:
        print('ERROR: TELEGRAM_BOT_TOKEN not set')
        return

    print(f'Loading today\'s posts from @{CHANNEL}...')
    existing_ids = load_existing_ids()
    print(f'Existing listing IDs count: {len(existing_ids)}')

    # Сканируем диапазон ID (8-80 достаточно для сегодня)
    added = 0
    skipped_duplicate = 0
    skipped_no_content = 0

    for msg_id in range(8, 81):
        item_id = f'media_vn_{msg_id}'
        if item_id in existing_ids:
            skipped_duplicate += 1
            continue

        post = scrape_post(msg_id)
        if not post:
            skipped_no_content += 1
            time.sleep(0.2)
            continue

        listing = build_listing(post)
        success = add_to_listings(listing)
        if success:
            added += 1
            print(f'  + [#{msg_id}] {listing["city"]} | {listing["title"][:60]}')
        else:
            skipped_duplicate += 1

        time.sleep(0.5)

    print(f'\nДобавлено: {added}')
    print(f'Пропущено (дубли): {skipped_duplicate}')
    print(f'Пропущено (нет контента): {skipped_no_content}')


if __name__ == '__main__':
    main()
