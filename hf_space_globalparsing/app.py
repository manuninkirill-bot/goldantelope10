import os, asyncio, re, uvicorn, difflib, time, logging, io
from fastapi import FastAPI
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto
from telethon.errors import FloodWaitError
from PIL import Image

def _phash(img, hash_size=8):
    size = hash_size * 4
    img = img.convert('L').resize((size, size), Image.LANCZOS)
    pixels = list(img.getdata())
    mean = sum(pixels) / len(pixels)
    return [p > mean for p in pixels]

def _phash_dist(h1, h2):
    return sum(a != b for a, b in zip(h1, h2))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('parser')

app = FastAPI()

API_ID = int(os.environ.get('TELETHON_API_ID', '32881984'))
API_HASH = os.environ.get('TELETHON_API_HASH', 'd2588f09dfbc5103ef77ef21c07dbf8b')
SESS = os.environ.get('TELETHON_SESSION', '')

DEST = {
    'VIET': 'vietnamparsing',
    'THAI': 'thailandparsing',
    'BIKE': 'baykivietnam',
    'CHAT': 'chatiparsing',
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
    'Hanoi_Viet', 'hanoi24hhh', 'hanoi_chats', 'Danang16', 'hanoichatvip'
]

CHAT_TH_CHANNELS = [
    'Phuket_chatBG', 'barakholka_pkhuket', 'chat_phuket', 'chats_phuket',
    'huahinrus', 'rentinthai', 'bangkok_chat_znakomstva', 'Bangkok_market_bg',
    'vse_svoi_bangkok', 'visa_thailand_chat', 'thailand_4at', 'rent_thailand_chat',
    'thailand_chatt1', 'ThailandChat_INF', 'chat_thailand', 'Bangkok_chatBG',
    'chat_bangkok', 'Bangkok_chats', 'PattayaSale',
    'pattayachatonline', 'Pattayapar', 'chats_pattaya', 'phuketdating', 'KrabiChat'
]

ALL_CHAT_CHANNELS = CHAT_VN_CHANNELS + CHAT_TH_CHANNELS

RE_CHANNELS = {
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
        'BK_rental', 'nha_trang_rent', 'RentTwentyTwo22NhaTrang'
    ],
}

EMOJI_RE = re.compile(
    "[\U00010000-\U0010ffff\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0"
    "\U000024C2-\U0001F251]+",
    flags=re.UNICODE
)

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
}

H = []
SENT_ALBUM_SIZES = set()
SENT_SINGLE_SIZES = set()
PHASH_CACHE = []
MAX_DEDUP_CACHE = 2000
MAX_PHASH_CACHE = 5000
PHASH_THRESHOLD = 8


def cl(t):
    if not t: return ""
    t = re.sub(r"t\.me/\S+|http\S+|#[A-Za-z0-9_а-яА-ЯёЁ]+|Источник:.*", "", t, flags=re.I)
    t = re.sub(r'[^\w\s.,!?:;()\-+=%№"\'/]', '', t)
    return " ".join(t.split())


def clean_chat_text(t):
    if not t: return ""
    t = EMOJI_RE.sub('', t)
    t = re.sub(r't\.me/\S+|http\S+', '', t)
    return " ".join(t.split()).strip()


def dup(t):
    if not t or len(t) < 20: return False
    c = re.sub(r'[^a-zA-Zа-яА-ЯёЁ0-9]', '', t)
    for o in H:
        if difflib.SequenceMatcher(None, c, o).ratio() > 0.88: return True
    H.append(c)
    if len(H) > 500: H.pop(0)
    return False


def photo_total_size(media):
    try:
        if hasattr(media, 'photo') and hasattr(media.photo, 'sizes'):
            return sum(getattr(s, 'size', 0) for s in media.photo.sizes if hasattr(s, 'size'))
    except Exception:
        pass
    return 0


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


def dup_by_size_single(media):
    sz = photo_total_size(media)
    if sz > 0:
        if sz in SENT_SINGLE_SIZES:
            return True
        SENT_SINGLE_SIZES.add(sz)
        if len(SENT_SINGLE_SIZES) > MAX_DEDUP_CACHE:
            SENT_SINGLE_SIZES.pop()
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


async def dup_by_phash_single(client, media):
    h = await compute_phash(client, media)
    if h is not None and phash_is_dup(h):
        return True
    return False


async def dup_by_phash_album(client, photos):
    if not photos:
        return False
    h = await compute_phash(client, photos[0].media)
    return h is not None and phash_is_dup(h)


