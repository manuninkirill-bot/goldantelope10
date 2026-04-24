"""
tg_feed.py — Telegram Feed.

Импорт истории: парсинг t.me/s/{username}?before={id}
  - Не требует TELEGRAM_CHAT_ID или пересылки
  - Фото приходят как прямые CDN-ссылки (браузер грузит напрямую)

Реальное время: handle_update() вызывается из _gavibeshub_poller в app.py
  - Фото: file_id → getFile → прямая ссылка https://api.telegram.org/file/bot.../...

Хранение: tg_feed_posts.json (список постов, новые первыми)
"""

import os
import re
import json
import time
import threading
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Конфигурация каналов ──────────────────────────────────────────────────────
# username (без @) → (channel_id, заголовок, страна, категория)
CHANNELS = {
    'parsing_vn':     (-1003987939980, 'Недвижимость Вьетнам',   'vietnam',   'real_estate'),
    'parsing_th':     (-1003411602924, 'Недвижимость Таиланд',   'thailand',  'real_estate'),
    'parsing_in':     (-1003948057945, 'Недвижимость Индия',     'india',     'real_estate'),
    'parsing_indo':   (-1003872341008, 'Недвижимость Индонезия', 'indonesia', 'real_estate'),
    'bikeparsing_vn': (-1003922185577, 'Байки Вьетнам',          'vietnam',   'transport'),
    'bikeparsing_th': (-1003894914160, 'Байки Таиланд',          'thailand',  'transport'),
    'bikeparsing_in': (-1003811252596, 'Байки Индия',            'india',     'transport'),
    'banner_vn':      (0,             'Баннеры Вьетнам',         'vietnam',   'banner'),
    'tusaparsing_vn': (-1003603825848, 'Развлечения Вьетнам',    'vietnam',   'entertainment'),
}

# Лимит постов на канал при импорте (None = все)
CHANNEL_IMPORT_LIMITS = {}  # None = все для всех каналов

# ── Хранилище ─────────────────────────────────────────────────────────────────
_POSTS_FILE = 'tg_feed_posts.json'
_posts_lock = threading.Lock()
_posts: list = []
_seen_ids: dict = {}   # channel → set of msg_ids

# ── Статус импорта ────────────────────────────────────────────────────────────
_import_status: dict = {}
_import_lock = threading.Lock()

# ── Режим внешнего поллинга (выставляется из app.py) ─────────────────────────
_external_poll_mode = False

# Headers для t.me/s запросов
_TG_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; TgFeed/1.0)',
    'Accept-Language': 'ru,en;q=0.9',
}


# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────────────────────────────────

def _token() -> str:
    return os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()


def _get_direct_photo_url(file_id: str) -> str:
    """
    file_id → прямая CDN-ссылка через Bot API getFile.
    Браузер грузит напрямую — сервер ничего не скачивает.
    """
    tok = _token()
    if not tok or not file_id:
        return ''
    try:
        r = requests.get(
            f'https://api.telegram.org/bot{tok}/getFile',
            params={'file_id': file_id},
            timeout=10,
        )
        data = r.json()
        if data.get('ok'):
            fp = data['result']['file_path']
            return f'https://api.telegram.org/file/bot{tok}/{fp}'
    except Exception as e:
        logger.debug(f'[tg_feed] getFile ошибка для {file_id}: {e}')
    return ''


def _ch_meta(channel: str):
    info = CHANNELS.get(channel, (0, channel, '', ''))
    return info  # (channel_id, title, country, category)


