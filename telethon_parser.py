import os, asyncio, re, difflib, time, logging, io, threading
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto
from telethon.errors import FloodWaitError
from PIL import Image

log = logging.getLogger('tg_parser')

API_ID = 32881984
API_HASH = 'd2588f09dfbc5103ef77ef21c07dbf8b'
SESSION_FILE = 'parser_session.txt'

DEST = {
    'VIET': 'vietnamparsing',
    'THAI': 'thailandparsing',
    'BIKE': 'baykivietnam',
    'CHAT_VN': 'chatiparsing',
    'CHAT_TH': 'chatiparsing',
    'ENTERTAIN': 'gavibeshub',
    'MED': 'medvietnam',
}

CHAT_VN_CHANNELS = [
    'nhatrang_bg', 'NhaTrangchat', 'NhaTrang55', 'svoi_nhatrang',
    'zhenskiy_nhatrang', 'NhaTrangLady', 'NhaTrangSun',
    'Danang_Viet', 'danang_women', 'danangchat_ask', 'zhenskiy_danang',
    'Danang_people', 'Vietnam_Danang1', 'chat_danang', 'danang_chats',
    'phanthietchat111', 'Nyachang_Vietnam', 'onus_vietnam', 'Viza_Vietnam',
    'Dalat_Vietnam', 'vietnam_chat1', 'vietnam_chats',
    'HoChiMinh_Saigon', 'HoChiMinhChatik', 'hochiminh01_bg',
    'phu_quoc_chat', 'phuquoc_getmir_chat', 'fukuok_chat', 'chat_fukuok',
    'hanoichatvip',
]

CHAT_TH_CHANNELS = [
    'Phuket_chatBG', 'barakholka_pkhuket', 'chat_phuket', 'chats_phuket',
    'huahinrus', 'rentinthai', 'bangkok_chat_znakomstva', 'Bangkok_market_bg',
    'vse_svoi_bangkok', 'visa_thailand_chat', 'thailand_4at', 'rent_thailand_chat',
    'thailand_chatt1', 'chat_bangkok', 'Bangkok_chats', 'PattayaSale',
    'pattayachatonline', 'Pattayapar', 'chats_pattaya', 'phuketdating', 'KrabiChat',
]

ENTERTAIN_CHANNELS = [
    'nhatrang_tusa_afisha', 'vietnam_vn', 'Nhatrangvseobovsem', 'nyachangafisha',
    'nhatrang_afisha', 'introconcertvn', 'afisha_nhatrang', 'T2TNhaTrangevents',
    'nachang_tusa', 'drinkparty666', 'nyachang_ru',
    'danangnew', 'ads_danang', 'danang_tysa', 'danang_afisha',
]

MED_CHANNELS = [
    'viet_med', 'viet_medicine', 'viethandentalrus', 'VietnamDentist', 'doctor_viet',
    'Medicine_Vietnam', 'mediacenter_vietsovpetro_school', 'vietmedic', 'health_med_viet',
]

