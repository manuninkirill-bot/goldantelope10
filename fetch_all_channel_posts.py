"""
Массовый фетчер постов из каналов недвижимости.
Использует проверенный web-scraper (t.me/s/) — фото = прямые CDN telesco.pe ссылки.
Браузер загружает напрямую, без серверного скачивания.

Использование:
    python fetch_all_channel_posts.py [--country vietnam|thailand|both] [--pages 5]
"""
import json, os, time, sys, argparse
from datetime import datetime, timezone

# Используем проверенный парсер
from bot_channel_parser import scrape_channel_page

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()

# ── Каналы ──────────────────────────────────────────────────────────────────
VN_CHANNELS = [
    'nychang_arenda', 'danang_arenda', 'viet_life_niachang',
    'danangrentaflat', 'hcmc_arenda', 'hanoi_rent', 'rent_nha_trang',
    'arenda_v_danang', 'tanrealtorgh', 'arenda_v_nyachang',
    'arenda_nyachang_zhilye', 'vietnam_arenda', 'nhatrang_luxury',
    'luckyhome_nhatrang', 'nhatrangapartment', 'nhatrang_rental',
]
TH_CHANNELS = [
    'nedvizhimost_pattaya', 'pattaya_realty_estate', 'arenda_pattaya',
    'phuketsk_arenda', 'arenda_phukets', 'phuket_rentas',
    'phuket_nedvizhimost_rent', 'phuketsk_for_rent',
]

CITY_MAP_VN = {
    'nhatrang':  ['нячанг', 'nha trang', 'nhatrang', 'камрань', 'cam ranh', 'bắc nha trang'],
    'hochiminh': ['хошимин', 'сайгон', 'saigon', 'ho chi minh', 'hcm'],
    'danang':    ['дананг', 'da nang', 'danang'],
    'hanoi':     ['ханой', 'hanoi', 'ha noi'],
    'phuquoc':   ['фукуок', 'phu quoc', 'phuquoc'],
    'dalat':     ['далат', 'da lat', 'dalat'],
    'muine':     ['муйне', 'mui ne'],
    'hoian':     ['хойан', 'hoi an'],
}
CITY_MAP_TH = {
    'pattaya':   ['паттайя', 'pattaya', 'wongamat', 'jomtien'],
    'phuket':    ['пхукет', 'phuket', 'rawai', 'patong', 'karon', 'kata'],
    'bangkok':   ['бангкок', 'bangkok'],
    'samui':     ['самуи', 'samui', 'ko samui'],
    'chiangmai': ['чиангмай', 'chiang mai'],
}

def detect_city(text: str, country: str) -> str:
    t = text.lower()
    city_map = CITY_MAP_VN if country == 'vietnam' else CITY_MAP_TH
    for slug, keywords in city_map.items():
        if any(kw in t for kw in keywords):
            return slug
    return ''

SPAM_KEYWORDS = [
    'high-roller', 'likesyou', 'casino', 'казино', '18+',
    'работа для молодых', 'пpибыльнaя', 'ρaҕoτa', 'поднял', 'рекорд',
    'впн', 'vpn', 'заработок', 'пассивный доход', 'зарабатывай',
    'извиняюсь что не по теме', 'есть работа', 'предлагаю работу',
]


def is_spam(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in SPAM_KEYWORDS)


def make_listing(channel: str, msg_id: int, post: dict, country: str) -> dict:
    text = post.get('text', '') or ''
    photos = post.get('photos') or []
    # Определяем источник (если пересланный пост)
    src_ch = post.get('src_ch') or channel
    src_id = post.get('src_id') or msg_id
    return {
        'id': f'{src_ch}_{src_id}',
        'title': text[:120].replace('\n', ' ').strip() or f'Пост {src_id}',
        'description': text,
        'text': text,
        'price': 0,
        'price_display': '',
        'city': 'Вьетнам' if country == 'vietnam' else 'Таиланд',
        'city_ru': 'Вьетнам' if country == 'vietnam' else 'Таиланд',
        'realestate_city': detect_city(text, country),
        'date': post.get('date') or datetime.now(timezone.utc).isoformat(),
        'contact': f'@{src_ch}',
        'contact_name': src_ch,
        'source_group': src_ch,
        'source_channel': channel,
        'telegram': f'https://t.me/{src_ch}',
        'telegram_link': f'https://t.me/{src_ch}/{src_id}',
        'image_url': photos[0] if photos else '',
        'all_images': photos,
        'photos': photos,
        'has_media': bool(photos),
        'status': 'active',
        'country': country,
        'message_id': src_id,
        'category': 'real_estate',
    }


def fetch_channel(channel: str, country: str, pages: int,
                  existing_ids: set) -> list[dict]:
    """Получает до pages страниц постов из канала. Возвращает новые листинги."""
    new_items = []
    before_id = None
    skipped_spam = skipped_no_photo = skipped_dup = 0

    for page_n in range(pages):
        posts = scrape_channel_page(channel, before=before_id)
        if not posts:
            break

        for msg_id, post in sorted(posts.items(), reverse=True):
            text = post.get('text', '') or ''
            photos = post.get('photos') or []

            # Определяем id для проверки дублей
            src_ch = post.get('src_ch') or channel
            src_id = post.get('src_id') or msg_id
            lid = f'{src_ch}_{src_id}'

            if lid in existing_ids:
                skipped_dup += 1
                continue
            if is_spam(text):
                skipped_spam += 1
                continue
            if not photos:
                skipped_no_photo += 1
                continue

            existing_ids.add(lid)
            new_items.append(make_listing(channel, msg_id, post, country))

        if posts:
            before_id = min(posts.keys()) - 1
        time.sleep(0.5)

    print(f"  [{channel}] стр={page_n+1} | "
          f"новых={len(new_items)} дубл={skipped_dup} "
          f"без_фото={skipped_no_photo} спам={skipped_spam}")
    return new_items


def run(country: str, pages: int):
    file_map = {
        'vietnam': 'listings_vietnam.json',
        'thailand': 'listings_thailand.json',
    }
    fname = file_map[country]
    channels = VN_CHANNELS if country == 'vietnam' else TH_CHANNELS

    with open(fname, encoding='utf-8') as f:
        data = json.load(f)

    existing = data.get('real_estate', [])
    existing_ids = {l['id'] for l in existing}
    before_count = len(existing)

    print(f"\n{'='*58}")
    print(f"Фетчинг {country.upper()} | {len(channels)} каналов | до {pages} стр каждый")
    print(f"Уже в базе: {before_count} объявлений")
    print('='*58)

    all_new = []
    for ch in channels:
        items = fetch_channel(ch, country, pages, existing_ids)
        all_new.extend(items)

    # Вставляем новые в начало списка (новые первыми)
    data['real_estate'] = all_new + existing

    with open(fname, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ {country}: {before_count} → {len(data['real_estate'])} (+{len(all_new)} новых)")
    return len(all_new)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--country', choices=['vietnam', 'thailand', 'both'], default='both')
    parser.add_argument('--pages', type=int, default=5,
                        help='Страниц на канал (~20 постов/страница)')
    args = parser.parse_args()

    countries = ['vietnam', 'thailand'] if args.country == 'both' else [args.country]
    total = 0
    for c in countries:
        total += run(c, args.pages)
    print(f"\nВсего добавлено: {total} новых объявлений")