def _extract_post_from_api(msg: dict, channel: str) -> dict | None:
    """Из объекта Bot API message → нормализованный пост."""
    msg_id = msg.get('message_id')
    if not msg_id:
        return None
    photos = msg.get('photo', [])
    photo_url = ''
    if photos:
        largest = max(photos, key=lambda p: p.get('file_size', 0))
        photo_url = _get_direct_photo_url(largest.get('file_id', ''))
    text = msg.get('text') or msg.get('caption') or ''
    date = msg.get('date', 0)
    channel_id, title, country, category = _ch_meta(channel)
    return {
        'id': msg_id, 'channel': channel, 'channel_id': channel_id,
        'title': title, 'country': country, 'category': category,
        'text': text, 'photo_url': photo_url, 'date': date,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Парсинг t.me/s/{username}  (для массового импорта)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_tme_page(username: str, before_id: int | None = None) -> tuple[list[dict], int | None]:
    """
    Парсит одну страницу t.me/s/{username}?before={before_id}.
    Возвращает (список постов, наименьший msg_id на странице или None).
    Фото — прямые CDN-ссылки из атрибута background-image.
    """
    url = f'https://t.me/s/{username}'
    params = {}
    if before_id:
        params['before'] = before_id
    try:
        r = requests.get(url, params=params, headers=_TG_HEADERS, timeout=15)
        if r.status_code != 200:
            logger.warning(f'[tg_feed] t.me/s/{username} HTTP {r.status_code}')
            return [], None
    except Exception as e:
        logger.warning(f'[tg_feed] запрос к t.me/s/{username} упал: {e}')
        return [], None

    soup = BeautifulSoup(r.text, 'html.parser')
    channel_id, title, country, category = _ch_meta(username)
    posts = []
    min_id = None

    for el in soup.select('.tgme_widget_message'):
        data_post = el.get('data-post', '')
        # data-post = "username/msg_id"
        m = re.search(r'/(\d+)$', data_post)
        if not m:
            continue
        msg_id = int(m.group(1))
        if min_id is None or msg_id < min_id:
            min_id = msg_id

        # Текст
        text_el = el.select_one('.tgme_widget_message_text')
        text = text_el.get_text('\n', strip=True) if text_el else ''

        # Фото — прямая ссылка из background-image
        photo_url = ''
        photo_wrap = el.select_one('.tgme_widget_message_photo_wrap')
        if photo_wrap:
            style = photo_wrap.get('style', '')
            bg_m = re.search(r"url\('([^']+)'\)", style)
            if not bg_m:
                bg_m = re.search(r'url\("([^"]+)"\)', style)
            if not bg_m:
                bg_m = re.search(r'url\(([^)]+)\)', style)
            if bg_m:
                photo_url = bg_m.group(1).strip("'\"")

        # Также ищем <img> внутри (видео-превью, стикеры и т.д.)
        if not photo_url:
            img = el.select_one('.tgme_widget_message_photo img, .tgme_widget_message_sticker img')
            if img:
                photo_url = img.get('src', '')

        # Дата
        date_el = el.select_one('time')
        date_str = date_el.get('datetime', '') if date_el else ''
        try:
            import datetime
            dt = datetime.datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            date_ts = int(dt.timestamp())
        except Exception:
            date_ts = 0

        if not text and not photo_url:
            continue  # сервисные сообщения пропускаем

        posts.append({
            'id': msg_id, 'channel': username, 'channel_id': channel_id,
            'title': title, 'country': country, 'category': category,
            'text': text, 'photo_url': photo_url, 'date': date_ts,
        })

    return posts, min_id


# ─────────────────────────────────────────────────────────────────────────────
# Сохранение / загрузка
# ─────────────────────────────────────────────────────────────────────────────

def _save_posts():
    try:
        with open(_POSTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_posts, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f'[tg_feed] ошибка сохранения: {e}')


def _load_posts():
    global _posts, _seen_ids
    if not os.path.exists(_POSTS_FILE):
        return
    try:
        with open(_POSTS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        _posts = data if isinstance(data, list) else []
        for p in _posts:
            ch = p.get('channel', '')
            if ch not in _seen_ids:
                _seen_ids[ch] = set()
            _seen_ids[ch].add(p.get('id'))
        logger.info(f'[tg_feed] Загружено {len(_posts)} постов с диска')
    except Exception as e:
        logger.warning(f'[tg_feed] ошибка загрузки: {e}')


def _add_post(post: dict) -> bool:
    """Добавляет пост, если он не дублируется. Возвращает True если добавлен."""
    ch = post.get('channel', '')
    pid = post.get('id')
    with _posts_lock:
        if ch not in _seen_ids:
            _seen_ids[ch] = set()
        if pid in _seen_ids[ch]:
            return False
        _seen_ids[ch].add(pid)
        _posts.insert(0, post)
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Публичный API (используется роутами в app.py)
# ─────────────────────────────────────────────────────────────────────────────

def start():
    _load_posts()
    logger.info(f'[tg_feed] Запущен. Постов: {len(_posts)}. '
                f'Каналов: {list(CHANNELS.keys())}')


def handle_update(update: dict):
    """
    Вызывается из _gavibeshub_poller в app.py для каждого входящего update.
    Сохраняет посты из отслеживаемых каналов.
    """
    cp = update.get('channel_post') or update.get('message', {})
    if not cp:
        return
    username = cp.get('chat', {}).get('username', '').lower()
    if username not in CHANNELS:
        return
    post = _extract_post_from_api(cp, username)
    if post and _add_post(post):
        _save_posts()
        logger.debug(f'[tg_feed] Новый пост @{username} id={post["id"]}')


def get_posts(channel: str | None = None, limit: int = 50, offset: int = 0) -> list:
    with _posts_lock:
        result = _posts
        if channel:
            result = [p for p in result if p.get('channel') == channel]
        return result[offset: offset + limit]


def get_stats() -> dict:
    with _posts_lock:
        by_channel: dict = {}
        for p in _posts:
            ch = p.get('channel', '')
            by_channel[ch] = by_channel.get(ch, 0) + 1
        return {
            'total_posts': len(_posts),
            'by_channel': by_channel,
            'channels': list(CHANNELS.keys()),
        }


def get_import_status() -> dict:
    with _import_lock:
        return {ch: dict(s) for ch, s in _import_status.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Массовый импорт через t.me/s
# ─────────────────────────────────────────────────────────────────────────────

def _import_channel(channel: str, limit: int | None = None):
    """
    Импортирует все (или ограниченное количество) постов канала
    через t.me/s/{username}?before={id}.
    Не требует TELEGRAM_CHAT_ID — просто публичное HTTP-превью.
    """
    with _import_lock:
        _import_status[channel] = {'done': False, 'count': 0, 'error': ''}

    logger.info(f'[tg_feed] Импорт @{channel}, лимит={limit}')

    count = 0
    before_id = None
    empty_pages = 0

    while True:
        posts, min_id = _parse_tme_page(channel, before_id)

        if not posts:
            empty_pages += 1
            if empty_pages >= 3:
                break
            time.sleep(2)
            continue
        empty_pages = 0

        added = 0
        for post in posts:
            if _add_post(post):
                count += 1
                added += 1

        if count % 100 == 0 and count > 0:
            _save_posts()
            logger.info(f'[tg_feed] @{channel}: {count} постов импортировано')

        # Достигли лимита?
        if limit and count >= limit:
            logger.info(f'[tg_feed] @{channel}: достигнут лимит {limit}')
            break

        # Идём глубже (более старые посты) — даже если все на этой странице уже есть
        if min_id and min_id > 1:
            before_id = min_id
        else:
            break  # дошли до начала канала

        # Вежливая пауза
        time.sleep(0.5)

    _save_posts()
    with _import_lock:
        _import_status[channel] = {'done': True, 'count': count, 'error': ''}
    logger.info(f'[tg_feed] @{channel}: импорт завершён — {count} постов')


def run_bulk_import(channels: list | None = None):
    """
    Запускает массовый импорт для указанных каналов (или всех).
    Каналы обрабатываются последовательно.
    """
    targets = channels or list(CHANNELS.keys())
    logger.info(f'[tg_feed] Массовый импорт: {targets}')
    for ch in targets:
        limit = CHANNEL_IMPORT_LIMITS.get(ch)
        _import_channel(ch, limit=limit)
    logger.info('[tg_feed] Массовый импорт завершён')