CHANNELS = {
    'THAI': [
        'arenda_phukets', 'THAILAND_REAL_ESTATE_PHUKET', 'housephuket', 'arenda_phuket_thailand',
        'phuket_nedvizhimost_rent', 'phuketsk_arenda', 'phuket_nedvizhimost_thailand', 'phuketsk_for_rent',
        'phuket_rentas', 'rentalsphuketonli', 'rentbuyphuket', 'Phuket_thailand05', 'nedvizhimost_pattaya',
        'arenda_pattaya', 'pattaya_realty_estate', 'HappyHomePattaya', 'sea_bangkok', 'Samui_for_you',
        'sea_phuket', 'realty_in_thailand', 'nedvig_thailand', 'thailand_nedvizhimost',
        'globe_nedvizhka_Thailand'
    ],
    'VIET': [
        'phuquoc_rent_wt', 'phyquocnedvigimost', 'Viet_Life_Phu_Quoc_rent', 'nhatrangapartment',
        'tanrealtorgh', 'viet_life_niachang', 'nychang_arenda', 'rent_nha_trang', 'nyachang_nedvizhimost',
        'nedvizimost_nhatrang', 'nhatrangforrent79', 'NhatrangRentl', 'arenda_v_nyachang', 'rent_appart_nha',
        'Arenda_Nyachang_Zhilye', 'NhaTrang_rental', 'realestatebythesea_1', 'NhaTrang_Luxury',
        'luckyhome_nhatrang', 'rentnhatrang', 'megasforrentnhatrang', 'viethome',
        'Vietnam_arenda', 'huynhtruonq', 'DaNangRentAFlat', 'danag_viet_life_rent', 'Danang_House',
        'DaNangApartmentRent', 'danang_arenda', 'arenda_v_danang', 'HoChiMinhRentI', 'hcmc_arenda',
        'Hanoirentapartment', 'HanoiRentl', 'Hanoi_Rent', 'PhuquocRentl'
    ],
    'BIKE': [
        'bike_nhatrang', 'motohub_nhatrang', 'NhaTrang_moto_market', 'RentBikeUniq',
        'BK_rental', 'nha_trang_rent', 'RentTwentyTwo22NhaTrang',
        'danang_bike_rent', 'bikerental1', 'viet_sovet'
    ],
}

STATS = {
    'running': False,
    'started_at': None,
    'user': None,
    'connected': {},
    'failed': {},
    'forwarded': 0,
    'photos': 0,
    'albums': 0,
    'dedup': 0,
    'errors': 0,
    'last_forward': None,
    'per_channel': {},
    'log': [],
}

_thread = None
_stop_event = threading.Event()

EMOJI_RE = re.compile(
    "[\U00010000-\U0010ffff\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0"
    "\U000024C2-\U0001F251]+",
    flags=re.UNICODE
)

H = []
SENT_ALBUM_SIZES = set()
SENT_SINGLE_SIZES = set()
PHASH_CACHE = []
MAX_DEDUP_CACHE = 2000
MAX_PHASH_CACHE = 5000
PHASH_THRESHOLD = 8


def _log(msg):
    log.info(msg)
    ts = time.strftime('%H:%M:%S')
    STATS['log'].append(f'[{ts}] {msg}')
    if len(STATS['log']) > 200:
        STATS['log'].pop(0)


def get_session():
    sess = os.environ.get('TELETHON_SESSION', '').strip()
    if sess:
        return sess
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE) as f:
            s = f.read().strip()
        if s:
            return s
    return ''


def _phash(img, hash_size=8):
    size = hash_size * 4
    img = img.convert('L').resize((size, size), Image.LANCZOS)
    pixels = list(img.getdata())
    mean = sum(pixels) / len(pixels)
    return [p > mean for p in pixels]


def _phash_dist(h1, h2):
    return sum(a != b for a, b in zip(h1, h2))


def clean_text(t):
    if not t:
        return ''
    t = EMOJI_RE.sub('', t)
    t = re.sub(r't\.me/\S+|http\S+', '', t)
    t = ' '.join(t.split())
    return t.strip()


def cl(t):
    if not t:
        return ''
    t = re.sub(r't\.me/\S+|http\S+|#[A-Za-z0-9_а-яА-ЯёЁ]+|Источник:.*', '', t, flags=re.I)
    t = re.sub(r'[^\w\s.,!?:;()\-+=%№"\'/]', '', t)
    return ' '.join(t.split())


def dup(t):
    if not t or len(t) < 20:
        return False
    c = re.sub(r'[^a-zA-Zа-яА-ЯёЁ0-9]', '', t)
    for o in H:
        if difflib.SequenceMatcher(None, c, o).ratio() > 0.88:
            return True
    H.append(c)
    if len(H) > 500:
        H.pop(0)
    return False


def photo_total_size(media):
    try:
        if hasattr(media, 'photo') and hasattr(media.photo, 'sizes'):
            return sum(getattr(s, 'size', 0) for s in media.photo.sizes if hasattr(s, 'size'))
    except Exception:
        pass
    return 0


