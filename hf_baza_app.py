import asyncio
import json
import os
import threading
from datetime import datetime, timedelta, timezone

import gradio as gr
from huggingface_hub import HfApi
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    UserStatusOnline, UserStatusRecently, UserStatusOffline,
    UserStatusLastWeek, UserStatusLastMonth,
    PeerUser, MessageService,
)
from telethon.errors import (
    ChatAdminRequiredError, ChannelPrivateError, FloodWaitError
)

API_ID = 32881984
API_HASH = 'd2588f09dfbc5103ef77ef21c07dbf8b'
SESSION = os.environ.get('TELETHON_SESSION', '')
HF_TOKEN = os.environ.get('HF_TOKEN', '')
HF_REPO = 'poweramanita/tg-users-data'

CHAT_VN = [
    'nhatrang_bg','NhaTrangchat','NhaTrang55','svoi_nhatrang',
    'zhenskiy_nhatrang','NhaTrangLady','NhaTrangSun',
    'Danang_Viet','danang_women','danangchat_ask','zhenskiy_danang',
    'Danang_people','Vietnam_Danang1','chat_danang','danang_chats',
    'phanthietchat111','Nyachang_Vietnam','onus_vietnam','Viza_Vietnam',
    'Dalat_Vietnam','vietnam_chat1','vietnam_chats',
    'HoChiMinh_Saigon','HoChiMinhChatik','hochiminh01_bg',
    'phu_quoc_chat','phuquoc_getmir_chat','fukuok_chat','chat_fukuok',
    'hanoichatvip',
]
ENTERTAIN = [
    'nhatrang_tusa_afisha','nhatrang_affiche','nyachangafisha',
    'nhatrang_afisha','introconcertvn','afisha_nhatrang','T2TNhaTrangevents',
    'nachang_tusa','drinkparty666','nyachang_ru',
    'danangnew','ads_danang','danang_afisha','danangpals',
]
MED = [
    'viet_med','viet_medicine','viethandentalrus','VietnamDentist','doctor_viet',
    'Medicine_Vietnam','mediacenter_vietsovpetro_school','vietmedic','health_med_viet',
]
RE_VN = [
    'phuquoc_rent_wt','phyquocnedvigimost','Viet_Life_Phu_Quoc_rent','nhatrangapartment',
    'tanrealtorgh','viet_life_niachang','nychang_arenda','rent_nha_trang','nyachang_nedvizhimost',
    'nedvizimost_nhatrang','nhatrangforrent79','NhatrangRentl','arenda_v_nyachang','rent_appart_nha',
    'Arenda_Nyachang_Zhilye','NhaTrang_rental','realestatebythesea_1','NhaTrang_Luxury',
    'luckyhome_nhatrang','rentnhatrang','megasforrentnhatrang','viethome',
    'Vietnam_arenda','huynhtruonq','DaNangRentAFlat','danag_viet_life_rent','Danang_House',
    'DaNangApartmentRent','danang_arenda','arenda_v_danang','HoChiMinhRentI','hcmc_arenda',
    'Hanoirentapartment','HanoiRentl','Hanoi_Rent','PhuquocRentl',
]
BIKE_VN = [
    'bike_nhatrang','motohub_nhatrang','NhaTrang_moto_market','RentBikeUniq',
    'BK_rental','nha_trang_rent','RentTwentyTwo22NhaTrang',
    'danang_bike_rent','bikerental1','viet_sovet',
]
CHAT_TH = [
    'Phuket_chatBG','barakholka_pkhuket','chat_phuket','chats_phuket',
    'huahinrus','rentinthai','bangkok_chat_znakomstva','Bangkok_market_bg',
    'vse_svoi_bangkok','visa_thailand_chat','thailand_4at','rent_thailand_chat',
    'thailand_chatt1','chat_bangkok','Bangkok_chats','PattayaSale',
    'pattayachatonline','Pattayapar','chats_pattaya','phuketdating','KrabiChat',
]
RE_TH = [
    'arenda_phukets','THAILAND_REAL_ESTATE_PHUKET','housephuket','arenda_phuket_thailand',
    'phuket_nedvizhimost_rent','phuketsk_arenda','phuket_nedvizhimost_thailand','phuketsk_for_rent',
    'phuket_rentas','rentalsphuketonli','rentbuyphuket','Phuket_thailand05','nedvizhimost_pattaya',
    'arenda_pattaya','pattaya_realty_estate','HappyHomePattaya','sea_bangkok','Samui_for_you',
    'sea_phuket','realty_in_thailand','nedvig_thailand','thailand_nedvizhimost',
    'globe_nedvizhka_Thailand',
]
BIKE_TH = [
    'arenda_thailandd','thailand_market','rental_service_thailand',
    'samui_arenda2','motorrenta','nashi_phuket_auto',
    'thailand_drive','PKHUKET_BAYKOV','Pattaya_Arenda_ru',
    'pattaya_happy_auto','pattaya_arenda','pattayamoto',
]

