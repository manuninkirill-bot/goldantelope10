"""
Парсер 9 каналов через t.me/s/ (публичный веб-просмотрщик) + Bot API.
Получает посты с ПРЯМЫМИ CDN-ссылками на фото (браузер грузит напрямую, без серверного скачивания).

Запуск:
    python bot_channel_parser.py

Затем загрузить на HF Space:
    python push_to_hf.py
"""

import os, re, json, time, logging, requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')

# ─── Список каналов ────────────────────────────────────────────────
CHANNELS = [
    # (username, channel_id, category, target_file, limit)
    ('vietnamparsing',  -1003693840816, 'real_estate',    'listings_vietnam.json',  2000),
    ('thailandparsing', -1003897335333, 'real_estate',    'listings_thailand.json', 2000),
    ('visarun_vn',      -1003660400331, 'visas',          'listings_vietnam.json',  None),
    ('paymens_vn',      -1003774177042, 'money_exchange', 'listings_vietnam.json',  None),
    ('baykivietnam',    -1003675974940, 'transport',      'listings_vietnam.json',  None),
    ('GAtours_vn',      -1003807018167, 'tours',          'listings_vietnam.json',  None),
    ('vibeshub_vn',     -1003733304010, 'entertainment',  'listings_vietnam.json',  None),
    ('restoranvietnam', -1003828019481, 'restaurants',    'listings_vietnam.json',  None),
    # media_vn — только баннеры, не трогаем listings
    ('media_vn',        -1003821326509, 'banners',        None,                     None),
]

# ─── Скрапинг t.me/s/{channel} ─────────────────────────────────────

def scrape_channel_page(channel: str, before: int = None) -> dict:
    """Скрапит одну страницу t.me/s/{channel}?before={id}.
    Возвращает dict: msg_id → {text, photos: [cdn_url,...], date}"""
    url = f'https://t.me/s/{channel}'
    params = {}
    if before:
        params['before'] = before
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'ru,en;q=0.9',
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f'[{channel}] Ошибка скрапинга: {e}')
        return {}

    html = r.text
    posts = {}

    # Находим блоки постов: <div class="tgme_widget_message_wrap ...">
    msg_blocks = re.split(r'(?=<div class="tgme_widget_message_wrap)', html)

    for block in msg_blocks:
        # Message ID из data-post="channel/123"
        mid_m = re.search(r'data-post="[^/"]+/(\d+)"', block)
        if not mid_m:
            continue
        msg_id = int(mid_m.group(1))

        # Текст поста
        text_m = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
        text = ''
        if text_m:
            raw_html = text_m.group(1)
            # Сначала превращаем <br> в перенос строки, чтобы не потерять форматирование
            raw_html = re.sub(r'<br\s*/?>', '\n', raw_html, flags=re.IGNORECASE)
            # Убираем остальные HTML-теги
            text = re.sub(r'<[^>]+>', '', raw_html)
            # Схлопываем пробелы/табы, но НЕ переносы строк
            text = re.sub(r'[ \t]+', ' ', text)
            # Убираем пробелы в начале/конце каждой строки
            text = '\n'.join(l.strip() for l in text.split('\n'))
            # Убираем тройные+ переносы → двойные
            text = re.sub(r'\n{3,}', '\n\n', text).strip()

        # Дата
        date_m = re.search(r'datetime="([^"]+)"', block)
        date_str = date_m.group(1) if date_m else ''

        # CDN-ссылки на фото (только из реальных постов, не из сервисных сообщений)
        # Удаляем секции сервисных сообщений (фото группы/аватар канала) перед поиском URL
        block_no_service = re.sub(r'<div class="tgme_widget_message_service[^"]*".*?</div>', '', block, flags=re.DOTALL)
        # background-image только из блоков фото поста (не из шапки канала/service)
        block_no_header = re.sub(r'<div class="tgme_channel_info[^"]*".*?</div>', '', block_no_service, flags=re.DOTALL)
        cdn_urls = re.findall(r'https://cdn\d*\.telesco\.pe/file/[^"\'>\s]+', block_no_header)
        # background-image — только из tgme_widget_message_photo (обычные фото поста)
        bg_urls = re.findall(r'tgme_widget_message_photo[^>]*>.*?background-image:url\(\'(https://cdn\d*\.telesco\.pe/file/[^\']+)\'\)', block_no_header, re.DOTALL)
        all_photos = list(dict.fromkeys(cdn_urls + bg_urls))  # уникальные, порядок сохранён

        # Первоисточник поста: сначала "Forwarded from", потом первая t.me-ссылка не на агрегатор
        _AGG_CHANNELS = {'parsing_vn', 'parsing_th', 'chatparsing_vn', 'tusaparsing_vn',
                         'baikeparsing_vn', 'baikeparsing_th', 'dom_vn', 'doma_th'}
        src_ch, src_id = '', 0
        fwd_m = re.search(r'tgme_widget_message_forwarded_from.*?href="https://t\.me/([^/"?]+)/(\d+)"', block, re.DOTALL)
        if fwd_m:
            src_ch, src_id = fwd_m.group(1), int(fwd_m.group(2))
        else:
            for _ch, _mid in re.findall(r'href="https://t\.me/([^/"?]+)/(\d+)"', block):
                if _ch.lower() not in _AGG_CHANNELS and not _ch.lower().startswith('parsing_'):
                    src_ch, src_id = _ch, int(_mid)
                    break

        posts[msg_id] = {
            'text': text,
            'photos': all_photos,
            'date': date_str,
            'src_ch': src_ch,
            'src_id': src_id,
        }

    return posts