def dup_by_size_single(media):
    sz = photo_total_size(media)
    if sz > 0:
        if sz in SENT_SINGLE_SIZES:
            return True
        SENT_SINGLE_SIZES.add(sz)
        if len(SENT_SINGLE_SIZES) > MAX_DEDUP_CACHE:
            SENT_SINGLE_SIZES.pop()
    return False


def dup_by_size_album(photos):
    if len(photos) < 2:
        return False
    total = sum(photo_total_size(m.media) for m in photos)
    if total > 0:
        key = (len(photos), total)
        if key in SENT_ALBUM_SIZES:
            return True
        SENT_ALBUM_SIZES.add(key)
        if len(SENT_ALBUM_SIZES) > MAX_DEDUP_CACHE:
            SENT_ALBUM_SIZES.pop()
    return False


async def compute_phash(client, media):
    try:
        buf = io.BytesIO()
        await client.download_media(media, file=buf, thumb=-1)
        buf.seek(0)
        img = Image.open(buf)
        h = _phash(img, hash_size=8)
        buf.close()
        return h
    except Exception as ex:
        log.debug(f'pHash error: {ex}')
        return None


def phash_is_dup(h):
    if h is None:
        return False
    for cached in PHASH_CACHE:
        if _phash_dist(h, cached) <= PHASH_THRESHOLD:
            return True
    PHASH_CACHE.append(h)
    if len(PHASH_CACHE) > MAX_PHASH_CACHE:
        PHASH_CACHE.pop(0)
    return False


def get_region(un):
    return next((r for r, l in CHANNELS.items() if any(x.lower() == un.lower() for x in l)), 'VIET')


