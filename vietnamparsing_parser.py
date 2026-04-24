import os
import json
import re
import time
import logging
import asyncio
import threading
import requests
import html as hlib
import unicodedata
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

_listings_lock = threading.Lock()

BOT_TOKEN = os.environ.get('VIETNAMPARSING_BOT_TOKEN', '') or os.environ.get('TELEGRAM_BOT_TOKEN', '')
SOURCE_CHANNEL = 'vietnamparsing'
ARENDABAY_CHANNEL = 'arendabaykavietnam'
BARAHOLKA_GROUP = 'hsjsbkskbs'  # supergroup @hsjsbkskbs = baraholkainvietnam

# Extra channels: username -> (category, transport_type or subcategory)
EXTRA_CHANNELS = {
    'baykivietnam':       ('transport', 'bikes'),
    'baraholkainvietnam':   ('marketplace', None),
    'hsjsbkskbs':           ('marketplace', None),  # same supergroup
    'gavibeshub':           ('entertainment', None),
    'restoranvietnam':      ('restaurants', None),
    'obmenvietnam':         ('chat', None),
    'GAvisarun':            ('visas', None),
    'GAtours':              ('tours', None),
    'GAkidsclub':           ('kids', None),
    'GAclinic_vn':          ('medicine', None),
    'GAfoods':              ('restaurants', None),
    'GApayments':           ('money_exchange', None),
    'paymens_vn':           ('money_exchange', None),
}

TH_EXTRA_CHANNELS = {
    'arenda_thailandd':          ('transport', 'bikes'),
    'thailand_market':           ('transport', 'bikes'),
    'rental_service_thailand':   ('transport', 'bikes'),
    'samui_arenda2':             ('transport', 'bikes'),
    'motorrenta':                ('transport', 'bikes'),
    'nashi_phuket_auto':         ('transport', 'bikes'),
    'thailand_drive':            ('transport', 'bikes'),
    'PKHUKET_BAYKOV':            ('transport', 'bikes'),
    'Pattaya_Arenda_ru':         ('transport', 'bikes'),
    'pattaya_happy_auto':        ('transport', 'bikes'),
    'pattaya_arenda':            ('transport', 'bikes'),
    'pattayamoto':               ('transport', 'bikes'),
}

LISTINGS_FILE = 'listings_vietnam.json'
INITIAL_FETCH_LIMIT = 200
POLL_INTERVAL = 60

# Диапазоны Unicode эмодзи для strip_emoji()
_EMOJI_RANGES = (
    (0x1F300, 0x1F9FF),  # Misc symbols, emoticons, transport, supplemental symbols
    (0x1FA00, 0x1FAFF),  # Chess, tools, etc.
    (0x2600,  0x27BF),   # Misc symbols & dingbats
    (0x2300,  0x23FF),   # Misc technical (⏰ etc.)
    (0x25A0,  0x25FF),   # Geometric shapes
    (0x2700,  0x27BF),   # Dingbats
    (0x1F000, 0x1F02F),  # Mahjong / playing cards
    (0x1F0A0, 0x1F0FF),
    (0x1F100, 0x1F1FF),  # Enclosed alphanumeric supplement
    (0x1F200, 0x1F2FF),  # Enclosed ideographic supplement
)
_SKIP_CODEPOINTS = {0x200D, 0xFE0F, 0x20E3, 0xFE00}


def _is_emoji_cp(cp: int) -> bool:
    """True если codepoint является эмодзи или декоративным символом."""
    if cp in _SKIP_CODEPOINTS:
        return True
    try:
        cat = unicodedata.category(chr(cp))
    except Exception:
        return True
    if cat in ('So', 'Cs'):
        return True
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


def strip_emoji(text: str) -> str:
    """Удаляет эмодзи и декоративные символы.
    Эмодзи между фрагментами текста заменяются переносом строки,
    чтобы сохранить структуру постов без исходного форматирования."""
    chars = list(text)
    n = len(chars)
    result = []
    i = 0
    # Отслеживаем, есть ли слева не-пробельный текст
    has_left_text = False

    while i < n:
        cp = ord(chars[i])
        if _is_emoji_cp(cp):
            # Собираем всю цепочку эмодзи подряд
            j = i
            while j < n and _is_emoji_cp(ord(chars[j])):
                j += 1
            # Есть ли не-пробельный текст справа?
            right_text = ''.join(chars[j:]).lstrip(' \t')
            has_right_text = bool(right_text and right_text[0] != '\n')
            # Заменяем эмодзи на \n только если он стоит МЕЖДУ текстовыми фрагментами
            if has_left_text and has_right_text:
                # Не дублируем \n если последний символ уже \n
                last = result[-1] if result else ''
                if last != '\n':
                    result.append('\n')
            # Иначе (в начале или в конце) — просто удаляем
            i = j
        else:
            ch = chars[i]
            result.append(ch)
            if ch not in ' \t\n':
                has_left_text = True
            elif ch == '\n':
                has_left_text = False
            i += 1

    cleaned = ''.join(result)
    # Убираем лишние пробелы и пустые строки
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
    cleaned = re.sub(r'\n[ \t]+', '\n', cleaned)   # убираем пробелы в начале строк
    cleaned = re.sub(r'[ \t]+\n', '\n', cleaned)   # убираем пробелы в конце строк
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


USD_TO_VND = 25300
EUR_TO_VND = 27500

CITY_MAP = {
    'Дананг': [
        'da nang', 'danang', 'дананг', 'da-nang', 'sơn trà', 'son tra',
        'hoa khanh', 'хоакхань', 'ngu hanh son', 'hai chau', 'thanh khe',
        'lien chieu', 'my khe', 'nam o', 'bac my an',
    ],
    'Нячанг': [
        'nha trang', 'нячанг', 'nhatrang', 'nha-trang', 'khanh hoa',
        'vĩnh nguyên', 'vinh nguyen', 'hon tre', 'hon chong', 'bai dai',
        'nyachang', 'niachang', 'arenda_v_nyachang', 'viet_life_niachang',
        'ня чанге', 'нячанге', 'нячанга', 'в нячанг',
    ],
    'Хошимин': [
        'ho chi minh', 'хошимин', 'сайгон', 'saigon', 'hcmc', 'hcm',
        'hochiminh', 'ho-chi-minh', 'district', 'quan ', 'bình thạnh',
        'binh thanh', 'thủ đức', 'thu duc', 'bình dương', 'binh duong',
        'tan binh', 'tân bình', 'go vap', 'gò vấp',
    ],
    'Ханой': [
        'hanoi', 'ha noi', 'ханой', 'hà nội', 'ha-noi', 'tây hồ',
        'tay ho', 'hoan kiem', 'hoàn kiếm', 'ba dinh', 'đống đa', 'dong da',
    ],
    'Фукуок': [
        'phu quoc', 'фукуок', 'phuquoc', 'phú quốc', 'phu-quoc',
        'duong dong', 'dương đông', 'long beach',
    ],
    'Далат': [
        'da lat', 'далат', 'dalat', 'đà lạt', 'da-lat', 'lam dong', 'lâm đồng',
    ],
    'Муйне': [
        'mui ne', 'муйне', 'muine', 'mũi né', 'mui-ne', 'phan thiet',
        'фантьет', 'phanthiet',
    ],
    'Хойан': [
        'hoi an', 'хойан', 'hoian', 'hội an', 'hoi-an', 'quảng nam',
    ],
    'Камрань': [
        'cam ranh', 'камрань', 'camranh', 'cam-ranh',
    ],
    'Вунгтау': [
        'vung tau', 'вунгтау', 'vungtau', 'vũng tàu', 'ba ria',
    ],
    'Хюэ': [
        'hue', 'huế', 'хюэ', 'thua thien',
    ],
}

LISTING_TYPE_RENT = [
    'аренд', 'rent', 'for rent', 'thuê', 'cho thuê', 'сдам', 'сдаю',
    'сдается', 'сдаётся', 'снять', 'краткосроч', 'долгосроч', 'посуточно',
    'available', 'lease', 'per month', 'per night', 'per day',
    '/month', '/mo', '/night',
]

LISTING_TYPE_SALE = [
    'продаж', 'продам', 'продается', 'продаётся', 'продаю', 'for sale',
    'bán', 'giá bán', 'купить', 'покупка', 'buy', 'purchase', 'selling',
]

SPAM_KEYWORDS = [
    # Finance / gambling / crypto
    'casino', 'казино', 'казик', 'джекпот', 'jackpot', 'slot', 'слот',
    'forex', 'crypto trading', 'заработок онлайн', 'пассивный доход',
    'бинарные опционы', 'deriv', 'click here', 'sign up now', 'register now',
    'advertising', 'binary options', 'invest', 'инвестиции в крипт',
    # Non-real-estate services
    'визаран', 'визабег', 'visa run', 'fast track',
    'подбор жилья без комисс',          # real-estate agency ad, not a listing
    'подбор жилья бесплатно',
    'доставка цветов', 'flower delivery', 'bamboo flowers',
    'страхование', 'осаго', 'каско',
    # Aggregator spam
    'все варианты из телеграмм групп',
    'хотите снять у реального собственника',
]

# Blocked sources — listings from these channels are auto-hidden
BLOCKED_SOURCES = []


def format_price_vnd(amount_vnd: int) -> str:
    s = str(int(amount_vnd))
    groups = []
    while len(s) > 3:
        groups.insert(0, s[-3:])
        s = s[:-3]
    if s:
        groups.insert(0, s)
    return ' '.join(groups) + ' VND'