def scrape_channel_all(channel: str, limit: int = None) -> dict:
    """Скрапит все посты канала с пагинацией. Возвращает dict msg_id → post."""
    all_posts = {}
    before = None
    page = 0
    max_pages = 200  # защита от бесконечного цикла

    logger.info(f'[{channel}] Начало скрапинга (limit={limit or "все"})...')

    while page < max_pages:
        page += 1
        posts = scrape_channel_page(channel, before)

        if not posts:
            logger.info(f'[{channel}] Страница {page}: постов нет, завершаем')
            break

        new_count = 0
        for mid, post in posts.items():
            if mid not in all_posts:
                all_posts[mid] = post
                new_count += 1

        logger.info(f'[{channel}] Стр.{page}: +{new_count} постов (всего: {len(all_posts)}), min_id={min(posts.keys())}')

        if limit and len(all_posts) >= limit:
            logger.info(f'[{channel}] Достигнут лимит {limit}')
            break

        # следующая страница — before=min_id
        before = min(posts.keys())
        if before <= 1:
            break

        time.sleep(0.5)  # вежливая пауза

    logger.info(f'[{channel}] Скрапинг завершён: {len(all_posts)} постов')
    return all_posts


# ─── Bot API: получить file_id через getUpdates ───────────────────

def get_bot_updates_file_ids() -> dict:
    """Получает file_ids из текущих Bot API updates.
    Возвращает dict: channel_username_msgid → file_id"""
    if not BOT_TOKEN:
        return {}
    try:
        r = requests.get(
            f'https://api.telegram.org/bot{BOT_TOKEN}/getUpdates',
            params={'limit': 100, 'allowed_updates': json.dumps(['channel_post'])},
            timeout=15
        )
        if not (r.status_code == 200 and r.json().get('ok')):
            return {}
        updates = r.json().get('result', [])
        index = {}
        for upd in updates:
            cp = upd.get('channel_post', {})
            if not cp:
                continue
            chat = cp.get('chat', {})
            username = (chat.get('username') or '').lower()
            msg_id = cp.get('message_id')
            if not username or not msg_id:
                continue
            photo = cp.get('photo', [])
            if photo:
                file_id = photo[-1]['file_id']
                key = f'{username}_{msg_id}'
                index[key] = file_id
        logger.info(f'[Bot API] getUpdates: найдено {len(index)} file_ids')
        return index
    except Exception as e:
        logger.warning(f'[Bot API] getUpdates error: {e}')
        return {}


def get_file_path(file_id: str) -> str:
    """Получает file_path через Bot API getFile."""
    if not BOT_TOKEN or not file_id:
        return ''
    try:
        r = requests.get(
            f'https://api.telegram.org/bot{BOT_TOKEN}/getFile',
            params={'file_id': file_id}, timeout=10
        )
        if r.status_code == 200 and r.json().get('ok'):
            return r.json()['result']['file_path']
    except Exception:
        pass
    return ''


# ─── Построение листинга из поста ─────────────────────────────────