def get_region(un):
    return next((r for r, l in RE_CHANNELS.items() if any(x.lower() == un.lower() for x in l)), 'VIET')


async def start_client():
    if not SESS:
        log.error('TELETHON_SESSION not set!')
        return
    while True:
        try:
            await _run_client()
        except Exception as ex:
            log.error(f'Client disconnected: {ex}. Reconnecting in 30s...')
            STATS['running'] = False
            await asyncio.sleep(30)


async def _run_client():
    client = TelegramClient(StringSession(SESS), API_ID, API_HASH,
                            connection_retries=10, retry_delay=5, auto_reconnect=True)
    await client.connect()

    if not await client.is_user_authorized():
        log.error('Session invalid!')
        return

    me = await client.get_me()
    STATS['user'] = f'{me.first_name} (id={me.id})'
    STATS['started_at'] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    STATS['running'] = True
    log.info(f'Authorized: {me.first_name} (id={me.id})')

    all_re_ents = []
    log.info('Loading dialogs...')
    dialogs_map = {}
    try:
        async def _load_dialogs():
            async for dialog in client.iter_dialogs(limit=300):
                un = getattr(dialog.entity, 'username', None)
                if un:
                    dialogs_map[un.lower()] = dialog.input_entity
        await asyncio.wait_for(_load_dialogs(), timeout=60)
        log.info(f'Dialogs loaded: {len(dialogs_map)}')
    except asyncio.TimeoutError:
        log.warning(f'iter_dialogs timeout, got {len(dialogs_map)} — continuing')
    except Exception as ex:
        log.warning(f'Dialog load error: {ex}')

    for grp in ('THAI', 'VIET', 'BIKE'):
        names = RE_CHANNELS[grp]
        ok, fail = [], []
        for n in names:
            if n.lower() in dialogs_map:
                all_re_ents.append(dialogs_map[n.lower()])
                ok.append(n)
            else:
                try:
                    ent = await asyncio.wait_for(client.get_input_entity(n), timeout=10)
                    all_re_ents.append(ent)
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
        log.info(f'[{grp}] -> @{DEST[grp]}: {len(ok)}/{len(names)} ok, {len(fail)} failed')

    all_chat_ents = []
    chat_ok, chat_fail = [], []
    for n in ALL_CHAT_CHANNELS:
        if n.lower() in dialogs_map:
            all_chat_ents.append(dialogs_map[n.lower()])
            chat_ok.append(n)
        else:
            try:
                ent = await asyncio.wait_for(client.get_input_entity(n), timeout=10)
                all_chat_ents.append(ent)
                chat_ok.append(n)
                await asyncio.sleep(0.3)
            except asyncio.TimeoutError:
                chat_fail.append(n)
            except FloodWaitError as fw:
                await asyncio.sleep(min(fw.seconds, 30))
                chat_fail.append(n)
            except Exception:
                chat_fail.append(n)
    STATS['connected']['CHAT'] = chat_ok
    STATS['failed']['CHAT'] = chat_fail
    log.info(f'[CHAT] -> @{DEST["CHAT"]}: {len(chat_ok)}/{len(ALL_CHAT_CHANNELS)} ok, {len(chat_fail)} failed')

    all_chat_set = {c.lower() for c in chat_ok}
    album_buffer = {}

    @client.on(events.NewMessage(chats=all_re_ents))
    async def handle_re(e):
        try:
            chat = await e.get_chat()
            un = (getattr(chat, 'username', None) or '').lower()
        except Exception:
            return

        reg = get_region(un)
        txt = cl(e.raw_text or e.text or '')
        if dup(txt):
            STATS['dedup'] += 1
            return

        try:
            gid = e.grouped_id
            if gid:
                if gid not in album_buffer:
                    album_buffer[gid] = []
                    asyncio.get_event_loop().call_later(2.0,
                        lambda g=gid, r=reg, u=un: asyncio.ensure_future(flush_album(client, g, r, u)))
                album_buffer[gid].append(e)
                return

            if e.media and isinstance(e.media, MessageMediaPhoto):
                if dup_by_size_single(e.media):
                    STATS['dedup'] += 1
                    return
                if await dup_by_phash_single(client, e.media):
                    STATS['dedup'] += 1
                    STATS['phash_dedup'] = STATS.get('phash_dedup', 0) + 1
                    return
                src = f"https://t.me/{un}/{e.id}"
                cap = f"{txt}\n\n{src}".strip() if txt else src
                await client.send_message(DEST[reg], cap[:1020], file=e.media, parse_mode=None)
                STATS['forwarded'] += 1
                STATS['photos'] += 1
            else:
                if not txt or len(txt) < 10:
                    return
                src = f"https://t.me/{un}/{e.id}"
                await client.send_message(DEST[reg], f"{txt}\n\n{src}"[:4000], parse_mode=None)
                STATS['forwarded'] += 1

            STATS['last_forward'] = time.strftime('%H:%M:%S UTC', time.gmtime())
            STATS['per_channel'][un] = STATS['per_channel'].get(un, 0) + 1
            log.info(f'[{reg}] @{un} -> @{DEST[reg]} | total: {STATS["forwarded"]}')
        except FloodWaitError as fw:
            log.warning(f'FloodWait {fw.seconds}s')
            await asyncio.sleep(fw.seconds + 5)
        except Exception as ex:
            STATS['errors'] += 1
            log.warning(f'Forward error: {ex}')

    async def flush_album(client, gid, reg, un):
        msgs = album_buffer.pop(gid, [])
        if not msgs:
            return
        p = [m.media for m in msgs if m.media and isinstance(m.media, MessageMediaPhoto)]
        if not p:
            return
        if dup_by_size_album(msgs):
            STATS['dedup'] += 1
            return
        if await dup_by_phash_album(client, msgs):
            STATS['dedup'] += 1
            STATS['phash_dedup'] = STATS.get('phash_dedup', 0) + 1
            return
        longest = max((cl(m.raw_text or m.text or '') for m in msgs), key=len, default='')
        src = f"https://t.me/{un}/{msgs[0].id}"
        cap = f"{longest}\n\n{src}".strip() if longest else src
        try:
            await client.send_message(DEST[reg], cap[:1020], file=p, parse_mode=None)
            STATS['forwarded'] += 1
            STATS['photos'] += len(p)
            STATS['albums'] += 1
            STATS['last_forward'] = time.strftime('%H:%M:%S UTC', time.gmtime())
            STATS['per_channel'][un] = STATS['per_channel'].get(un, 0) + 1
            log.info(f'[{reg}] ALBUM @{un} -> @{DEST[reg]} | {len(p)} photos')
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 5)
        except Exception as ex:
            STATS['errors'] += 1
            log.warning(f'Album error: {ex}')

    @client.on(events.NewMessage(chats=all_chat_ents if all_chat_ents else ALL_CHAT_CHANNELS))
    async def handle_chat(e):
        try:
            chat = await e.get_chat()
            un = (getattr(chat, 'username', None) or '').lower()
        except Exception:
            return
        if un not in all_chat_set or e.media:
            return
        t = clean_chat_text(e.raw_text or e.text or '')
        if not t or len(t) < 3:
            return
        if len(t) > 300:
            t = t[:297] + '...'
        try:
            src = f"https://t.me/{un}/{e.id}"
            await client.send_message(DEST['CHAT'], f"{t}\n\n{src}", parse_mode=None)
            STATS['forwarded'] += 1
            STATS['last_forward'] = time.strftime('%H:%M:%S UTC', time.gmtime())
            STATS['per_channel'][un] = STATS['per_channel'].get(un, 0) + 1
            log.info(f'[CHAT] @{un} -> @{DEST["CHAT"]}')
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 5)
        except Exception as ex:
            STATS['errors'] += 1

    total = sum(len(v) for v in STATS['connected'].values())
    log.info(f'Total: {total} channels. Listening...')
    await client.run_until_disconnected()


@app.on_event("startup")
async def sup():
    asyncio.create_task(start_client())


@app.get("/")
async def root():
    return {
        "status": "running" if STATS['running'] else "starting",
        "user": STATS['user'],
        "started_at": STATS['started_at'],
        "connected": {k: len(v) for k, v in STATS['connected'].items()},
        "failed": {k: v for k, v in STATS['failed'].items() if v},
        "forwarded": STATS['forwarded'],
        "photos": STATS['photos'],
        "albums": STATS['albums'],
        "dedup": STATS['dedup'],
        "phash_dedup": STATS.get('phash_dedup', 0),
        "errors": STATS['errors'],
        "last_forward": STATS['last_forward'],
        "top_channels": dict(sorted(STATS['per_channel'].items(), key=lambda x: -x[1])[:15]),
    }


@app.get("/health")
async def health():
    return {"ok": True, "running": STATS['running']}


@app.get("/status")
async def status():
    return await root()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