vn_channels = (
    [('chat',c) for c in CHAT_VN] +
    [('entertainment',c) for c in ENTERTAIN] +
    [('medicine',c) for c in MED] +
    [('real_estate',c) for c in RE_VN] +
    [('transport',c) for c in BIKE_VN]
)
th_channels = (
    [('chat',c) for c in CHAT_TH] +
    [('real_estate',c) for c in RE_TH] +
    [('transport',c) for c in BIKE_TH]
)
TOTAL_VN = len(vn_channels)
TOTAL_TH = len(th_channels)
TOTAL = TOTAL_VN + TOTAL_TH

status = {'running':False,'done':False,'vn':0,'th':0,'idx':0,'cur':'','log':[],'errors':0}
RESULT = '/tmp/tg_users_database.json'

def log(m):
    t = datetime.now().strftime('%H:%M:%S')
    line = f"[{t}] {m}"
    status['log'].append(line)
    if len(status['log']) > 1000:
        status['log'] = status['log'][-700:]
    print(line, flush=True)

def get_status_text(st):
    if isinstance(st, UserStatusOnline): return 'online'
    if isinstance(st, UserStatusRecently): return 'recently'
    if isinstance(st, UserStatusOffline):
        if st.was_online: return st.was_online.strftime('%Y-%m-%d %H:%M')
        return 'offline'
    if isinstance(st, UserStatusLastWeek): return 'last_week'
    if isinstance(st, UserStatusLastMonth): return 'last_month'
    return 'unknown'

async def get_participants(client, entity):
    users = {}
    count = 0
    try:
        async for u in client.iter_participants(entity, aggressive=True):
            count += 1
            if u.bot:
                continue
            users[u.id] = {
                'user_id': u.id,
                'username': u.username or '',
                'first_name': u.first_name or '',
                'last_name': u.last_name or '',
                'phone': u.phone or '',
                'last_seen': get_status_text(u.status),
            }
    except ChatAdminRequiredError:
        pass
    except Exception:
        pass
    return users, count

async def get_message_authors(client, entity, months=6):
    users = {}
    msg_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)
    try:
        async for msg in client.iter_messages(entity, limit=None, offset_date=cutoff, reverse=True):
            msg_count += 1
            if isinstance(msg, MessageService):
                continue
            sender = msg.sender
            if sender is None:
                if msg.from_id and isinstance(msg.from_id, PeerUser):
                    uid = msg.from_id.user_id
                    if uid not in users:
                        try:
                            sender = await client.get_entity(uid)
                        except Exception:
                            users[uid] = {
                                'user_id': uid,
                                'username': '',
                                'first_name': '',
                                'last_name': '',
                                'phone': '',
                                'last_seen': 'from_messages',
                            }
                            continue
                else:
                    continue

            if sender and hasattr(sender, 'bot') and sender.bot:
                continue
            if sender and hasattr(sender, 'id'):
                if sender.id not in users:
                    users[sender.id] = {
                        'user_id': sender.id,
                        'username': getattr(sender, 'username', '') or '',
                        'first_name': getattr(sender, 'first_name', '') or '',
                        'last_name': getattr(sender, 'last_name', '') or '',
                        'phone': getattr(sender, 'phone', '') or '',
                        'last_seen': 'from_messages',
                    }

            if msg.fwd_from and msg.fwd_from.from_id and isinstance(msg.fwd_from.from_id, PeerUser):
                fwd_uid = msg.fwd_from.from_id.user_id
                if fwd_uid not in users:
                    try:
                        fwd_user = await client.get_entity(fwd_uid)
                        if not getattr(fwd_user, 'bot', False):
                            users[fwd_uid] = {
                                'user_id': fwd_uid,
                                'username': getattr(fwd_user, 'username', '') or '',
                                'first_name': getattr(fwd_user, 'first_name', '') or '',
                                'last_name': getattr(fwd_user, 'last_name', '') or '',
                                'phone': '',
                                'last_seen': 'forwarded',
                            }
                    except Exception:
                        users[fwd_uid] = {
                            'user_id': fwd_uid,
                            'username': '',
                            'first_name': '',
                            'last_name': '',
                            'phone': '',
                            'last_seen': 'forwarded',
                        }

            if msg_count % 5000 == 0:
                log(f"      ...{msg_count} сообщений, {len(users)} авторов")
    except FloodWaitError as e:
        w = min(e.seconds, 120)
        log(f"      FloodWait {e.seconds}с при чтении сообщений, жду {w}с")
        await asyncio.sleep(w + 3)
    except Exception as e:
        log(f"      Ошибка при чтении сообщений: {type(e).__name__}: {e}")
    return users, msg_count