def parse_number_from_str(s: str) -> float:
    s = s.strip()
    s = re.sub(r'[\s\u00a0\xa0]', '', s)
    if not s:
        return 0.0

    # Multiple dots: 16.000.000 → thousands separator
    if re.match(r'^\d{1,3}(\.\d{3})+$', s):
        try:
            return float(s.replace('.', ''))
        except:
            return 0.0

    # Both comma and dot present
    if ',' in s and '.' in s:
        last_comma = s.rfind(',')
        last_dot = s.rfind('.')
        if last_dot > last_comma:
            # English: 1,234.56 or 18,500,000 VND
            s = s.replace(',', '')
        else:
            # European: 1.234,56 → 1234.56
            # Special case: "18.500,000" has 8 raw digits; person likely meant 18,500,000 VND
            # (wrote dot-thousands but mixed notation). Strip all separators if digits >= 7.
            digits_only = re.sub(r'[,.]', '', s)
            if len(digits_only) >= 7:
                try:
                    return float(digits_only)
                except:
                    pass
            s = s.replace('.', '').replace(',', '.')
        try:
            return float(s)
        except:
            return 0.0

    # Only commas: 6,500,000 (thousands) or 6,5 (European decimal)
    if ',' in s:
        parts = s.split(',')
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            # All groups after first have 3 digits → thousands separator
            try:
                return float(s.replace(',', ''))
            except:
                return 0.0
        else:
            # Decimal comma: 6,5 → 6.5
            try:
                return float(s.replace(',', '.'))
            except:
                return 0.0

    # Only dot(s)
    if '.' in s:
        parts = s.split('.')
        if len(parts) == 2:
            if len(parts[1]) == 3 and parts[1].isdigit() and parts[0].isdigit():
                # Ambiguous: 8.500 — treat as thousands separator (8500)
                try:
                    return float(s.replace('.', ''))
                except:
                    return 0.0
            else:
                # Decimal: 8.5, 8.50, 8.75
                try:
                    return float(s)
                except:
                    return 0.0

    try:
        return float(s)
    except:
        return 0.0


def normalize_price_text(text: str) -> str:
    # Normalize Unicode compatibility characters (e.g. 𝕧𝕟𝕕 → vnd)
    text = unicodedata.normalize('NFKC', text)
    # Remove URLs so message IDs inside t.me/channel/12345 aren't parsed as prices
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r't\.me/\S+', '', text)
    # Strip Vietnamese phone numbers: +84 / 84 followed by any 9-digit number,
    # OR local 0[3-9]... format (operator digits are 3-9, NOT 0-2 which appear in prices).
    # Using [3-9] for the first operator digit avoids stripping e.g. "025 300 000" in prices.
    text = re.sub(r'(?<!\d)(?:\+84|84)\s*[3-9]\d[\d\s\-\.]{7,9}(?!\d)', ' ', text)
    text = re.sub(r'(?<!\d)0[3-9]\d[\d\s\-\.]{7,9}(?!\d)', ' ', text)
    return text


def extract_price(text: str):
    if not text:
        return None, None

    # Normalize Unicode lookalikes (𝕧𝕟𝕕 → vnd, etc.) and strip URLs
    text = normalize_price_text(text)

    # Russian million words: миллион / миллиона / миллионов (+ МИЛЛИОНОВ etc.)
    _mln_ru = r'(?:миллионов|миллиона|миллион|млн)'
    _mln_en = r'(?:million|mln)'
    _mln_vi = r'(?:triệu|trieu|tr\.?\s?đ|tr\b)'
    _mln_any = rf'(?:{_mln_ru}|{_mln_en}|{_mln_vi})'
    # tỷ = Vietnamese for billion; require word boundary so "ty" doesn't match "Type:", "style" etc.
    _ty_vi = r'(?:tỷ|tỉ|\bty\b)'
    _vnd = r'(?:VND|vnd|донг|đồng|₫)'
    _per = r'(?:/\s*(?:month|mon|мес(?:яц)?|mo)\b)?'  # optional /month /мес suffix
    # Number with separators. Two forms allowed:
    #   a) Comma/dot only: 20,000,000 or 20.000.000 (no spaces)
    #   b) Space-thousands: 20 000 000 (only groups of exactly 3 digits after space)
    # This prevents "20,000,000 25,000,000" from merging into one huge number.
    # Use \d{1,4} for the leading group to handle e.g. "1516,000,000" (1.516B VND).
    _num = (r'\d{1,4}(?:,\d{3})*(?:\.\d+)?'   # comma-thousands or decimal
            r'|\d{1,3}(?:\.\d{3})+(?:,\d+)?'  # dot-thousands (European)
            r'|\d{1,3}(?:\s\d{3})+'            # space-thousands: 20 000 000
            r'|\d+'                             # plain integer
            )

    patterns = [
        # 1. Tỷ (billion VND) — highest priority multiplier
        (rf'({_num})\s*{_ty_vi}', 'VND_TY'),
        # 2. Millions with explicit word (млн/миллион/million/triệu) — before plain VND
        #    This prevents utility costs like "16.000 vnd/m³" overriding "13 млн донг"
        (rf'({_num})\s*{_mln_any}\s*{_vnd}?{_per}', 'VND_MLN'),
        # 2b. Short "M" abbreviation for million: 17M VND, 17M/month
        # Requires VND or /month context to avoid matching "700m from beach" (meters)
        (rf'(\d[\d.,]*)\s*[Mm]\b(?=\s*(?:{_vnd}|/\s*(?:month|mon|мес)))', 'VND_MLN'),
        # 3. Unambiguous dot-million: 16.000.000 VND (must have 2+ dot-groups)
        (rf'(\d{{1,3}}(?:\.\d{{3}}){{2,}})\s*{_vnd}', 'VND'),
        # 4. Large plain number + VND (>= 6 digits = at least 100 000)
        (rf'({_num})\s*{_vnd}{_per}', 'VND'),
        # 5. USD
        (rf'({_num})\s*(?:USD|usd|\$|доллар)', 'USD'),
        (rf'\$\s*({_num})', 'USD'),
        # 6. EUR
        (rf'({_num})\s*(?:EUR|eur|€|евро)', 'EUR'),
        (rf'€\s*({_num})', 'EUR'),
        # 7. Any plain number + VND (fallback, lower priority)
        (rf'({_num})\s*{_vnd}{_per}', 'VND'),
        # 8. Large number with /month or /мес without currency → assume VND
        (rf'({_num})\s*/\s*(?:month|mon|мес(?:яц)?|mo)\b', 'VND_GUESS'),
        # 9. Price keyword context
        (rf'(?:price|цена|стоимость|giá)[^\d]{{0,10}}([\d][\d\s.,]*)\s*(?:{_vnd}|USD|usd|\$|EUR|€)?', 'AUTO'),
    ]

    MIN_VND = 2_000_000  # minimum plausible real estate price in VND

    for pattern, currency in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1).strip()
        amount = parse_number_from_str(raw)
        if amount <= 0:
            continue

        if currency == 'VND':
            vnd = int(amount)
            if vnd < 1000:
                vnd *= 1_000_000
        elif currency == 'VND_MLN':
            if amount >= 1_000_000:
                # Double-multiplier guard: "18,500,000 million" means the number
                # is already in the million-VND range; treat as direct VND.
                vnd = int(amount)
            else:
                vnd = int(amount * 1_000_000)
        elif currency == 'VND_TY':
            vnd = int(amount * 1_000_000_000)
        elif currency == 'USD':
            if amount > 100_000:
                vnd = int(amount)
            else:
                vnd = int(amount * USD_TO_VND)
        elif currency == 'EUR':
            if amount > 100_000:
                vnd = int(amount)
            else:
                vnd = int(amount * EUR_TO_VND)
        elif currency == 'VND_GUESS':
            if amount < 100_000:
                continue
            # Cap VND_GUESS at 2 billion (beyond that = phone number or error)
            if amount > 2_000_000_000:
                continue
            vnd = int(amount)
        elif currency == 'AUTO':
            if amount < 10_000:
                vnd = int(amount * USD_TO_VND)
            else:
                vnd = int(amount)
        else:
            continue

        # Skip prices below minimum — keep searching for a larger one
        if vnd < MIN_VND:
            continue

        return vnd, format_price_vnd(vnd)

    return None, None


def detect_city(text: str) -> str:
    text_lower = text.lower()
    for city_ru, keywords in CITY_MAP.items():
        for kw in keywords:
            if kw in text_lower:
                return city_ru
    return 'Вьетнам'


def detect_listing_type(text: str) -> str:
    text_lower = text.lower()
    sale_hits = sum(1 for kw in LISTING_TYPE_SALE if kw in text_lower)
    rent_hits = sum(1 for kw in LISTING_TYPE_RENT if kw in text_lower)
    if sale_hits > rent_hits:
        return 'sale'
    return 'rent'


def is_spam(text: str) -> bool:
    if not text or len(text.strip()) < 20:
        return True
    text_lower = text.lower()
    for kw in SPAM_KEYWORDS:
        if kw in text_lower:
            return True
    return False


def is_blocked_source(text: str) -> bool:
    """Returns True if the listing comes from a blocked channel — skip entirely."""
    text_lower = text.lower()
    for src in BLOCKED_SOURCES:
        if src in text_lower:
            return True
    return False