async def _run_client(sess):
    client = TelegramClient(StringSession(sess), API_ID, API_HASH,
                            connection_retries=10, retry_delay=5, auto_reconnect=True)
    await client.connect()

    if not await client.is_user_authorized():
        _log('❌ Сессия недействительна! Нужна новая авторизация через /tg-auth')
        STATS['running'] = False
        return

    me = await client.get_me()
    STATS['user'] = f'{me.first_name} (id={me.id})'
    STATS['started_at'] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    STATS['running'] = True
    _log(f'✅ Авторизован: {me.first_name} (id={me.id})')

    all_ents = []
    chat_vn_names = list(CHAT_VN_CHANNELS)
    chat_th_names = list(CHAT_TH_CHANNELS)

    _log('Загружаю диалоги...')
    dialogs_map = {}
    try:
        async def _load_dialogs():
            async for dialog in client.iter_dialogs(limit=300):
                chat = dialog.entity
                un = getattr(chat, 'username', None)
                if un:
                    dialogs_map[un.lower()] = dialog.input_entity
        await asyncio.wait_for(_load_dialogs(), timeout=60)
        _log(f'Диалоги загружены: {len(dialogs_map)}')
    except asyncio.TimeoutError:
        _log(f'Диалоги: таймаут, получено {len(dialogs_map)} — продолжаю')
    except Exception as ex:
        _log(f'Ошибка диалогов: {ex}')

    for grp in ('THAI', 'VIET', 'BIKE'):
        names = CHANNELS[grp]
        ok, fail = [], []
        for n in names:
            key = n.lower()
            if key in dialogs_map:
                all_ents.append(dialogs_map[key])
                ok.append(n)
            else:
                try:
                    ent = await asyncio.wait_for(client.get_input_entity(n), timeout=10)
                    all_ents.append(ent)
                    ok.append(n)
                    await asyncio.sleep(0.3)
                except asyncio.TimeoutError:
                    fail.append(n)
                except FloodWaitError as fw:
                    await asyncio.sleep(min(fw.seconds, 30))
                    fail.append(n)
                except Exception:
                    fail.append(n)
        STATS['connected'][grp] = ok
        STATS['failed'][grp] = fail
        _log(f'[{grp}] → @{DEST[grp]}: {len(ok)}/{len(names)} OK, {len(fail)} не удалось')

    # Entity resolution для CHAT каналов (как THAI/VIET/BIKE)
    all_chat_ents = []
    for grp_name, chat_names in [('CHAT_VN', CHAT_VN_CHANNELS), ('CHAT_TH', CHAT_TH_CHANNELS)]:
        ok, fail = [], []
        for n in chat_names:
            key = n.lower()
            if key in dialogs_map:
                all_chat_ents.append(dialogs_map[key])
                ok.append(n)
            else:
                try:
                    ent = await asyncio.wait_for(client.get_input_entity(n), timeout=10)
                    all_chat_ents.append(ent)
                    ok.append(n)
                    await asyncio.sleep(0.3)
                except asyncio.TimeoutError:
                    fail.append(n)
                except FloodWaitError as fw:
                    await asyncio.sleep(min(fw.seconds, 30))
                    fail.append(n)
                except Exception:
                    fail.append(n)
        STATS['connected'][grp_name] = ok
        STATS['failed'][grp_name] = fail
        _log(f'[{grp_name}] → @{DEST[grp_name]}: {len(ok)}/{len(chat_names)} OK, {len(fail)} не удалось')

    # Entity resolution для ENTERTAIN каналов
    all_entertain_ents = []
    entertain_ok, entertain_fail = [], []
    for n in ENTERTAIN_CHANNELS:
        key = n.lower()
        if key in dialogs_map:
            all_entertain_ents.append(dialogs_map[key])
            entertain_ok.append(n)
        else:
            try:
                ent = await asyncio.wait_for(client.get_input_entity(n), timeout=10)
                all_entertain_ents.append(ent)
                entertain_ok.append(n)
                await asyncio.sleep(0.3)
            except asyncio.TimeoutError:
                entertain_fail.append(n)
            except FloodWaitError as fw:
                await asyncio.sleep(min(fw.seconds, 30))
                entertain_fail.append(n)
            except Exception:
                entertain_fail.append(n)
    STATS['connected']['ENTERTAIN'] = entertain_ok
    STATS['failed']['ENTERTAIN'] = entertain_fail
    _log(f'[ENTERTAIN] → @{DEST["ENTERTAIN"]}: {len(entertain_ok)}/{len(ENTERTAIN_CHANNELS)} OK, {len(entertain_fail)} не удалось')

    # Entity resolution для MED каналов
    all_med_ents = []
    med_ok, med_fail = [], []
    for n in MED_CHANNELS:
        key = n.lower()
        if key in dialogs_map:
            all_med_ents.append(dialogs_map[key])
            med_ok.append(n)
        else:
            try:
                ent = await asyncio.wait_for(client.get_input_entity(n), timeout=10)
                all_med_ents.append(ent)
                med_ok.append(n)
                await asyncio.sleep(0.3)
            except asyncio.TimeoutError:
                med_fail.append(n)
            except FloodWaitError as fw:
                await asyncio.sleep(min(fw.seconds, 30))
                med_fail.append(n)
            except Exception:
                med_fail.append(n)
    STATS['connected']['MED'] = med_ok
    STATS['failed']['MED'] = med_fail
    _log(f'[MED] → @{DEST["MED"]}: {len(med_ok)}/{len(MED_CHANNELS)} OK, {len(med_fail)} не удалось')

    total_ok = sum(len(v) for v in STATS['connected'].values())
    _log(f'Итого {total_ok} каналов. Слушаю новые сообщения...')

    chat_vn_set = {c.lower() for c in CHAT_VN_CHANNELS}
    chat_th_set = {c.lower() for c in CHAT_TH_CHANNELS}

    album_buffer = {}

    @client.on(events.NewMessage(chats=all_ents))
    async def handle_re(e):
        try:
            chat = await e.get_chat()
            un = (getattr(chat, 'username', None) or '').lower()
        except Exception:
            return

        region = get_region(un)
        dest = DEST[region]
        txt = cl(e.raw_text or e.text or '')
        if dup(txt):
            STATS['dedup'] += 1
            return

        try:
            gid = e.grouped_id
            if gid:
                if gid not in album_buffer:
                    album_buffer[gid] = []
                    asyncio.get_event_loop().call_later(2.0, lambda g=gid: asyncio.ensure_future(_flush_album(client, g, dest, un)))
                album_buffer[gid].append(e)
                return

            if e.media and isinstance(e.media, MessageMediaPhoto):
                if dup_by_size_single(e.media):
                    STATS['dedup'] += 1
                    return
                ph = await compute_phash(client, e.media)
                if phash_is_dup(ph):
                    STATS['dedup'] += 1
                    return
                await client.send_message(dest, txt or '.', file=e.media, parse_mode=None)
                STATS['photos'] += 1
            else:
                if not txt or len(txt) < 10:
                    return
                await client.send_message(dest, txt, parse_mode=None)

            STATS['forwarded'] += 1
            STATS['last_forward'] = time.strftime('%H:%M:%S UTC', time.gmtime())
            STATS['per_channel'][un] = STATS['per_channel'].get(un, 0) + 1
            _log(f'→ @{dest} [{region}] @{un}')
        except FloodWaitError as fw:
            _log(f'FloodWait {fw.seconds}s @{un}')
            await asyncio.sleep(fw.seconds + 5)
        except Exception as ex:
            STATS['errors'] += 1
            log.warning(f'Ошибка пересылки: {ex}')

    async def _flush_album(client, gid, dest, un):
        msgs = album_buffer.pop(gid, [])
        if not msgs:
            return
        if dup_by_size_album(msgs):
            STATS['dedup'] += 1
            return
        ph = await compute_phash(client, msgs[0].media)
        if phash_is_dup(ph):
            STATS['dedup'] += 1
            return
        txt = cl(next((m.raw_text or m.text or '' for m in msgs if m.raw_text or m.text), ''))
        try:
            files = [m.media for m in msgs if m.media]
            await client.send_message(dest, txt or '.', file=files, parse_mode=None)
            STATS['albums'] += 1
            STATS['forwarded'] += 1
            STATS['last_forward'] = time.strftime('%H:%M:%S UTC', time.gmtime())
            STATS['per_channel'][un] = STATS['per_channel'].get(un, 0) + 1
            _log(f'→ @{dest} [ALBUM {len(files)}ф] @{un}')
        except Exception as ex:
            STATS['errors'] += 1
            log.warning(f'Ошибка альбома: {ex}')

    all_chat_set = chat_vn_set | chat_th_set
    entertain_set = {c.lower() for c in entertain_ok}
    med_set = {c.lower() for c in med_ok}

    @client.on(events.NewMessage(chats=all_entertain_ents if all_entertain_ents else ENTERTAIN_CHANNELS))
    async def handle_entertain(e):
        try:
            chat = await e.get_chat()
            un = (getattr(chat, 'username', None) or '').lower()
        except Exception:
            return
        if un not in entertain_set:
            return
        t = clean_text(e.raw_text or e.text or '')
        if not t or len(t) < 3:
            return
        if len(t) > 2000:
            t = t[:1997] + '...'
        try:
            src = f'https://t.me/{un}/{e.id}'
            if e.media:
                await client.send_message(DEST['ENTERTAIN'], t or '.', file=e.media, parse_mode=None)
            else:
                await client.send_message(DEST['ENTERTAIN'], f'{t}\n\n{src}', parse_mode=None)
            STATS['forwarded'] += 1
            STATS['last_forward'] = time.strftime('%H:%M:%S UTC', time.gmtime())
            STATS['per_channel'][un] = STATS['per_channel'].get(un, 0) + 1
            _log(f'ENTERTAIN @{un} → @{DEST["ENTERTAIN"]}')
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 5)
        except Exception as ex:
            STATS['errors'] += 1

    @client.on(events.NewMessage(chats=all_med_ents if all_med_ents else MED_CHANNELS))
    async def handle_med(e):
        try:
            chat = await e.get_chat()
            un = (getattr(chat, 'username', None) or '').lower()
        except Exception:
            return
        if un not in med_set:
            return
        t = clean_text(e.raw_text or e.text or '')
        if not t or len(t) < 3:
            return
        if len(t) > 2000:
            t = t[:1997] + '...'
        try:
            src = f'https://t.me/{un}/{e.id}'
            if e.media:
                await client.send_message(DEST['MED'], t or '.', file=e.media, parse_mode=None)
            else:
                await client.send_message(DEST['MED'], f'{t}\n\n{src}', parse_mode=None)
            STATS['forwarded'] += 1
            STATS['last_forward'] = time.strftime('%H:%M:%S UTC', time.gmtime())
            STATS['per_channel'][un] = STATS['per_channel'].get(un, 0) + 1
            _log(f'MED @{un} → @{DEST["MED"]}')
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 5)
        except Exception as ex:
            STATS['errors'] += 1

    @client.on(events.NewMessage(chats=all_chat_ents if all_chat_ents else list(CHAT_VN_CHANNELS) + list(CHAT_TH_CHANNELS)))
    async def handle_chat(e):
        try:
            chat = await e.get_chat()
            un = (getattr(chat, 'username', None) or '').lower()
        except Exception:
            return
        if un not in all_chat_set or e.media:
            return
        t = clean_text(e.raw_text or e.text or '')
        if not t or len(t) < 3:
            return
        if len(t) > 300:
            t = t[:297] + '...'
        try:
            src = f'https://t.me/{un}/{e.id}'
            await client.send_message(DEST['CHAT_VN'], f'{t}\n\n{src}', parse_mode=None)
            STATS['forwarded'] += 1
            STATS['last_forward'] = time.strftime('%H:%M:%S UTC', time.gmtime())
            STATS['per_channel'][un] = STATS['per_channel'].get(un, 0) + 1
            country = 'VN' if un in chat_vn_set else 'TH'
            _log(f'CHAT-{country} @{un} → @{DEST["CHAT_VN"]}')
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 5)
        except Exception as ex:
            STATS['errors'] += 1

    _log('🚀 Парсер запущен!')
    await client.run_until_disconnected()


