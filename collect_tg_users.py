import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import UserStatusOnline, UserStatusRecently, UserStatusLastWeek, UserStatusLastMonth, UserStatusOffline
from telethon.errors import ChatAdminRequiredError, ChannelPrivateError, FloodWaitError

API_ID = 32881984
API_HASH = 'd2588f09dfbc5103ef77ef21c07dbf8b'
SESSION = os.environ.get('TELETHON_SESSION', '')

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
    'nhatrang_tusa_afisha', 'nhatrang_affiche', 'nyachangafisha',
    'nhatrang_afisha', 'introconcertvn', 'afisha_nhatrang', 'T2TNhaTrangevents',
    'nachang_tusa', 'drinkparty666', 'nyachang_ru',
    'danangnew', 'ads_danang', 'danang_afisha', 'danangpals',
]

MED_CHANNELS = [
    'viet_med', 'viet_medicine', 'viethandentalrus', 'VietnamDentist', 'doctor_viet',
    'Medicine_Vietnam', 'mediacenter_vietsovpetro_school', 'vietmedic', 'health_med_viet',
]

RE_CHANNELS_THAI = [
    'arenda_phukets', 'THAILAND_REAL_ESTATE_PHUKET', 'housephuket', 'arenda_phuket_thailand',
    'phuket_nedvizhimost_rent', 'phuketsk_arenda', 'phuket_nedvizhimost_thailand', 'phuketsk_for_rent',
    'phuket_rentas', 'rentalsphuketonli', 'rentbuyphuket', 'Phuket_thailand05', 'nedvizhimost_pattaya',
    'arenda_pattaya', 'pattaya_realty_estate', 'HappyHomePattaya', 'sea_bangkok', 'Samui_for_you',
    'sea_phuket', 'realty_in_thailand', 'nedvig_thailand', 'thailand_nedvizhimost',
    'globe_nedvizhka_Thailand',
]

RE_CHANNELS_VIET = [
    'phuquoc_rent_wt', 'phyquocnedvigimost', 'Viet_Life_Phu_Quoc_rent', 'nhatrangapartment',
    'tanrealtorgh', 'viet_life_niachang', 'nychang_arenda', 'rent_nha_trang', 'nyachang_nedvizhimost',
    'nedvizimost_nhatrang', 'nhatrangforrent79', 'NhatrangRentl', 'arenda_v_nyachang', 'rent_appart_nha',
    'Arenda_Nyachang_Zhilye', 'NhaTrang_rental', 'realestatebythesea_1', 'NhaTrang_Luxury',
    'luckyhome_nhatrang', 'rentnhatrang', 'megasforrentnhatrang', 'viethome',
    'Vietnam_arenda', 'huynhtruonq', 'DaNangRentAFlat', 'danag_viet_life_rent', 'Danang_House',
    'DaNangApartmentRent', 'danang_arenda', 'arenda_v_danang', 'HoChiMinhRentI', 'hcmc_arenda',
    'Hanoirentapartment', 'HanoiRentl', 'Hanoi_Rent', 'PhuquocRentl',
]

BIKE_CHANNELS_VIET = [
    'bike_nhatrang', 'motohub_nhatrang', 'NhaTrang_moto_market', 'RentBikeUniq',
    'BK_rental', 'nha_trang_rent', 'RentTwentyTwo22NhaTrang',
    'danang_bike_rent', 'bikerental1', 'viet_sovet',
]

BIKE_CHANNELS_THAI = [
    'arenda_thailandd', 'thailand_market', 'rental_service_thailand',
    'samui_arenda2', 'motorrenta', 'nashi_phuket_auto',
    'thailand_drive', 'PKHUKET_BAYKOV', 'Pattaya_Arenda_ru',
    'pattaya_happy_auto', 'pattaya_arenda', 'pattayamoto',
]

vietnam_channels = []
thailand_channels = []

for ch in CHAT_VN_CHANNELS:
    vietnam_channels.append(('chat', ch))
for ch in ENTERTAIN_CHANNELS:
    vietnam_channels.append(('entertainment', ch))
for ch in MED_CHANNELS:
    vietnam_channels.append(('medicine', ch))
for ch in RE_CHANNELS_VIET:
    vietnam_channels.append(('real_estate', ch))
for ch in BIKE_CHANNELS_VIET:
    vietnam_channels.append(('transport', ch))

for ch in CHAT_TH_CHANNELS:
    thailand_channels.append(('chat', ch))
for ch in RE_CHANNELS_THAI:
    thailand_channels.append(('real_estate', ch))
for ch in BIKE_CHANNELS_THAI:
    thailand_channels.append(('transport', ch))


def get_user_status(status):
    if status is None:
        return 'unknown'
    if isinstance(status, UserStatusOnline):
        return 'online'
    if isinstance(status, UserStatusRecently):
        return 'recently'
    if isinstance(status, UserStatusOffline):
        if status.was_online:
            return f'offline:{status.was_online.isoformat()}'
        return 'offline'
    if isinstance(status, UserStatusLastWeek):
        return 'last_week'
    if isinstance(status, UserStatusLastMonth):
        return 'last_month'
    return 'unknown'


def is_active_24h(status):
    if status is None:
        return False
    if isinstance(status, UserStatusOnline):
        return True
    if isinstance(status, UserStatusRecently):
        return True
    if isinstance(status, UserStatusOffline):
        if status.was_online:
            return status.was_online > datetime.now(timezone.utc) - timedelta(hours=24)
    return False