async def do_channel(client, ch, cat):
    try:
        if isinstance(ch, int) or (isinstance(ch, str) and ch.lstrip('-').isdigit()):
            ent = await asyncio.wait_for(client.get_entity(int(ch)), timeout=15)
        else:
            ent = await asyncio.wait_for(client.get_entity(ch), timeout=15)

        title = getattr(ent, 'title', ch)
        pc = getattr(ent, 'participants_count', '?')
        log(f"  @{ch} ({cat}) [{title}] ~{pc} подп.")

        p_users, p_count = await asyncio.wait_for(
            get_participants(client, ent), timeout=600
        )
        log(f"    Участники: {len(p_users)} из {p_count}")

        m_users, m_count = await asyncio.wait_for(
            get_message_authors(client, ent, months=6), timeout=1800
        )
        log(f"    Сообщения (6мес): {m_count} сообщений, {len(m_users)} авторов")

        merged = dict(p_users)
        for uid, udata in m_users.items():
            if uid not in merged:
                merged[uid] = udata
            else:
                if not merged[uid]['username'] and udata['username']:
                    merged[uid]['username'] = udata['username']
                if not merged[uid]['first_name'] and udata['first_name']:
                    merged[uid]['first_name'] = udata['first_name']

        log(f"    ИТОГО: {len(merged)} уникальных (участники + авторы)")
        return list(merged.values())

    except ChatAdminRequiredError:
        log(f"  @{ch} — нет доступа")
        status['errors'] += 1
    except ChannelPrivateError:
        log(f"  @{ch} — приватный")
        status['errors'] += 1
    except FloodWaitError as e:
        w = min(e.seconds, 300)
        log(f"  @{ch} — FloodWait {e.seconds}с, жду {w}с")
        await asyncio.sleep(w + 3)
        return await do_channel(client, ch, cat)
    except asyncio.TimeoutError:
        log(f"  @{ch} — таймаут")
        status['errors'] += 1
    except Exception as e:
        log(f"  @{ch} — ошибка: {type(e).__name__}: {e}")
        status['errors'] += 1
    return []

def save(vn, th, st='in_progress'):
    both = set(vn.keys()) & set(th.keys())
    r = {
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'status': st,
        'mode': 'DEEP: participants + message_authors (6 months)',
        'stats': {
            'vietnam_unique': len(vn),
            'thailand_unique': len(th),
            'total_unique': len(set(vn.keys()) | set(th.keys())),
            'in_both_countries': len(both),
            'channels_vn': TOTAL_VN,
            'channels_th': TOTAL_TH,
        },
        'vietnam': list(vn.values()),
        'thailand': list(th.values()),
    }
    with open(RESULT, 'w') as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    try:
        if HF_TOKEN:
            api = HfApi(token=HF_TOKEN)
            api.upload_file(
                path_or_fileobj=RESULT,
                path_in_repo='tg_users_database.json',
                repo_id=HF_REPO,
                repo_type='dataset',
                commit_message=f'Deep: VN={len(vn)} TH={len(th)} ({st})',
            )
            log(f"  [HF: VN={len(vn)} TH={len(th)}]")
    except Exception as e:
        log(f"  [HF ошибка: {e}]")