async def _main_loop():
    while not _stop_event.is_set():
        sess = get_session()
        if not sess:
            _log('⚠️ TELETHON_SESSION не задана. Авторизуйтесь через /tg-auth')
            await asyncio.sleep(30)
            continue
        try:
            await _run_client(sess)
        except Exception as ex:
            _log(f'Отключён: {ex}. Переподключаюсь через 30с...')
            STATS['running'] = False
        await asyncio.sleep(30)


def _thread_target():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_main_loop())


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_thread_target, name='tg-parser', daemon=True)
    _thread.start()
    _log('Поток парсера запущен')


def get_status():
    s = STATS
    lines = []
    if s['running']:
        lines.append(f"✅ Работает | Пользователь: {s['user']}")
        lines.append(f"Запущен: {s['started_at']}")
    else:
        lines.append('⏳ Ожидание / переподключение...')
    lines.append(f"Переслано: {s['forwarded']} (фото: {s['photos']}, альбомов: {s['albums']}, дедуп: {s['dedup']}, ошибок: {s['errors']})")
    if s['last_forward']:
        lines.append(f"Последний: {s['last_forward']}")
    for grp, names in s['connected'].items():
        lines.append(f"  [{grp}] {len(names)} каналов → @{DEST.get(grp,'?')}")
    failed = {k: v for k, v in s['failed'].items() if v}
    if failed:
        lines.append('❌ Не удалось:')
        for grp, names in failed.items():
            lines.append(f"  [{grp}] {len(names)} каналов")
    if s['log']:
        lines.append('\n--- Последние логи ---')
        lines.extend(s['log'][-20:])
    return '\n'.join(lines)