def detect_logo_fingerprints(scraped: dict) -> set:
    """Определяет fingerprints логотипа канала.
    Логотип — фото, которое появляется в 2+ разных постах (или в msg_id==1)."""
    from collections import Counter
    fp_counter = Counter()
    for mid, post in scraped.items():
        for p in post.get('photos', []):
            fp = p.split('/file/')[-1][:40] if '/file/' in p else p[:40]
            fp_counter[fp] += 1
        # Пост 1 = Channel created, все его фото — логотип
        if mid == 1:
            for p in post.get('photos', []):
                fp = p.split('/file/')[-1][:40] if '/file/' in p else p[:40]
                fp_counter[fp] += 999  # принудительно помечаем
    # Fingerprints встречающиеся в 2+ постах = логотип
    logos = {fp for fp, cnt in fp_counter.items() if cnt >= 2}
    if logos:
        logger.info(f'[logo] Обнаружены fingerprints логотипа: {logos}')
    return logos


def make_listing(channel: str, msg_id: int, post: dict, category: str, country: str,
                 logo_fps: set = None) -> dict:
    """Создаёт новый объект листинга из поста канала."""
    text = post.get('text', '')
    photos = post.get('photos', [])
    date = post.get('date', '')

    # Заголовок: для туров — первая строка; для остальных — первые 120 символов
    if category == 'tours':
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        title = lines[0][:120] if lines else f'Пост {msg_id}'
    else:
        title = text[:120].replace('\n', ' ').strip() if text else f'Пост {msg_id}'

    # Фильтруем логотипы из фото
    if logo_fps:
        photos = [p for p in photos if not any(
            (p.split('/file/')[-1][:40] if '/file/' in p else p[:40]) == fp
            for fp in logo_fps
        )]

    # Первоисточник: если пост переслан из другого канала — берём его
    src_ch = post.get('src_ch') or channel
    src_id = post.get('src_id') or msg_id

    listing = {
        'id': f'{src_ch}_{src_id}',
        'title': title,
        'description': '',
        'text': text,
        'price': 0,
        'price_display': '',
        'city': 'Вьетнам' if country == 'vietnam' else 'Таиланд',
        'city_ru': 'Вьетнам' if country == 'vietnam' else 'Таиланд',
        'date': date,
        'contact': f'@{src_ch}',
        'contact_name': src_ch,
        'source_group': src_ch,
        'source_channel': channel,
        'telegram': f'https://t.me/{src_ch}',
        'telegram_link': f'https://t.me/{src_ch}/{src_id}',
        'image_url': photos[0] if photos else '',
        'all_images': photos,
        'photos': photos,
        'status': 'active',
        'country': country,
        'message_id': msg_id,
        'has_media': bool(photos),
        'category': category,
    }
    return listing


# ─── Обновление существующих листингов ────────────────────────────

def update_listings_photos(listings: list, scraped: dict, channel: str) -> tuple:
    """Обновляет image_url и photos в существующих листингах свежими CDN URL.
    Возвращает (обновлённые листинги, количество обновлений)."""
    updated = 0
    id_to_post = {}
    for mid, post in scraped.items():
        key = f'{channel}_{mid}'
        id_to_post[key] = post

    for item in listings:
        item_id = item.get('id', '')
        src = item.get('source_group', '')

        # Ищем пост по id листинга
        post = id_to_post.get(item_id)
        if not post:
            # Пробуем по source_group + message_id
            mid = item.get('message_id')
            if mid and src == channel:
                post = scraped.get(int(mid)) or scraped.get(mid)

        if post and post.get('photos'):
            old_url = item.get('image_url', '')
            new_url = post['photos'][0]
            item['image_url'] = new_url
            item['photos'] = post['photos']
            item['all_images'] = post['photos']
            item['has_media'] = True
            if old_url != new_url:
                updated += 1

    return listings, updated


# ─── Главный процесс ──────────────────────────────────────────────

def load_json(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_json(path: str, data: dict):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)
    logger.info(f'Сохранено: {path} ({os.path.getsize(path)//1024} KB)')