async def run():
    status.update(running=True, done=False, log=[], vn=0, th=0, idx=0, errors=0)
    if not SESSION:
        log("ОШИБКА: TELETHON_SESSION не задана!")
        status['running'] = False
        return
    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        log("ОШИБКА: сессия не авторизована!")
        await client.disconnect()
        status['running'] = False
        return
    me = await client.get_me()
    log(f"Авторизован: {me.first_name} id={me.id}")
    log(f"РЕЖИМ: ГЛУБОКИЙ — участники + авторы сообщений за 6 месяцев")
    log(f"+ пересланные сообщения (forwarded authors)")
    log(f"Каналов: VN={TOTAL_VN} TH={TOTAL_TH} всего={TOTAL}")
    log("")

    vn_u, th_u = {}, {}

    log("=" * 60)
    log("ВЬЕТНАМ")
    log("=" * 60)
    for i, (cat, ch) in enumerate(vn_channels, 1):
        status['idx'] = i
        status['cur'] = f"VN [{i}/{TOTAL_VN}] @{ch}"
        log(f"\n[{i}/{TOTAL_VN}] @{ch}")
        for u in await do_channel(client, ch, cat):
            uid = u['user_id']
            if uid not in vn_u:
                vn_u[uid] = {
                    'user_id': uid,
                    'username': u['username'],
                    'first_name': u['first_name'],
                    'last_name': u['last_name'],
                    'phone': u['phone'],
                    'last_seen': u['last_seen'],
                    'channels': [],
                }
            if ch not in vn_u[uid]['channels']:
                vn_u[uid]['channels'].append(ch)
            if u.get('username') and not vn_u[uid]['username']:
                vn_u[uid]['username'] = u['username']
        status['vn'] = len(vn_u)
        if i % 3 == 0:
            save(vn_u, th_u)
            log(f"  [сохранено: VN={len(vn_u)}]")
        await asyncio.sleep(3)

    save(vn_u, th_u)
    log(f"\nВьетнам завершён: {len(vn_u)} уникальных")

    log("")
    log("=" * 60)
    log("ТАЙЛАНД")
    log("=" * 60)
    for i, (cat, ch) in enumerate(th_channels, 1):
        status['idx'] = TOTAL_VN + i
        status['cur'] = f"TH [{i}/{TOTAL_TH}] @{ch}"
        log(f"\n[{i}/{TOTAL_TH}] @{ch}")
        for u in await do_channel(client, ch, cat):
            uid = u['user_id']
            if uid not in th_u:
                th_u[uid] = {
                    'user_id': uid,
                    'username': u['username'],
                    'first_name': u['first_name'],
                    'last_name': u['last_name'],
                    'phone': u['phone'],
                    'last_seen': u['last_seen'],
                    'channels': [],
                }
            if ch not in th_u[uid]['channels']:
                th_u[uid]['channels'].append(ch)
            if u.get('username') and not th_u[uid]['username']:
                th_u[uid]['username'] = u['username']
        status['th'] = len(th_u)
        if i % 3 == 0:
            save(vn_u, th_u)
            log(f"  [сохранено: TH={len(th_u)}]")
        await asyncio.sleep(3)

    save(vn_u, th_u, 'complete')
    total = len(set(vn_u.keys()) | set(th_u.keys()))
    both = len(set(vn_u.keys()) & set(th_u.keys()))
    log("")
    log("=" * 60)
    log("ГОТОВО!")
    log(f"Вьетнам: {len(vn_u)} уникальных")
    log(f"Тайланд: {len(th_u)} уникальных")
    log(f"ВСЕГО: {total}")
    log(f"В обоих: {both}")
    log(f"Ошибок: {status['errors']}")
    log("=" * 60)
    await client.disconnect()
    status['running'] = False
    status['done'] = True

def start():
    if status['running']:
        return "Уже работает!"
    threading.Thread(target=lambda: asyncio.new_event_loop().run_until_complete(run()), daemon=True).start()
    return "ГЛУБОКИЙ сбор запущен! (участники + сообщения 6 мес + пересылки)"

def get_st():
    if not status['running'] and not status['done']:
        return "Ожидание. Нажмите Запустить."
    s = "РАБОТАЕТ" if status['running'] else "ЗАВЕРШЁН"
    return (
        f"Статус: {s}\n"
        f"Прогресс: {status['idx']}/{TOTAL} каналов\n"
        f"Текущий: {status['cur']}\n"
        f"VN: {status['vn']} | TH: {status['th']}\n"
        f"Ошибок: {status['errors']}"
    )

def get_log():
    return "\n".join(status['log'][-120:]) or "Пусто"

def dl():
    return RESULT if os.path.exists(RESULT) else None

with gr.Blocks(title="TG Deep Collector") as demo:
    gr.Markdown(f"# ГЛУБОКИЙ сбор: участники + авторы сообщений")
    gr.Markdown(f"**{TOTAL}** каналов | participants + messages (6 мес) + forwarded")
    with gr.Row():
        b1 = gr.Button("🚀 Запустить", variant="primary")
        b2 = gr.Button("🔄 Обновить")
    st = gr.Textbox(label="Статус", lines=6, interactive=False)
    lg = gr.Textbox(label="Лог", lines=25, interactive=False)
    b3 = gr.Button("📥 Скачать JSON")
    fo = gr.File(label="Результат")
    b1.click(fn=start, outputs=st)
    b2.click(fn=get_st, outputs=st)
    b2.click(fn=get_log, outputs=lg)
    b3.click(fn=dl, outputs=fo)
    demo.load(fn=get_st, outputs=st)

demo.launch(server_name="0.0.0.0", server_port=7860)
