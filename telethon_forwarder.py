import asyncio, os, re, difflib, threading, logging, time
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto
from telethon.errors import AuthKeyDuplicatedError, FloodWaitError

log = logging.getLogger('telethon_fwd')

API_ID = 32881984
API_HASH = 'd2588f09dfbc5103ef77ef21c07dbf8b'

DEST = {
    'VIET': 'vietnamparsing',
    'THAI': 'thailandparsing',
    'BIKE': 'baykivietnam',
    'ENTERTAIN': 'gavibeshub',
    'MED': 'medvietnam',
}

SOURCES = {
    'THAI': [
        'arenda_phukets','THAILAND_REAL_ESTATE_PHUKET','housephuket','arenda_phuket_thailand',
        'phuket_nedvizhimost_rent','phuketsk_arenda','phuket_nedvizhimost_thailand','phuketsk_for_rent',
        'phuket_rentas','rentalsphuketonli','rentbuyphuket','Phuket_thailand05','nedvizhimost_pattaya',
        'arenda_pattaya','pattaya_realty_estate','HappyHomePattaya','sea_bangkok','Samui_for_you',
        'sea_phuket','realty_in_thailand','nedvig_thailand','thailand_nedvizhimost','globe_nedvizhka_Thailand',
    ],
    'VIET': [
        'phuquoc_rent_wt','phyquocnedvigimost','Viet_Life_Phu_Quoc_rent','nhatrangapartment',
        'tanrealtorgh','viet_life_niachang','nychang_arenda','rent_nha_trang','nyachang_nedvizhimost',
        'nedvizimost_nhatrang','nhatrangforrent79','NhatrangRentl','arenda_v_nyachang','rent_appart_nha',
        'Arenda_Nyachang_Zhilye','NhaTrang_rental','realestatebythesea_1','NhaTrang_Luxury',
        'luckyhome_nhatrang','rentnhatrang','megasforrentnhatrang','viethome',
        'Vietnam_arenda','huynhtruonq','DaNangRentAFlat','danag_viet_life_rent','Danang_House',
        'DaNangApartmentRent','danang_arenda','arenda_v_danang','HoChiMinhRentI','hcmc_arenda',
        'Hanoirentapartment','HanoiRentl','Hanoi_Rent','PhuquocRentl',
    ],
    'BIKE': [
        'bike_nhatrang','motohub_nhatrang','NhaTrang_moto_market','RentBikeUniq',
        'BK_rental','nha_trang_rent','RentTwentyTwo22NhaTrang',
        'danang_bike_rent','bikerental1','viet_sovet',
    ],
    'ENTERTAIN': [
        'nhatrang_tusa_afisha','vietnam_vn','Nhatrangvseobovsem','nyachangafisha',
        'nhatrang_afisha','introconcertvn','afisha_nhatrang','T2TNhaTrangevents',
        'nachang_tusa','drinkparty666','nyachang_ru',
        'danangnew','ads_danang','danang_tysa','danang_afisha',
    ],
    'MED': [
        'viet_med','viet_medicine','viethandentalrus','VietnamDentist','doctor_viet',
        'Medicine_Vietnam','mediacenter_vietsovpetro_school','vietmedic','health_med_viet',
    ],
}

STATS = {
    'running': False,
    'user': None,
    'started_at': None,
    'connected': {},
    'failed': {},
    'forwarded': {},
    'total_messages': 0,
    'total_photos': 0,
    'total_albums': 0,
}

_history = []

def _cl(t):
    if not t: return ''
    t = re.sub(r't\.me/\S+|http\S+|#[A-Za-z0-9_а-яА-ЯёЁ]+|Источник:.*', '', t, flags=re.I)
    t = re.sub(r'[^\w\s.,!?:;()\-+=%№"\'/]', '', t)
    return ' '.join(t.split())

def _dup(t):
    if not t or len(t) < 20: return False
    c = re.sub(r'[^a-zA-Zа-яА-ЯёЁ0-9]', '', t)
    for o in _history:
        if difflib.SequenceMatcher(None, c, o).ratio() > 0.88: return True
    _history.append(c)
    if len(_history) > 500: _history.pop(0)
    return False