def run():
    logger.info('=== Запуск bot_channel_parser ===')

    # Получаем file_ids из Bot API updates (для file_id_index.json)
    bot_file_ids = get_bot_updates_file_ids()

    # Загружаем текущий индекс file_id
    fid_index = load_json('file_id_index.json')
    fid_index.update(bot_file_ids)

    # Загружаем файлы данных
    vn_data = load_json('listings_vietnam.json')
    th_data = load_json('listings_thailand.json')

    # Обеспечиваем наличие всех категорий
    for cat in ['real_estate', 'restaurants', 'transport', 'entertainment',
                'money_exchange', 'tours', 'exchange', 'chat', 'visas']:
        vn_data.setdefault(cat, [])
    for cat in ['real_estate', 'restaurants', 'transport', 'exchange']:
        th_data.setdefault(cat, [])

    for (channel, chan_id, category, target_file, limit) in CHANNELS:
        if target_file is None:
            logger.info(f'[{channel}] Пропускаем (баннеры)')
            continue

        logger.info(f'\n{"="*50}')
        logger.info(f'Канал: @{channel} → {category} → {target_file}')

        # Скрапим посты
        scraped = scrape_channel_all(channel, limit)

        if not scraped:
            logger.warning(f'[{channel}] Нет данных, пропускаем')
            continue

        # Определяем целевой файл и данные
        if target_file == 'listings_vietnam.json':
            data = vn_data
            country = 'vietnam'
        else:
            data = th_data
            country = 'thailand'

        # Определяем fingerprints логотипа канала (фото встречающееся в 2+ постах)
        logo_fps = detect_logo_fingerprints(scraped)

        # Обновляем фото у существующих листингов этого канала
        existing = data.get(category, [])
        updated_listings, n_updated = update_listings_photos(existing, scraped, channel)
        logger.info(f'[{channel}] Обновлено фото у {n_updated} существующих записей')

        # Убираем логотип из существующих записей
        if logo_fps:
            for item in updated_listings:
                if item.get('source_group') != channel:
                    continue
                orig = item.get('photos', [])
                clean = [p for p in orig if not any(
                    (p.split('/file/')[-1][:40] if '/file/' in p else p[:40]) == fp
                    for fp in logo_fps
                )]
                if len(clean) != len(orig):
                    item['photos'] = clean
                    item['all_images'] = clean
                    item['image_url'] = clean[0] if clean else ''
                    item['has_media'] = bool(clean)

        # Определяем ID существующих листингов этого канала
        # Используем ВСЕ id из существующих (чтобы не дублировать записи с source_group=None)
        existing_ids = {item['id'] for item in existing}

        # Добавляем новые посты (которых нет в существующих)
        n_added = 0
        SKIP_TITLES = {'channel created', 'канал создан', 'channel photo updated', 'telegram'}
        SPAM_KEYWORDS = [
            'high-roller', 'likesyou', 'high roller', 'casino', 'казино',
            'поднял', 'рекорд', 'впн', 'vpn', '18+', 'работа для молодых',
            'пpибыльнaя', 'ρaҕoτa', 'извиняюсь что не по теме',
            'есть работа', 'заработок', 'пассивный доход', 'зарабатывай',
        ]
        for msg_id in sorted(scraped.keys(), reverse=True):
            item_id = f'{channel}_{msg_id}'
            if item_id in existing_ids:
                continue
            post = scraped[msg_id]
            # Пропускаем системные сообщения (Channel created, etc.)
            raw_title = (post.get('text', '') or '')[:40].lower().strip()
            if not raw_title or raw_title in SKIP_TITLES:
                logger.info(f'[{channel}] Пропуск системного поста {msg_id}: «{raw_title}»')
                continue
            # Спам-фильтр для всех категорий
            post_text_lower = (post.get('text', '') or '').lower()
            if any(kw in post_text_lower for kw in SPAM_KEYWORDS):
                logger.info(f'[{channel}] Пропуск спама {msg_id}')
                continue
            # Запрет: недвижимость без CDN-фото не добавляем
            if category == 'real_estate':
                cdn_photos = [p for p in (post.get('photos') or []) if p and p.startswith('http')]
                if not cdn_photos:
                    logger.info(f'[{channel}] Пропуск real_estate без CDN-фото: msg_id={msg_id}')
                    continue
            # Запрет: развлечения без фото не добавляем
            if category == 'entertainment':
                any_photos = [p for p in (post.get('photos') or []) if p]
                if not any_photos:
                    logger.info(f'[{channel}] Пропуск entertainment без фото: msg_id={msg_id}')
                    continue
            new_item = make_listing(channel, msg_id, post, category, country, logo_fps=logo_fps)
            updated_listings.insert(0, new_item)
            existing_ids.add(item_id)
            n_added += 1

        logger.info(f'[{channel}] Добавлено новых записей: {n_added}')
        data[category] = updated_listings

        # Обновляем file_id_index из scraped постов (если есть file_ids в bot_file_ids)
        for mid in scraped:
            key = f'{channel}_{mid}'
            if key in bot_file_ids:
                fid_index[key] = bot_file_ids[key]

    # Сохраняем обновлённые данные
    save_json('listings_vietnam.json', vn_data)
    save_json('listings_thailand.json', th_data)
    save_json('file_id_index.json', fid_index)

    logger.info('\n=== Готово! ===')
    logger.info('Теперь запустите: python push_to_hf.py')


if __name__ == '__main__':
    run()