def extract_source_from_text(text: str) -> str:
    # Try to get @username from "Источник: @username" or "Источник: https://t.me/username"
    m = re.search(r'(?:источник|source)[:\s]+https?://t\.me/([\w]+)', text, re.IGNORECASE)
    if m:
        return f"@{m.group(1)}"
    m = re.search(r'(?:источник|source)[:\s]+(@[\w]+)', text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Fallback: any t.me URL (channel name only, no message_id)
    m = re.search(r'https?://t\.me/([\w]+)(?:/\d+)?', text, re.IGNORECASE)
    if m:
        return f"@{m.group(1)}"
    # Full source line text
    m = re.search(r'^(?:источник|source)[:\s]*(.*?)$', text, re.IGNORECASE | re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ''


def extract_telegram_link_from_text(text: str) -> str:
    """Extract 'Ссылка: https://t.me/...' direct post URL from message text."""
    # "Ссылка: https://t.me/channel/12345"
    m = re.search(r'(?:ссылка|link)[:\s]+(https?://t\.me/[\w/]+)', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Any t.me URL with a message_id (channel/12345)
    m = re.search(r'(https?://t\.me/[\w]+/\d+)', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ''


SKIP_LINE_PREFIXES = re.compile(
    r'^(?:источник|source|описание|цена|price|адрес|address|тип|type|город|city|available|'
    r'расположение|location|контакт|contact|telegram|whatsapp|ссылка|link|https?://)',
    re.IGNORECASE
)

def extract_title(text: str) -> str:
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for line in lines:
        if SKIP_LINE_PREFIXES.match(line):
            continue
        clean = re.sub(r'[#*_]', '', line).strip()
        if len(clean) > 5:
            return clean[:120]
    # Fallback: strip source/label lines from full text
    fallback = re.sub(
        r'(?:источник|source|описание|цена|адрес|город|available)[:\s]*\S+\s*\n?',
        '', text, flags=re.IGNORECASE
    ).strip()
    return (fallback[:100] if fallback else text[:100])


def clean_html_text(html_str: str) -> str:
    text = re.sub(r'<br\s*/?>', '\n', html_str)
    text = re.sub(r'<a\s[^>]*href="([^"]+)"[^>]*>.*?</a>', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    return hlib.unescape(text).strip()


def fetch_tg_post_text(tg_url: str) -> str:
    """Получает полный текст поста Telegram через ?embed=1 когда парсер получил только ссылку."""
    try:
        embed_url = tg_url.rstrip('/') + '?embed=1&mode=tme'
        r = requests.get(embed_url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
        })
        if r.status_code != 200:
            return ''
        m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', r.text, re.DOTALL)
        if m:
            return clean_html_text(m.group(1))
    except Exception as e:
        logger.debug(f'fetch_tg_post_text {tg_url}: {e}')
    return ''


def scrape_channel_page(before_id: int = None) -> list:
    url = f"https://t.me/s/{SOURCE_CHANNEL}"
    if before_id:
        url += f"?before={before_id}"

    try:
        resp = requests.get(url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; Python/3.11 parser)'
        })
        resp.raise_for_status()
        page = resp.text
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return []

    msg_blocks = re.split(r'(?=<div class="tgme_widget_message_wrap)', page)
    results = []

    for block in msg_blocks[1:]:
        post_id_m = re.search(r'data-post="[^/]+/(\d+)"', block)
        if not post_id_m:
            continue
        post_id = int(post_id_m.group(1))

        date_m = re.search(r'datetime="([^"]+)"', block)
        date_str = date_m.group(1) if date_m else datetime.now(timezone.utc).isoformat()

        text_m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
        text = clean_html_text(text_m.group(1)) if text_m else ''

        imgs = re.findall(r"background-image:url\('(https://cdn[^']+)'\)", block)
        imgs = list(dict.fromkeys(imgs))

        results.append({
            'post_id': post_id,
            'date': date_str,
            'text': text,
            'images': imgs,
        })

    return results


def fetch_initial_200() -> int:
    logger.info(f"Fetching last {INITIAL_FETCH_LIMIT} messages from t.me/s/{SOURCE_CHANNEL}...")

    data = load_listings()
    existing_ids = get_existing_ids(data)

    if 'real_estate' not in data:
        data['real_estate'] = []

    all_messages = []
    before_id = None
    pages_fetched = 0
    max_pages = 12

    while len(all_messages) < INITIAL_FETCH_LIMIT and pages_fetched < max_pages:
        page_msgs = scrape_channel_page(before_id=before_id)
        if not page_msgs:
            break

        all_messages.extend(page_msgs)
        pages_fetched += 1
        oldest_id = min(m['post_id'] for m in page_msgs)
        before_id = oldest_id
        logger.info(f"  Page {pages_fetched}: got {len(page_msgs)} msgs (oldest: {oldest_id})")

        if len(page_msgs) < 3:
            break
        time.sleep(1.0)

    logger.info(f"Total scraped: {len(all_messages)} messages across {pages_fetched} pages")

    new_items = []
    for msg in all_messages[:INITIAL_FETCH_LIMIT]:
        item_id = f"vietnamparsing_{msg['post_id']}"
        if item_id in existing_ids:
            continue

        item = build_listing_item(msg, item_id)
        if item is None:
            continue

        new_items.append(item)
        existing_ids.add(item_id)

    new_count = len(new_items)
    if new_count > 0:
        fresh_data = load_listings()
        fresh_ids = get_existing_ids(fresh_data)
        if 'real_estate' not in fresh_data:
            fresh_data['real_estate'] = []
        added = 0
        for item in new_items:
            if item['id'] not in fresh_ids:
                fresh_data['real_estate'].insert(0, item)
                added += 1
        if added > 0:
            save_listings(fresh_data)
    logger.info(f"Initial fetch complete. Added {new_count} new real estate listings.")
    return new_count


def build_listing_item(msg: dict, item_id: str) -> dict | None:
    text = msg.get('text', '')
    if is_spam(text):
        return None
    blocked = is_blocked_source(text)

    _tg_link_re = re.search(r'(https?://t\.me/[\w]+/\d+)', text)
    _text_without_links = re.sub(r'https?://\S+', '', text).strip()
    if _tg_link_re and len(_text_without_links) < 50:
        tg_link_url = _tg_link_re.group(1)
        fetched = fetch_tg_post_text(tg_link_url)
        if fetched and len(fetched) >= 15:
            text = fetched + f'\n\n{tg_link_url}'
            logger.info(f'[embed_enrich] {item_id}: enriched {len(fetched)} chars from {tg_link_url[:60]}')

    price_vnd, price_display = extract_price(text)
    city = detect_city(text)
    listing_type = detect_listing_type(text)
    title = extract_title(text)
    source = extract_source_from_text(text)
    telegram_link = extract_telegram_link_from_text(text)
    images = msg.get('images', [])
    if not images:
        return None  # skip listings without photos
    # Skip listings with no real description (only meta lines like Источник/Ссылка)
    _meta_re = re.compile(r'^(источник|ссылка|link|source)\s*:', re.IGNORECASE)
    _main_content = '\n'.join(l for l in text.split('\n') if not _meta_re.match(l.strip())).strip()
    if len(_main_content) < 15:
        return None  # no real description — would show "Описание недоступно"

    # Очищаем описание от эмодзи для отображения (заголовок, цена, город уже извлечены)
    clean_text = strip_emoji(text)
    clean_title = strip_emoji(title)

    return {
        'id': item_id,
        'title': clean_title,
        'text': clean_text,
        'description': clean_text,
        'city': city,
        'city_ru': city,
        'listing_type': listing_type,
        'price': price_vnd,
        'price_display': price_display or '',
        'contact': source or 'Контакт в описании',
        'telegram_link': telegram_link or '',
        'source_group': f"@{SOURCE_CHANNEL}",
        'photos': images,
        'image_url': images[0] if images else None,
        'all_images': images if images else None,
        'date': msg.get('date', datetime.now(timezone.utc).isoformat()),
        'category': 'real_estate',
        'status': 'approved',
        'country': 'vietnam',
        'message_id': msg['post_id'],
        'has_media': bool(images),
        'hidden': blocked,
    }


def build_arendabay_transport_item(msg: dict, item_id: str) -> dict | None:
    """Build a transport/bikes listing from an @arendabaykavietnam post."""
    text = msg.get('text', '')
    if is_spam(text):
        return None
    images = msg.get('images', [])
    title = extract_title(text) or 'Аренда байка'
    city = detect_city(text) or 'Nha Trang'
    telegram_link = extract_telegram_link_from_text(text) or ''
    price_vnd, price_display = extract_price(text)

    _meta_re = re.compile(r'^(источник|ссылка|link|source)\s*:', re.IGNORECASE)
    _main_content = '\n'.join(l for l in text.split('\n') if not _meta_re.match(l.strip())).strip()
    if len(_main_content) < 10:
        return None

    return {
        'id': item_id,
        'title': title,
        'text': text,
        'description': text,
        'category': 'transport',
        'transport_type': 'bikes',
        'city': city,
        'city_ru': city,
        'price': price_vnd,
        'price_display': price_display or '',
        'contact': f'@{ARENDABAY_CHANNEL}',
        'contact_name': ARENDABAY_CHANNEL,
        'source_group': ARENDABAY_CHANNEL,
        'telegram': f'https://t.me/{ARENDABAY_CHANNEL}',
        'telegram_link': telegram_link,
        'photos': images,
        'image_url': images[0] if images else None,
        'all_images': images if images else None,
        'date': msg.get('date', datetime.now(timezone.utc).isoformat()),
        'status': 'approved',
        'country': 'vietnam',
        'message_id': msg.get('post_id', 0),
        'has_media': bool(images),
    }


def process_arendabay_update(update: dict, override_photos: list | None = None) -> dict | None:
    """Process a single Bot API update from @arendabaykavietnam into a transport/bikes listing."""
    post = update.get('channel_post') or update.get('message')
    if not post:
        return None

    chat = post.get('chat', {})
    chat_username = chat.get('username', '').lower()
    if chat_username != ARENDABAY_CHANNEL.lower():
        return None

    text = post.get('text', '') or post.get('caption', '')
    msg_id = post.get('message_id', 0)
    date_ts = post.get('date', 0)
    date_str = datetime.fromtimestamp(date_ts, tz=timezone.utc).isoformat() if date_ts else datetime.now(timezone.utc).isoformat()

    if override_photos is not None:
        photos = override_photos
    else:
        cdn_photos = _scrape_cdn_photos_for_post(ARENDABAY_CHANNEL, msg_id)
        if cdn_photos:
            photos = cdn_photos
        else:
            url = _extract_largest_photo_url(post)
            photos = [url] if url else []

    item_id = f"arendabay_{msg_id}"
    msg_data = {
        'post_id': msg_id,
        'date': date_str,
        'text': text,
        'images': photos,
    }
    return build_arendabay_transport_item(msg_data, item_id)


def scrape_arendabay_page(before_id: int = None) -> list:
    """Scrape a page of posts from t.me/s/arendabaykavietnam."""
    url = f"https://t.me/s/{ARENDABAY_CHANNEL}"
    if before_id:
        url += f"?before={before_id}"
    try:
        resp = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
        page = resp.text
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return []

    msg_blocks = re.split(r'(?=<div class="tgme_widget_message_wrap)', page)
    results = []
    for block in msg_blocks[1:]:
        post_id_m = re.search(r'data-post="[^/]+/(\d+)"', block)
        if not post_id_m:
            continue
        post_id = int(post_id_m.group(1))
        date_m = re.search(r'datetime="([^"]+)"', block)
        date_str = date_m.group(1) if date_m else datetime.now(timezone.utc).isoformat()
        text_m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
        text = clean_html_text(text_m.group(1)) if text_m else ''
        imgs = re.findall(r"background-image:url\('(https://cdn[^']+)'\)", block)
        imgs = list(dict.fromkeys(imgs))
        results.append({'post_id': post_id, 'date': date_str, 'text': text, 'images': imgs})
    return results


def fetch_arendabay_history(data: dict, existing_ids: set, max_msgs: int = 200) -> int:
    """Scrape recent posts from @arendabaykavietnam and add them as transport/bikes."""
    new_count = 0
    if 'transport' not in data:
        data['transport'] = []
    try:
        all_msgs = []
        before_id = None
        for _ in range(10):
            page_msgs = scrape_arendabay_page(before_id=before_id)
            if not page_msgs:
                break
            all_msgs.extend(page_msgs)
            if len(all_msgs) >= max_msgs:
                break
            before_id = min(m['post_id'] for m in page_msgs)
            if len(page_msgs) < 3:
                break
            time.sleep(0.5)

        for msg in all_msgs:
            # Skip system "Channel created" post
            if msg['text'].strip().lower() in ('channel created', 'канал создан', '') and not msg['images']:
                continue
            item_id = f"arendabay_{msg['post_id']}"
            if item_id in existing_ids:
                continue
            item = build_arendabay_transport_item(msg, item_id)
            if item is None:
                continue
            data['transport'].insert(0, item)
            existing_ids.add(item_id)
            new_count += 1
            logger.info(f"[arendabay] New bike: {item['title'][:60]}")
    except Exception as e:
        logger.warning(f"Arendabay scrape error: {e}")
    return new_count


def _is_link_only(text: str) -> bool:
    """Return True if text is essentially just links/mentions with no real content."""
    if not text:
        return True
    cleaned = re.sub(r'https?://\S+', '', text)
    cleaned = re.sub(r'@[\w]+', '', cleaned)
    cleaned = re.sub(r'[^\w]', '', cleaned)
    return len(cleaned) < 20


def build_generic_listing(msg: dict, item_id: str, channel: str, category: str, subcategory=None) -> dict | None:
    """Build a listing item from any extra channel post."""
    text = msg.get('text', '') or msg.get('caption', '') or ''
    if not text and not msg.get('images'):
        return None
    if _is_link_only(text):
        return None

    date_str = msg.get('date', datetime.now(timezone.utc).isoformat())
    photos = msg.get('images') or []
    msg_id = msg.get('post_id', 0)

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    # Если первая строка — только эмодзи, берём следующую как заголовок
    raw_title = lines[0] if lines else 'Без названия'
    clean_t = strip_emoji(raw_title).strip()
    if not clean_t and len(lines) > 1:
        raw_title = lines[1]
        clean_t = strip_emoji(raw_title).strip()
    title = (clean_t or raw_title)[:120]
    description = '\n'.join(lines[1:]) if len(lines) > 1 else text[:300]
    # Если заголовок совпадает с description[0], сдвигаем description
    desc_lines = [l.strip() for l in description.splitlines() if l.strip()]
    if desc_lines and strip_emoji(desc_lines[0]).strip() == title:
        description = '\n'.join(desc_lines[1:])
    # Если description — только t.me ссылка (пересланный пост), убираем
    if re.match(r'^https?://t\.me/[\w/]+$', description.strip()):
        description = '\n'.join(lines[1:]).replace(description.strip(), '').strip()

    price_display = ''
    price = 0
    price_match = re.search(r'(\d[\d\s,.]*)\s*(?:₫|VND|vnd|usd|USD|\$|€|EUR|฿|THB)', text, re.IGNORECASE)
    if price_match:
        price_display = price_match.group(0)
        try:
            price = int(re.sub(r'[\s,.]', '', price_match.group(1)))
        except Exception:
            pass

    tg_link = f'https://t.me/{channel}/{msg_id}' if msg_id else f'https://t.me/{channel}'

    RAW_CHANNELS = {'paymens_vn'}
    if channel in RAW_CHANNELS:
        clean_desc = description.strip()
        clean_text = text.strip()
        title = (lines[0] if lines else 'Без названия')[:120]
    else:
        clean_desc = strip_emoji(description).strip()
        clean_text = strip_emoji(text).strip()

    item: dict = {
        'id': item_id,
        'title': title,
        'description': clean_desc,
        'text': clean_text,
        'price': price,
        'price_display': price_display,
        'city': _detect_city_from_text(text, CHANNEL_CITY_MAP.get(channel, 'Вьетнам')),
        'city_ru': _detect_city_from_text(text, CHANNEL_CITY_MAP.get(channel, 'Вьетнам')),
        'date': date_str,
        'contact': f'@{channel}',
        'contact_name': channel,
        'source_group': channel,
        'telegram': f'https://t.me/{channel}',
        'telegram_link': tg_link,
        'image_url': photos[0] if photos else '',
        'all_images': photos,
        'photos': photos,
        'status': 'active',
        'country': 'vietnam',
        'message_id': msg_id,
        'has_media': bool(photos),
        'category': category,
    }

    if category == 'transport':
        item['transport_type'] = subcategory or 'bikes'
        item['listing_type'] = 'transport'
    elif category == 'marketplace':
        item['marketplace_category'] = subcategory or 'other'
        item['whatsapp'] = ''
    elif category == 'entertainment':
        item['listing_type'] = 'entertainment'
        item['realestate_city'] = item.get('city', 'Нячанг').lower().replace('нячанг', 'nhatrang').replace('хошимин', 'hochiminh').replace('дананг', 'danang').replace('фукуок', 'phuquoc').replace('хойан', 'hoian').replace('далат', 'dalat').replace('вьетнам', 'nhatrang')
    elif category == 'restaurants':
        item['location'] = ''
        item['source'] = f'https://t.me/{channel}'
        item['whatsapp'] = ''
        item['images'] = photos

    return item


def process_extra_channel_update(update: dict, channel: str, category: str, subcategory=None,
                                 override_photos=None) -> dict | None:
    """Process a Bot API update from an extra channel into a listing."""
    post = update.get('channel_post') or update.get('message') or {}
    chat_username = post.get('chat', {}).get('username', '').lower()
    if chat_username not in (channel.lower(), EXTRA_CHANNELS and channel.lower()):
        pass  # accept anyway; already filtered upstream

    msg_id = post.get('message_id', 0)
    text = post.get('text') or post.get('caption') or ''
    has_photo = bool(post.get('photo') or post.get('media_group_id'))
    if not text.strip() and not has_photo:
        return None

    date_ts = post.get('date', 0)
    date_str = datetime.fromtimestamp(date_ts, tz=timezone.utc).isoformat() if date_ts else datetime.now(timezone.utc).isoformat()

    if override_photos:
        photos = override_photos
    else:
        has_photo = bool(post.get('photo'))
        if has_photo:
            if channel in PRIVATE_SUPERGROUPS:
                photos = [f'/tg_img/{channel}/{msg_id}']
                photo_list = post.get('photo', [])
                if photo_list:
                    largest = max(photo_list, key=lambda p: p.get('file_size', 0))
                    fid = largest.get('file_id')
                    if fid:
                        try:
                            idx_path = 'file_id_index.json'
                            idx = {}
                            if os.path.exists(idx_path):
                                with open(idx_path, 'r') as _f:
                                    idx = json.load(_f)
                            idx[f'{channel}_{msg_id}'] = fid
                            with open(idx_path, 'w') as _f:
                                json.dump(idx, _f, ensure_ascii=False, indent=2)
                        except Exception:
                            pass
            else:
                photos = [f'https://t.me/{channel}/{msg_id}']
        else:
            photos = []

    item_id = f"{channel}_{msg_id}"
    msg_data = {
        'post_id': msg_id,
        'date': date_str,
        'text': text,
        'caption': post.get('caption', ''),
        'images': photos,
    }
    return build_generic_listing(msg_data, item_id, channel, category, subcategory)


def scrape_extra_channel_page(channel: str, before_id: int | None = None) -> list[dict]:
    """Scrape posts from t.me/s/<channel>. Returns list of raw msg dicts."""
    url = f'https://t.me/s/{channel}'
    params = {}
    if before_id:
        params['before'] = before_id
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0'}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.warning(f"[{channel}] scrape error: {e}")
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    messages = soup.find_all('div', class_='tgme_widget_message_wrap')
    results = []
    for wrap in messages:
        try:
            msg_div = wrap.find('div', class_='tgme_widget_message')
            if not msg_div:
                continue
            data_post = msg_div.get('data-post', '')
            post_id = 0
            if '/' in data_post:
                try:
                    post_id = int(data_post.split('/')[-1])
                except Exception:
                    pass

            text_div = wrap.find('div', class_='tgme_widget_message_text')
            text = text_div.get_text('\n', strip=True) if text_div else ''

            date_tag = wrap.find('time')
            date_str = date_tag.get('datetime', '') if date_tag else ''

            photo_wraps = wrap.find_all('a', class_='tgme_widget_message_photo_wrap')
            photos = []
            if photo_wraps and post_id:
                photos = [f'https://t.me/{channel}/{post_id}']

            results.append({
                'post_id': post_id,
                'text': text,
                'date': date_str,
                'images': photos,
            })
        except Exception:
            continue
    return results


PRIVATE_SUPERGROUPS = {
    'gavibeshub': -1003873439967,
    'GAvisarun': -1003798373372,
    'GAtours': -1003806322614,
    'GAkidsclub': -1003651083423,
    'GAclinic_vn': -1003435759447,
    'GAfoods': -1003824692347,
    'GApayments': -1003752108127,
    'paymens_vn': -1002406407953,
}

CHANNEL_CITY_MAP = {
    'gavibeshub': 'Нячанг',
    'GAvisarun': 'Вьетнам',
    'GAtours': 'Вьетнам',
    'GAkidsclub': 'Вьетнам',
    'GAclinic_vn': 'Вьетнам',
    'GAfoods': 'Вьетнам',
    'GApayments': 'Вьетнам',
    'paymens_vn': 'Вьетнам',
}

CITY_KEYWORDS = {
    'Нячанг': ['нячанг', 'nha trang', 'nhatrang'],
    'Дананг': ['дананг', 'da nang', 'danang'],
    'Хошимин': ['хошимин', 'ho chi minh', 'hochiminh', 'сайгон', 'saigon'],
    'Ханой': ['ханой', 'hanoi', 'ha noi'],
    'Фукуок': ['фукуок', 'phu quoc', 'phuquoc'],
    'Далат': ['далат', 'da lat', 'dalat'],
    'Муйне': ['муйне', 'mui ne', 'muine', 'муй не'],
    'Фантьет': ['фантьет', 'фантхиет', 'phan thiet', 'phanthiet'],
    'Вунгтау': ['вунгтау', 'vung tau', 'vungtau'],
    'Хойан': ['хойан', 'хой ан', 'hoi an', 'hoian'],
}


def _detect_city_from_text(text: str, default_city: str = 'Вьетнам') -> str:
    """Detect Vietnamese city name from message text."""
    if not text:
        return default_city
    text_lower = text.lower()
    for city, keywords in CITY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return city
    return default_city


def fetch_private_group_history_via_bot(channel: str, chat_id: int, category: str,
                                         subcategory, data: dict, existing_ids: set,
                                         max_msg_id: int = 50) -> int:
    """Fetch history from a private supergroup via Bot API forwardMessage trick."""
    if not BOT_TOKEN:
        return 0
    new_count = 0
    if category not in data:
        data[category] = []
    file_id_map = {}
    try:
        with open('file_id_index.json', 'r') as f:
            file_id_map = json.load(f)
    except Exception:
        pass

    for msg_id in range(1, max_msg_id + 1):
        item_id = f"{channel}_{msg_id}"
        if item_id in existing_ids:
            continue
        try:
            r = requests.post(
                f'https://api.telegram.org/bot{BOT_TOKEN}/forwardMessage',
                json={
                    'chat_id': chat_id,
                    'from_chat_id': chat_id,
                    'message_id': msg_id,
                    'disable_notification': True
                },
                timeout=10
            )
            result = r.json()
            if not result.get('ok'):
                continue

            msg = result['result']
            fwd_id = msg['message_id']
            requests.post(
                f'https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage',
                json={'chat_id': chat_id, 'message_id': fwd_id},
                timeout=5
            )

            text = msg.get('text', '') or msg.get('caption', '') or ''
            if not text.strip():
                continue

            has_photo = 'photo' in msg
            if has_photo:
                if channel in PRIVATE_SUPERGROUPS:
                    photos = [f'/tg_img/{channel}/{msg_id}']
                else:
                    photos = [f'https://t.me/{channel}/{msg_id}']
            else:
                photos = []

            if has_photo and msg.get('photo'):
                best = max(msg['photo'], key=lambda p: p.get('file_size', 0))
                file_id_map[item_id] = best['file_id']

            date_ts = msg.get('date', 0)
            date_str = datetime.fromtimestamp(date_ts, tz=timezone.utc).isoformat() if date_ts else datetime.now(timezone.utc).isoformat()

            msg_data = {
                'post_id': msg_id,
                'date': date_str,
                'text': text,
                'images': photos,
            }
            item = build_generic_listing(msg_data, item_id, channel, category, subcategory)
            if item is None:
                continue
            data[category].insert(0, item)
            existing_ids.add(item_id)
            new_count += 1
            logger.info(f"[{channel}] Bot API history: {item['title'][:60]}")
            time.sleep(0.3)
        except Exception as e:
            logger.debug(f"[{channel}] msg_id={msg_id} error: {e}")
            continue

    try:
        with open('file_id_index.json', 'w') as f:
            json.dump(file_id_map, f, indent=2)
    except Exception:
        pass
    return new_count


def fetch_extra_channel_history(channel: str, category: str, subcategory,
                                data: dict, existing_ids: set, max_pages: int = 3) -> int:
    """Fetch historical posts from an extra channel and add to data."""
    new_count = 0
    if category not in data:
        data[category] = []
    try:
        before_id = None
        for _ in range(max_pages):
            msgs = scrape_extra_channel_page(channel, before_id)
            if not msgs:
                break
            for msg in msgs:
                item_id = f"{channel}_{msg['post_id']}"
                if item_id in existing_ids:
                    continue
                item = build_generic_listing(msg, item_id, channel, category, subcategory)
                if item is None:
                    continue
                data[category].insert(0, item)
                existing_ids.add(item_id)
                new_count += 1
            before_id = min(m['post_id'] for m in msgs if m['post_id'])
            time.sleep(0.5)
    except Exception as e:
        logger.warning(f"[{channel}] history fetch error: {e}")
    return new_count


def load_listings() -> dict:
    with _listings_lock:
        try:
            with open(LISTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load listings: {e}")
            return {}


def save_listings(data: dict):
    with _listings_lock:
        try:
            tmp = LISTINGS_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                os.replace(tmp, LISTINGS_FILE)
            except OSError:
                import shutil
                shutil.move(tmp, LISTINGS_FILE)
        except Exception as e:
            logger.error(f"Failed to save listings: {e}")
            try:
                with open(LISTINGS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e2:
                logger.error(f"Direct save also failed: {e2}")


def _text_fingerprint(text):
    return set(text.lower().split())

def _is_text_duplicate(text_a, text_b, threshold=0.9):
    if not text_a or not text_b:
        return False
    fp_a = _text_fingerprint(text_a)
    fp_b = _text_fingerprint(text_b)
    if not fp_a or not fp_b:
        return False
    inter = len(fp_a & fp_b)
    union = len(fp_a | fp_b)
    jaccard = inter / union if union > 0 else 0.0
    if jaccard < 0.7:
        return False
    from difflib import SequenceMatcher
    return SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio() >= threshold

def _get_item_text(item):
    return (item.get('description') or item.get('text') or item.get('title') or '').strip()

def _get_item_price(item):
    return str(item.get('price') or item.get('price_raw') or '').strip()

def _is_link_only_item(item: dict) -> bool:
    desc = (item.get('description') or item.get('text') or '').strip()
    if not desc:
        return True
    desc_no_links = re.sub(r'https?://\S+', '', desc).strip()
    desc_no_links = re.sub(r'@\w+', '', desc_no_links).strip()
    if len(desc_no_links) < 20:
        return True
    return False

def atomic_add_listing(category: str, item: dict) -> bool:
    if _is_link_only_item(item):
        logger.info(f"[filter] Отклонено (только ссылка/короткий текст): {item.get('id','')}")
        return False
    with _listings_lock:
        try:
            with open(LISTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}
        ids = set()
        for cat_items in data.values():
            if isinstance(cat_items, list):
                for it in cat_items:
                    if isinstance(it, dict) and 'id' in it:
                        ids.add(it['id'])
        if item.get('id') in ids:
            return False
        new_text = _get_item_text(item)
        new_price = _get_item_price(item)
        if new_text and category in data and isinstance(data[category], list):
            for existing in data[category][:200]:
                ex_price = _get_item_price(existing)
                if new_price != ex_price and (new_price or ex_price):
                    continue
                if _is_text_duplicate(new_text, _get_item_text(existing)):
                    logger.info(f"[dedup] Дубликат отклонён: {item.get('id','')} ~= {existing.get('id','')}")
                    return False
        if category not in data:
            data[category] = []
        data[category].insert(0, item)
        try:
            tmp = LISTINGS_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                os.replace(tmp, LISTINGS_FILE)
            except OSError:
                import shutil
                shutil.move(tmp, LISTINGS_FILE)
            return True
        except Exception as e:
            logger.error(f"atomic_add_listing failed: {e}")
            return False


def _content_fingerprint(item: dict) -> str:
    title = (item.get('title', '') or '')[:80].strip().lower()
    price = str(item.get('price', '') or '')
    return f"{title}||{price}"


def get_existing_ids(data: dict) -> set:
    ids = set()
    for cat, items in data.items():
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and 'id' in item:
                    ids.add(item['id'])
    return ids


def get_content_fingerprints(data: dict) -> set:
    fps = set()
    for cat, items in data.items():
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    fp = _content_fingerprint(item)
                    if fp != '||':
                        fps.add(fp)
    return fps


def poll_bot_for_updates(last_update_id: int = 0) -> tuple[list, int]:
    """Poll for updates via getUpdates every 30s. Deletes webhook if conflict."""
    if not BOT_TOKEN:
        return [], last_update_id
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        params = {
            'offset': last_update_id + 1,
            'timeout': 20,
            'allowed_updates': json.dumps(['channel_post', 'message']),
        }
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 409:
            logger.info("getUpdates 409 — удаляю webhook для перехода на polling...")
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook",
                json={'drop_pending_updates': False}, timeout=10
            )
            time.sleep(2)
            resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        updates = result.get('result', [])
        if updates:
            last_update_id = updates[-1]['update_id']
        return updates, last_update_id
    except Exception as e:
        logger.warning(f"Bot API poll error: {e}")
        return [], last_update_id


def _scrape_cdn_photos_for_post(channel: str, post_id: int) -> list:
    """Scrape permanent CDN photo URLs for a specific Telegram post from the public viewer."""
    if not post_id:
        return []
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
        r = requests.get(f"https://t.me/s/{channel}?before={post_id + 1}", headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        html = r.text
        # Find the specific post block
        pattern = rf'data-post="{channel}/{post_id}"(.*?)(?=data-post="{channel}/\d+"|\Z)'
        block_m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        block = block_m.group(0) if block_m else html
        imgs = re.findall(r"background-image:url\('(https://cdn[^']+)'\)", block)
        return list(dict.fromkeys(imgs))
    except Exception:
        return []


def _resolve_file_url(file_id: str) -> str:
    """Resolve a Telegram file_id to a direct download URL."""
    if not file_id or not BOT_TOKEN:
        return ''
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={'file_id': file_id}, timeout=10
        )
        file_path = resp.json().get('result', {}).get('file_path', '')
        if file_path:
            return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    except Exception:
        pass
    return ''


def _extract_largest_photo_url(post: dict) -> str:
    """Get the URL of the largest photo variant from a Telegram post."""
    photo_list = post.get('photo', [])
    if not photo_list:
        return ''
    largest = max(photo_list, key=lambda p: p.get('file_size', 0))
    return _resolve_file_url(largest.get('file_id', ''))


def process_bot_update(update: dict, override_photos: list | None = None) -> dict | None:
    """Process a single Bot API update into a listing item.

    override_photos: if provided, use these URLs instead of extracting from post.
                     Used when media-group photos are pre-collected by the caller.
    """
    post = update.get('channel_post') or update.get('message')
    if not post:
        return None

    chat = post.get('chat', {})
    chat_username = chat.get('username', '')
    if chat_username.lower() != SOURCE_CHANNEL.lower():
        return None

    text = post.get('text', '') or post.get('caption', '')
    msg_id = post.get('message_id', 0)
    date_ts = post.get('date', 0)
    date_str = datetime.fromtimestamp(date_ts, tz=timezone.utc).isoformat() if date_ts else datetime.now(timezone.utc).isoformat()

    if override_photos is not None:
        photos = override_photos
    else:
        # Scrape viewer to count photos, then store as t.me URLs (proxy keeps them fresh forever)
        cdn_photos = _scrape_cdn_photos_for_post(SOURCE_CHANNEL, msg_id)
        if cdn_photos:
            photos = [f'https://t.me/{SOURCE_CHANNEL}/{msg_id + i}' for i in range(len(cdn_photos))]
        else:
            # Fallback: temporary Bot API URL (if post is very new and not yet visible on viewer)
            url = _extract_largest_photo_url(post)
            photos = [url] if url else []

    fwd = post.get('forward_from_chat', {})
    fwd_name = ''
    if fwd:
        fwd_name = fwd.get('username', '') or fwd.get('title', '')
        if fwd.get('username'):
            fwd_name = f"@{fwd['username']}"

    source_in_text = extract_source_from_text(text)
    contact = source_in_text or fwd_name or 'Контакт в описании'

    item_id = f"vietnamparsing_{msg_id}"
    msg_data = {
        'post_id': msg_id,
        'date': date_str,
        'text': text,
        'images': photos,
    }
    item = build_listing_item(msg_data, item_id)
    if item:
        item['contact'] = contact
    return item


_parser_state = {
    'running': False,
    'last_update_id': 0,
    'new_today': 0,
    'total_parsed': 0,
    'last_run': None,
    'status': 'idle',
}

def get_parser_state() -> dict:
    return _parser_state.copy()


def run_initial_fetch():
    _parser_state['status'] = 'fetching_initial'
    count = fetch_initial_200()
    # Also try to fetch history from @arendabaykavietnam (needs bot to be admin)
    try:
        ab_data = load_listings()
        ab_ids = get_existing_ids(ab_data)
        ab_count = fetch_arendabay_history(ab_data, ab_ids)
        if ab_count > 0:
            save_listings(ab_data)
            count += ab_count
            logger.info(f"Fetched {ab_count} transport/bikes from @{ARENDABAY_CHANNEL}")
    except Exception as e:
        logger.warning(f"Arendabay history fetch error: {e}")

    for ch, priv_chat_id in PRIVATE_SUPERGROUPS.items():
        if ch in EXTRA_CHANNELS:
            cat, subcat = EXTRA_CHANNELS[ch]
            try:
                pg_data = load_listings()
                pg_ids = get_existing_ids(pg_data)
                n = fetch_private_group_history_via_bot(ch, priv_chat_id, cat, subcat, pg_data, pg_ids)
                if n > 0:
                    save_listings(pg_data)
                    count += n
                    logger.info(f"Fetched {n} [{cat}] from private @{ch} via Bot API")
            except Exception as e_pg:
                logger.warning(f"[{ch}] private group history error: {e_pg}")

    EXTRA_PUBLIC_CHANNELS = {
        k: v for k, v in EXTRA_CHANNELS.items()
        if k not in ('hsjsbkskbs',) and k not in PRIVATE_SUPERGROUPS
    }
    for ch, (cat, subcat) in EXTRA_PUBLIC_CHANNELS.items():
        try:
            ex_data = load_listings()
            ex_ids = get_existing_ids(ex_data)
            n = fetch_extra_channel_history(ch, cat, subcat, ex_data, ex_ids, max_pages=2)
            if n > 0:
                save_listings(ex_data)
                count += n
                logger.info(f"Fetched {n} [{cat}] items from @{ch}")
        except Exception as e_ex:
            logger.warning(f"[{ch}] history fetch error: {e_ex}")

    # Fetch history from TH extra channels (transport Thailand)
    from thailandparsing_parser import (
        load_listings as th_load_init, save_listings as th_save_init,
        get_existing_ids as th_get_ids_init,
    )
    for ch, (cat, subcat) in TH_EXTRA_CHANNELS.items():
        try:
            th_ex_data = th_load_init()
            th_ex_ids = th_get_ids_init(th_ex_data)
            n = fetch_extra_channel_history(ch, cat, subcat, th_ex_data, th_ex_ids, max_pages=2)
            if n > 0:
                th_save_init(th_ex_data)
                count += n
                logger.info(f"Fetched {n} TH [{cat}] items from @{ch}")
        except Exception as e_th_ex:
            logger.warning(f"[TH {ch}] history fetch error: {e_th_ex}")

    _parser_state['total_parsed'] = count
    _parser_state['new_today'] = count
    _parser_state['last_run'] = datetime.now(timezone.utc).isoformat()
    _parser_state['status'] = 'monitoring'
    logger.info("Switched to monitoring mode.")


def _group_media_updates(updates: list) -> tuple[list, list, list, dict, dict]:
    """Split updates into (vietnam_items, thailand_updates, arendabay_items, extra_items, th_extra_items).

    Returns:
      vietnam_items: list of (update, override_photos) tuples for @vietnamparsing
      thailand_updates: list of raw updates from @thailandparsing
      arendabay_items: list of (update, override_photos) tuples for @arendabaykavietnam
      extra_items: dict of channel -> list of (update, override_photos) for EXTRA_CHANNELS
      th_extra_items: dict of channel -> list of (update, override_photos) for TH_EXTRA_CHANNELS
    """
    from collections import OrderedDict

    _private_chatid_to_channel = {v: k for k, v in PRIVATE_SUPERGROUPS.items()}

    thailand_updates = []
    media_groups: dict = OrderedDict()
    arendabay_media_groups: dict = OrderedDict()
    extra_media_groups: dict = {}  # channel -> OrderedDict of mgid -> group
    th_extra_media_groups: dict = {}  # channel -> OrderedDict of mgid -> group
    singles = []
    arendabay_singles = []
    extra_singles: dict = {}  # channel -> list of (upd, None)
    th_extra_singles: dict = {}  # channel -> list of (upd, None)
    _th_extra_lower = {k.lower(): k for k in TH_EXTRA_CHANNELS}

    for upd in updates:
        post = upd.get('channel_post') or upd.get('message') or {}
        chat_username = post.get('chat', {}).get('username', '').lower()
        if not chat_username:
            chat_id = post.get('chat', {}).get('id')
            if chat_id and chat_id in _private_chatid_to_channel:
                chat_username = _private_chatid_to_channel[chat_id]

        if chat_username == 'thailandparsing':
            thailand_updates.append(upd)
            continue

        if chat_username == ARENDABAY_CHANNEL.lower():
            mgid = post.get('media_group_id')
            if mgid:
                if mgid not in arendabay_media_groups:
                    arendabay_media_groups[mgid] = {'main': upd, 'all_updates': []}
                else:
                    caption = post.get('caption') or post.get('text', '')
                    existing_main_post = (
                        arendabay_media_groups[mgid]['main'].get('channel_post')
                        or arendabay_media_groups[mgid]['main'].get('message') or {}
                    )
                    existing_has_text = existing_main_post.get('caption') or existing_main_post.get('text')
                    if caption and not existing_has_text:
                        arendabay_media_groups[mgid]['main'] = upd
                arendabay_media_groups[mgid]['all_updates'].append(upd)
            else:
                arendabay_singles.append((upd, None))
            continue

        if chat_username in EXTRA_CHANNELS:
            ch = chat_username
            mgid = post.get('media_group_id')
            if mgid:
                if ch not in extra_media_groups:
                    extra_media_groups[ch] = OrderedDict()
                if mgid not in extra_media_groups[ch]:
                    extra_media_groups[ch][mgid] = {'main': upd, 'all_updates': []}
                else:
                    caption = post.get('caption') or post.get('text', '')
                    existing_main_post = (
                        extra_media_groups[ch][mgid]['main'].get('channel_post')
                        or extra_media_groups[ch][mgid]['main'].get('message') or {}
                    )
                    existing_has_text = existing_main_post.get('caption') or existing_main_post.get('text')
                    if caption and not existing_has_text:
                        extra_media_groups[ch][mgid]['main'] = upd
                extra_media_groups[ch][mgid]['all_updates'].append(upd)
            else:
                if ch not in extra_singles:
                    extra_singles[ch] = []
                extra_singles[ch].append((upd, None))
            continue

        if chat_username in _th_extra_lower:
            ch = _th_extra_lower[chat_username]
            mgid = post.get('media_group_id')
            if mgid:
                if ch not in th_extra_media_groups:
                    th_extra_media_groups[ch] = OrderedDict()
                if mgid not in th_extra_media_groups[ch]:
                    th_extra_media_groups[ch][mgid] = {'main': upd, 'all_updates': []}
                else:
                    caption = post.get('caption') or post.get('text', '')
                    existing_main_post = (
                        th_extra_media_groups[ch][mgid]['main'].get('channel_post')
                        or th_extra_media_groups[ch][mgid]['main'].get('message') or {}
                    )
                    existing_has_text = existing_main_post.get('caption') or existing_main_post.get('text')
                    if caption and not existing_has_text:
                        th_extra_media_groups[ch][mgid]['main'] = upd
                th_extra_media_groups[ch][mgid]['all_updates'].append(upd)
            else:
                if ch not in th_extra_singles:
                    th_extra_singles[ch] = []
                th_extra_singles[ch].append((upd, None))
            continue

        if chat_username == 'vietnamparsing':
            mgid = post.get('media_group_id')
            if mgid:
                if mgid not in media_groups:
                    media_groups[mgid] = {'main': upd, 'all_updates': []}
                else:
                    caption = post.get('caption') or post.get('text', '')
                    existing_main_post = (
                        media_groups[mgid]['main'].get('channel_post')
                        or media_groups[mgid]['main'].get('message') or {}
                    )
                    existing_has_text = existing_main_post.get('caption') or existing_main_post.get('text')
                    if caption and not existing_has_text:
                        media_groups[mgid]['main'] = upd
                media_groups[mgid]['all_updates'].append(upd)
            else:
                singles.append((upd, None))

    # Build override_photos for each Vietnam media group
    vietnam_items = list(singles)
    for mgid, grp in media_groups.items():
        grp['all_updates'].sort(
            key=lambda u: (u.get('channel_post') or u.get('message') or {}).get('message_id', 0)
        )
        main_post = grp['main'].get('channel_post') or grp['main'].get('message') or {}
        main_msg_id = main_post.get('message_id', 0)
        cdn_photos = _scrape_cdn_photos_for_post(SOURCE_CHANNEL, main_msg_id)
        if cdn_photos:
            tme_photos = [f'https://t.me/{SOURCE_CHANNEL}/{main_msg_id + i}' for i in range(len(cdn_photos))]
            vietnam_items.append((grp['main'], tme_photos))
        else:
            all_photos = []
            for upd in grp['all_updates']:
                post = upd.get('channel_post') or upd.get('message') or {}
                url = _extract_largest_photo_url(post)
                if url and url not in all_photos:
                    all_photos.append(url)
            vietnam_items.append((grp['main'], all_photos if all_photos else None))

    # Build override_photos for each Arendabay media group
    arendabay_items = list(arendabay_singles)
    for mgid, grp in arendabay_media_groups.items():
        grp['all_updates'].sort(
            key=lambda u: (u.get('channel_post') or u.get('message') or {}).get('message_id', 0)
        )
        main_post = grp['main'].get('channel_post') or grp['main'].get('message') or {}
        main_msg_id = main_post.get('message_id', 0)
        cdn_photos = _scrape_cdn_photos_for_post(ARENDABAY_CHANNEL, main_msg_id)
        if cdn_photos:
            arendabay_items.append((grp['main'], cdn_photos))
        else:
            all_photos = []
            for upd in grp['all_updates']:
                post = upd.get('channel_post') or upd.get('message') or {}
                url = _extract_largest_photo_url(post)
                if url and url not in all_photos:
                    all_photos.append(url)
            arendabay_items.append((grp['main'], all_photos if all_photos else None))

    # Build extra_items: channel -> list of (upd, override_photos)
    extra_items: dict = {}
    for ch, singles_list in extra_singles.items():
        extra_items[ch] = list(singles_list)
    for ch, mg_dict in extra_media_groups.items():
        if ch not in extra_items:
            extra_items[ch] = []
        for mgid, grp in mg_dict.items():
            grp['all_updates'].sort(
                key=lambda u: (u.get('channel_post') or u.get('message') or {}).get('message_id', 0)
            )
            all_photos = []
            for upd in grp['all_updates']:
                post = upd.get('channel_post') or upd.get('message') or {}
                url = _extract_largest_photo_url(post)
                if url and url not in all_photos:
                    all_photos.append(url)
            extra_items[ch].append((grp['main'], all_photos if all_photos else None))

    th_extra_items: dict = {}
    for ch, singles_list in th_extra_singles.items():
        th_extra_items[ch] = list(singles_list)
    for ch, mg_dict in th_extra_media_groups.items():
        if ch not in th_extra_items:
            th_extra_items[ch] = []
        for mgid, grp in mg_dict.items():
            grp['all_updates'].sort(
                key=lambda u: (u.get('channel_post') or u.get('message') or {}).get('message_id', 0)
            )
            all_photos = []
            for upd in grp['all_updates']:
                post = upd.get('channel_post') or upd.get('message') or {}
                url = _extract_largest_photo_url(post)
                if url and url not in all_photos:
                    all_photos.append(url)
            th_extra_items[ch].append((grp['main'], all_photos if all_photos else None))

    return vietnam_items, thailand_updates, arendabay_items, extra_items, th_extra_items


def _scrape_new_from_tme(existing_ids: set, data: dict) -> int:
    """Scrape last 3 pages of t.me/s/vietnamparsing for new listings. Returns count added."""
    new_count = 0
    try:
        all_msgs = []
        before_id = None
        for _ in range(3):
            page_msgs = scrape_channel_page(before_id=before_id)
            if not page_msgs:
                break
            all_msgs.extend(page_msgs)
            before_id = min(m['post_id'] for m in page_msgs)
            time.sleep(0.5)

        if not all_msgs:
            return 0

        if 'real_estate' not in data:
            data['real_estate'] = []

        for msg in all_msgs:
            item_id = f"vietnamparsing_{msg['post_id']}"
            if item_id in existing_ids:
                continue
            item = build_listing_item(msg, item_id)
            if item is None:
                continue
            data['real_estate'].insert(0, item)
            existing_ids.add(item_id)
            new_count += 1
            logger.info(f"[t.me/s] New: [{item['city']}] {item['title'][:60]} | {item['price_display']}")
    except Exception as e:
        logger.warning(f"t.me/s scrape error: {e}")
    return new_count


def _handle_user_commands(updates: list) -> None:
    """Process /start and other private user commands from bot updates."""
    try:
        from telegram_bot import handle_start, send_message
    except ImportError:
        return

    webapp_url_env = os.environ.get('REPLIT_DOMAINS', '')
    webapp_url = f"https://{webapp_url_env.split(',')[0]}" if webapp_url_env else "https://goldantelope-asia.replit.app"

    for upd in updates:
        msg = upd.get('message')
        if not msg:
            continue
        chat = msg.get('chat', {})
        if chat.get('type') != 'private':
            continue
        text = msg.get('text', '')
        chat_id = chat.get('id')
        user_name = msg.get('from', {}).get('first_name', '')
        if not chat_id:
            continue
        if text == '/start':
            handle_start(chat_id, user_name)
            logger.info(f"[bot] /start from {user_name} ({chat_id})")
        elif text == '/help':
            send_message(chat_id, '🦌 <b>Goldantelope ASIA</b>\n\n/start — Главное меню\n/help — Помощь\n\n📍 <a href="https://t.me/goldantelopeasia_bot">@goldantelopeasia_bot</a>')


def run_monitoring_loop():
    from thailandparsing_parser import add_thailand_listings
    _parser_state['running'] = True
    last_update_id = 0
    scrape_counter = 0
    SCRAPE_EVERY = 5  # Run t.me/s scrape every N bot-poll cycles (every 5 min at 60s interval)
    logger.info("Starting bot update polling loop (Vietnam + Thailand)...")

    while _parser_state['running']:
        try:
            updates, last_update_id = poll_bot_for_updates(last_update_id)
            if updates:
                for _dbg_upd in updates:
                    _dbg_post = _dbg_upd.get('channel_post') or _dbg_upd.get('message') or {}
                    _dbg_chat = _dbg_post.get('chat', {})
                    _dbg_uname = _dbg_chat.get('username', '')
                    _dbg_cid = _dbg_chat.get('id', '')
                    _dbg_title = _dbg_chat.get('title', '')
                    _dbg_text = (_dbg_post.get('text') or _dbg_post.get('caption') or '')[:40]
                    logger.info(f"[poll_debug] upd_id={_dbg_upd.get('update_id')} chat_id={_dbg_cid} username={_dbg_uname} title={_dbg_title} text={_dbg_text}")

                # Handle private user commands (/start etc.)
                _handle_user_commands(updates)

                data = load_listings()
                existing_ids = get_existing_ids(data)
                new_count = 0

                vietnam_items, thailand_updates, arendabay_items, extra_items, th_extra_items = _group_media_updates(updates)

                for upd, override_photos in vietnam_items:
                    item = process_bot_update(upd, override_photos=override_photos)
                    if not item:
                        continue
                    if item['id'] in existing_ids:
                        continue
                    if 'real_estate' not in data:
                        data['real_estate'] = []
                    data['real_estate'].insert(0, item)
                    existing_ids.add(item['id'])
                    new_count += 1
                    n_photos = len(item.get('all_images') or item.get('photos') or [])
                    logger.info(f"New VN: [{item['city']}] {item['title'][:60]} | {item['price_display']} | {n_photos} photo(s)")

                for upd, override_photos in arendabay_items:
                    item = process_arendabay_update(upd, override_photos=override_photos)
                    if not item:
                        continue
                    if item['id'] in existing_ids:
                        continue
                    if 'transport' not in data:
                        data['transport'] = []
                    data['transport'].insert(0, item)
                    existing_ids.add(item['id'])
                    new_count += 1
                    n_photos = len(item.get('all_images') or item.get('photos') or [])
                    logger.info(f"New BIKES: [{item['city']}] {item['title'][:60]} | {n_photos} photo(s)")

                for ch, upd_list in extra_items.items():
                    category, subcategory = EXTRA_CHANNELS.get(ch, (None, None))
                    if not category:
                        continue
                    for upd, override_photos in upd_list:
                        item = process_extra_channel_update(upd, ch, category, subcategory, override_photos)
                        if not item:
                            continue
                        if item['id'] in existing_ids:
                            continue
                        if category not in data:
                            data[category] = []
                        data[category].insert(0, item)
                        existing_ids.add(item['id'])
                        new_count += 1
                        logger.info(f"New [{category}] from @{ch}: {item['title'][:60]}")

                if new_count > 0:
                    save_listings(data)
                    _parser_state['new_today'] = _parser_state.get('new_today', 0) + new_count
                    _parser_state['total_parsed'] = _parser_state.get('total_parsed', 0) + new_count

                if th_extra_items:
                    from thailandparsing_parser import (
                        load_listings as th_load, save_listings as th_save,
                        get_existing_ids as th_get_ids,
                    )
                    th_data = th_load()
                    th_ids = th_get_ids(th_data)
                    th_new = 0
                    for ch, upd_list in th_extra_items.items():
                        category, subcategory = TH_EXTRA_CHANNELS.get(ch, (None, None))
                        if not category:
                            continue
                        for upd, override_photos in upd_list:
                            item = process_extra_channel_update(upd, ch, category, subcategory, override_photos)
                            if not item:
                                continue
                            if item['id'] in th_ids:
                                continue
                            if category not in th_data:
                                th_data[category] = []
                            th_data[category].insert(0, item)
                            th_ids.add(item['id'])
                            th_new += 1
                            logger.info(f"New TH [{category}] from @{ch}: {item['title'][:60]}")
                    if th_new > 0:
                        th_save(th_data)
                        _parser_state['new_today'] = _parser_state.get('new_today', 0) + th_new
                        _parser_state['total_parsed'] = _parser_state.get('total_parsed', 0) + th_new

                if thailand_updates:
                    add_thailand_listings(thailand_updates)

            # Every 5 minutes: scrape t.me/s/vietnamparsing + scan new Thailand posts by ID
            scrape_counter += 1
            if scrape_counter >= SCRAPE_EVERY:
                scrape_counter = 0

                # Vietnam: t.me/s/ scrape
                data = load_listings()
                existing_ids = get_existing_ids(data)
                n_vn = _scrape_new_from_tme(existing_ids, data)
                if n_vn > 0:
                    save_listings(data)
                    _parser_state['new_today'] = _parser_state.get('new_today', 0) + n_vn
                    _parser_state['total_parsed'] = _parser_state.get('total_parsed', 0) + n_vn
                    logger.info(f"t.me/s scrape added {n_vn} new VN listings")

                # Arendabay: scrape new bike posts
                try:
                    data = load_listings()
                    existing_ids = get_existing_ids(data)
                    n_ab = fetch_arendabay_history(data, existing_ids, max_msgs=20)
                    if n_ab > 0:
                        save_listings(data)
                        _parser_state['new_today'] = _parser_state.get('new_today', 0) + n_ab
                        _parser_state['total_parsed'] = _parser_state.get('total_parsed', 0) + n_ab
                        logger.info(f"Arendabay scrape added {n_ab} new bike listings")
                except Exception as e_ab:
                    logger.warning(f"Arendabay scrape error: {e_ab}")

                # VN extra channels: periodic re-scrape (public channels via t.me/s/)
                for ex_ch, (ex_cat, ex_subcat) in EXTRA_CHANNELS.items():
                    try:
                        ex_data = load_listings()
                        ex_ids = get_existing_ids(ex_data)
                        n_ex = fetch_extra_channel_history(ex_ch, ex_cat, ex_subcat, ex_data, ex_ids, max_pages=1)
                        if n_ex > 0:
                            save_listings(ex_data)
                            _parser_state['new_today'] = _parser_state.get('new_today', 0) + n_ex
                            _parser_state['total_parsed'] = _parser_state.get('total_parsed', 0) + n_ex
                            logger.info(f"VN extra @{ex_ch} scrape added {n_ex} [{ex_cat}] listings")
                    except Exception as e_ex:
                        logger.warning(f"VN extra @{ex_ch} scrape error: {e_ex}")

                # Thailand: scan new posts by consecutive ID probing
                try:
                    from thailandparsing_parser import (
                        load_listings as th_load,
                        save_listings as th_save,
                        get_existing_ids as th_get_ids,
                        scan_new_thailand_by_id,
                    )
                    th_data = th_load()
                    th_ids = th_get_ids(th_data)
                    n_th = scan_new_thailand_by_id(th_ids, th_data)
                    if n_th > 0:
                        th_save(th_data)
                        _parser_state['new_today'] = _parser_state.get('new_today', 0) + n_th
                        _parser_state['total_parsed'] = _parser_state.get('total_parsed', 0) + n_th
                        logger.info(f"TH id-scan added {n_th} new Thailand listings")
                except Exception as e_th:
                    logger.warning(f"TH id-scan error: {e_th}")

                # Thailand extra channels: scrape transport
                for th_ch, (th_cat, th_subcat) in TH_EXTRA_CHANNELS.items():
                    try:
                        from thailandparsing_parser import (
                            load_listings as th_load2, save_listings as th_save2,
                            get_existing_ids as th_get_ids2,
                        )
                        th_ex_data = th_load2()
                        th_ex_ids = th_get_ids2(th_ex_data)
                        n_thx = fetch_extra_channel_history(th_ch, th_cat, th_subcat, th_ex_data, th_ex_ids, max_pages=1)
                        if n_thx > 0:
                            th_save2(th_ex_data)
                            _parser_state['new_today'] = _parser_state.get('new_today', 0) + n_thx
                            _parser_state['total_parsed'] = _parser_state.get('total_parsed', 0) + n_thx
                            logger.info(f"TH scrape @{th_ch} added {n_thx} transport listings")
                    except Exception as e_thx:
                        logger.warning(f"TH @{th_ch} scrape error: {e_thx}")

            _parser_state['last_run'] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            logger.error(f"Monitoring loop error: {e}")

        time.sleep(POLL_INTERVAL)


def repair_transport_images():
    """Конвертирует устаревшие CDN (telesco.pe) ссылки транспорта в прямые t.me ссылки."""
    data = load_listings()
    transport = data.get('transport', [])
    updated = 0
    for item in transport:
        mid = item.get('message_id')
        channel = item.get('source_group') or item.get('contact_name') or ''
        if not mid or not channel:
            continue
        changed = False
        if 'telesco.pe' in (item.get('image_url') or ''):
            item['image_url'] = f'https://t.me/{channel}/{mid}'
            changed = True
        imgs = item.get('all_images') or []
        new_imgs = []
        for i, u in enumerate(imgs):
            if 'telesco.pe' in str(u):
                new_imgs.append(f'https://t.me/{channel}/{mid + i}')
                changed = True
            else:
                new_imgs.append(u)
        if new_imgs != imgs:
            item['all_images'] = new_imgs
            item['photos'] = new_imgs
        if changed:
            updated += 1
    if updated:
        save_listings(data)
        logger.info(f'[transport_repair] Конвертировано {updated} CDN → t.me ссылок.')
    else:
        logger.info('[transport_repair] CDN ссылок не найдено.')


def repair_link_only_listings():
    """Находит объявления у которых description = только t.me ссылка,
    подгружает реальный текст через ?embed=1 и сохраняет обратно в JSON."""
    data = load_listings()
    repaired = 0
    skipped = 0

    for section in ('real_estate',):
        for item in data.get(section, []):
            desc = (item.get('description') or '').strip()
            tg_link = (item.get('telegram_link') or '').strip()

            _desc_link = re.search(r'(https?://t\.me/[\w]+/\d+)', desc)
            _desc_no_links = re.sub(r'https?://\S+', '', desc).strip()
            is_link_only = bool(re.match(r'^https?://t\.me/[\w/]+$', desc))
            is_short_with_link = bool(_desc_link) and len(_desc_no_links) < 50

            if not (is_link_only or is_short_with_link or len(desc) < 20):
                continue

            target_url = tg_link or (_desc_link.group(1) if _desc_link else None) or (desc if desc.startswith('http') else None)
            if not target_url:
                continue

            fetched = fetch_tg_post_text(target_url)
            if not fetched or len(fetched) < 15:
                skipped += 1
                continue

            new_desc = strip_emoji(fetched + (f'\n\n{target_url}' if target_url else ''))
            item['description'] = new_desc
            item['text'] = new_desc
            item['title'] = strip_emoji(extract_title(fetched))
            if not item.get('city') or item['city'] in ('', 'Другое'):
                item['city'] = detect_city(fetched) or item.get('city', '')
                item['city_ru'] = item['city']
            if not item.get('price'):
                pv, pd = extract_price(fetched)
                if pv:
                    item['price'] = pv
                    item['price_display'] = pd or ''
            repaired += 1
            time.sleep(0.2)

    if repaired:
        save_listings(data)
        logger.info(f'[repair] Обогащено {repaired} объявлений с пустым описанием ({skipped} не удалось)')
    else:
        logger.info(f'[repair] Нет объявлений для обогащения ({skipped} пропущено)')


def start_parser_in_background():
    if _parser_state['running']:
        logger.info("Parser already running.")
        return

    def worker():
        # Сначала чиним уже сохранённые объявления с пустыми описаниями
        try:
            repair_link_only_listings()
        except Exception as e:
            logger.warning(f'[repair] Ошибка: {e}')
        # Обновляем устаревшие CDN-фото транспорта
        try:
            repair_transport_images()
        except Exception as e:
            logger.warning(f'[transport_repair] Ошибка: {e}')
        run_initial_fetch()
        run_monitoring_loop()

    thread = threading.Thread(target=worker, daemon=True, name='VietnamParsingParser')
    thread.start()
    logger.info("Parser started in background thread.")


if __name__ == '__main__':
    import sys
    if '--monitor-only' in sys.argv:
        _parser_state['status'] = 'monitoring'
        run_monitoring_loop()
    else:
        run_initial_fetch()
        if '--no-monitor' not in sys.argv:
            run_monitoring_loop()