async def _fetch_participants(client, entity, timeout=45):
    users = []
    count = 0
    async for user in client.iter_participants(entity, limit=5000):
        count += 1
        if user.bot:
            continue
        if not is_active_24h(user.status):
            continue
        users.append({
            'user_id': user.id,
            'username': user.username or '',
        })
    return users, count


async def collect_from_channel(client, channel_username, category, country):
    try:
        entity = await asyncio.wait_for(client.get_entity(channel_username), timeout=10)
        print(f"  [{country.upper()}] @{channel_username} ({category}) — получаю участников...", flush=True)
        try:
            users, count = await asyncio.wait_for(_fetch_participants(client, entity), timeout=60)
            print(f"    -> всего {count} участников, активных за 24ч: {len(users)}", flush=True)
            return users
        except asyncio.TimeoutError:
            print(f"    -> таймаут (60с), пропускаю", flush=True)
            return []
    except ChatAdminRequiredError:
        print(f"  [{country.upper()}] @{channel_username} — нет доступа (нужны права админа)", flush=True)
    except ChannelPrivateError:
        print(f"  [{country.upper()}] @{channel_username} — канал приватный", flush=True)
    except FloodWaitError as e:
        wait = min(e.seconds, 60)
        print(f"  [{country.upper()}] @{channel_username} — FloodWait {e.seconds}с, жду {wait}с...", flush=True)
        await asyncio.sleep(wait + 2)
        return await collect_from_channel(client, channel_username, category, country)
    except asyncio.TimeoutError:
        print(f"  [{country.upper()}] @{channel_username} — таймаут на get_entity, пропускаю", flush=True)
    except Exception as e:
        print(f"  [{country.upper()}] @{channel_username} — ошибка: {e}", flush=True)
    return []


async def main():
    if not SESSION:
        print("TELETHON_SESSION не задана!")
        return

    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        print("Сессия не авторизована!")
        await client.disconnect()
        return

    me = await client.get_me()
    print(f"Авторизован как: {me.first_name} (@{me.username}), id={me.id}")
    print(f"Начинаю сбор пользователей...")
    print(f"Вьетнам: {len(vietnam_channels)} каналов")
    print(f"Тайланд: {len(thailand_channels)} каналов")
    print()

    vn_users = {}
    th_users = {}

    def save_progress():
        both = set(vn_users.keys()) & set(th_users.keys())
        result = {
            'collected_at': datetime.now(timezone.utc).isoformat(),
            'status': 'in_progress',
            'stats': {
                'vietnam_unique_users': len(vn_users),
                'thailand_unique_users': len(th_users),
                'users_in_both': len(both),
            },
            'vietnam': list(vn_users.values()),
            'thailand': list(th_users.values()),
        }
        with open('tg_users_database.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print("ВЬЕТНАМ")
    print("=" * 60)
    for i, (category, ch) in enumerate(vietnam_channels, 1):
        print(f"\n[{i}/{len(vietnam_channels)}] @{ch}")
        users = await collect_from_channel(client, ch, category, 'vietnam')
        for u in users:
            uid = u['user_id']
            if uid not in vn_users:
                vn_users[uid] = {
                    'user_id': uid,
                    'username': u['username'],
                    'channels': [],
                }
            if ch not in vn_users[uid]['channels']:
                vn_users[uid]['channels'].append(ch)
        if i % 5 == 0:
            save_progress()
            print(f"  [промежуточно сохранено: VN={len(vn_users)}]", flush=True)
        await asyncio.sleep(0.3)

    save_progress()
    print(f"\nВьетнам завершён: {len(vn_users)} уникальных пользователей", flush=True)

    print()
    print("=" * 60)
    print("ТАЙЛАНД")
    print("=" * 60)
    for i, (category, ch) in enumerate(thailand_channels, 1):
        print(f"\n[{i}/{len(thailand_channels)}] @{ch}", flush=True)
        users = await collect_from_channel(client, ch, category, 'thailand')
        for u in users:
            uid = u['user_id']
            if uid not in th_users:
                th_users[uid] = {
                    'user_id': uid,
                    'username': u['username'],
                    'channels': [],
                }
            if ch not in th_users[uid]['channels']:
                th_users[uid]['channels'].append(ch)
        if i % 5 == 0:
            save_progress()
            print(f"  [промежуточно сохранено: TH={len(th_users)}]", flush=True)
        await asyncio.sleep(0.3)

    both = set(vn_users.keys()) & set(th_users.keys())

    result = {
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'stats': {
            'vietnam_unique_users': len(vn_users),
            'thailand_unique_users': len(th_users),
            'users_in_both': len(both),
            'vietnam_channels_scanned': len(vietnam_channels),
            'thailand_channels_scanned': len(thailand_channels),
        },
        'vietnam': list(vn_users.values()),
        'thailand': list(th_users.values()),
    }

    with open('tg_users_database.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 60)
    print("ГОТОВО!")
    print(f"Вьетнам: {len(vn_users)} уникальных пользователей")
    print(f"Тайланд: {len(th_users)} уникальных пользователей")
    print(f"Есть в обоих: {len(both)}")
    print(f"Результат сохранён в tg_users_database.json")
    print("=" * 60)

    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