async def _run(sess_str):
    client = TelegramClient(StringSession(sess_str), API_ID, API_HASH,
                            connection_retries=5, retry_delay=5)
    await client.connect()

    if not await client.is_user_authorized():
        log.error('Telethon: сессия невалидна!')
        return

    me = await client.get_me()
    STATS['user'] = f'{me.first_name} (id={me.id})'
    STATS['started_at'] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    STATS['running'] = True
    log.info(f'Telethon: авторизован {me.first_name} (id={me.id})')

    all_ents = []
    log.info('Загружаю диалоги (один запрос вместо ResolveUsername для каждого канала):')
    log.info('=' * 55)

    # Загружаем все диалоги аккаунта одним запросом — без FloodWait по username
    dialogs_map = {}  # username.lower() → InputChannel entity
    try:
        async for dialog in client.iter_dialogs():
            chat = dialog.entity
            un = getattr(chat, 'username', None)
            if un:
                dialogs_map[un.lower()] = dialog.input_entity
        log.info(f'Диалогов загружено: {len(dialogs_map)}')
    except Exception as ex:
        log.warning(f'Ошибка загрузки диалогов: {ex}')

    # Сопоставляем каналы из SOURCES с диалогами
    for grp, names in SOURCES.items():
        ok, fail = [], []
        for n in names:
            key = n.lower()
            if key in dialogs_map:
                all_ents.append(dialogs_map[key])
                ok.append(n)
            else:
                # Пробуем get_input_entity только для неизвестных каналов
                try:
                    ent = await client.get_input_entity(n)
                    all_ents.append(ent)
                    ok.append(n)
                    await asyncio.sleep(0.5)
                except FloodWaitError as fw:
                    log.warning(f'  ⏳ FloodWait {fw.seconds}s @{n} — пропускаем')
                    fail.append(n)
                except Exception as e:
                    fail.append(n)
                    log.debug(f'  ❌ @{n}: {str(e)[:60]}')
        STATS['connected'][grp] = ok
        STATS['failed'][grp] = fail
        STATS['forwarded'][grp] = {'messages': 0, 'photos': 0, 'albums': 0}
        log.info(f'  [{grp}] → @{DEST[grp]}: ✅{len(ok)}/{len(names)} | ❌{len(fail)}')

    total_ok = sum(len(v) for v in STATS['connected'].values())
    log.info('=' * 55)
    log.info(f'Итого: {total_ok} каналов. Слушаю сообщения...')

    @client.on(events.NewMessage(chats=all_ents))
    async def on_msg(e):
        if e.grouped_id or not e.media or not isinstance(e.media, MessageMediaPhoto): return
        t = e.raw_text or ''
        if len(t) < 15 or _dup(t): return
        try:
            chat = await e.get_chat()
            un = chat.username.lower() if hasattr(chat, 'username') and chat.username else str(e.chat_id)
            grp = next((g for g, l in SOURCES.items() if any(x.lower() == un for x in l)), 'VIET')
            footer = f'\n\n📌 @{un} | ID: {e.id}\n🔗 https://t.me/{un}/{e.id}'
            body = _cl(t)
            msg = (body[:1020 - len(footer)] + footer) if body else footer.strip()
            await client.send_message(DEST[grp], msg, file=e.media, parse_mode=None)
            STATS['forwarded'][grp]['messages'] += 1
            STATS['forwarded'][grp]['photos'] += 1
            STATS['total_messages'] += 1
            STATS['total_photos'] += 1
            log.info(f'📨 @{un}→@{DEST[grp]} | 📸1 | итого: {STATS["total_messages"]} сообщ / {STATS["total_photos"]} фото')
        except Exception as ex:
            log.warning(f'Ошибка пересылки: {ex}')

    @client.on(events.Album(chats=all_ents))
    async def on_album(e):
        p = [m for m in e.messages if isinstance(m.media, MessageMediaPhoto)]
        if not p or _dup(e.text): return
        try:
            chat = await e.get_chat()
            un = chat.username.lower() if hasattr(chat, 'username') and chat.username else str(e.chat_id)
            grp = next((g for g, l in SOURCES.items() if any(x.lower() == un for x in l)), 'VIET')
            footer = f'\n\n📌 @{un} | ID: {e.messages[0].id}\n🔗 https://t.me/{un}/{e.messages[0].id}'
            body = _cl(e.text)
            msg = (body[:1020 - len(footer)] + footer) if body else footer.strip()
            await client.send_message(DEST[grp], msg, file=p, parse_mode=None)
            STATS['forwarded'][grp]['messages'] += 1
            STATS['forwarded'][grp]['photos'] += len(p)
            STATS['forwarded'][grp]['albums'] += 1
            STATS['total_messages'] += 1
            STATS['total_photos'] += len(p)
            log.info(f'🖼️  @{un}→@{DEST[grp]} | 📸{len(p)} альб | итого: {STATS["total_messages"]} / {STATS["total_photos"]} фото')
        except Exception as ex:
            log.warning(f'Ошибка альбома: {ex}')

    await client.run_until_disconnected()

def start_forwarder(sess_str: str):
    def _thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run(sess_str))
        except AuthKeyDuplicatedError:
            log.error('AuthKeyDuplicated — перезапустите приложение')
        except Exception as e:
            log.error(f'Telethon forwarder ошибка: {e}')
        finally:
            STATS['running'] = False
            loop.close()

    t = threading.Thread(target=_thread, daemon=True, name='TelethonForwarder')
    t.start()
    log.info('Telethon forwarder поток запущен')
    return t
