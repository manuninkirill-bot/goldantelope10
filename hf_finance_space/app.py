import os
import re
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import gradio as gr
from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

API_ID = int(os.environ.get("API_ID", "38261764"))
API_HASH = os.environ.get("API_HASH", "9a28366ae819d01b5ba5ad6d5de4baa8")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
TARGET_CHANNEL = os.environ.get("TARGET_CHANNEL", "obmenvietnam")

SOURCE_GROUPS = {
    "exchange_vn_dn": "Дананг",
    "obmen_valyut_Nhatrang": "Нячанг",
    "obmendengi24": "Фукуок",
}

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001f926-\U0001f937"
    "\U00010000-\U0010ffff"
    "\u2640-\u2642"
    "\u2600-\u2B55"
    "\u200d"
    "\u23cf"
    "\u23e9"
    "\u231a"
    "\ufe0f"
    "\u3030"
    "]+",
    flags=re.UNICODE,
)

STATUS_LOG = []
MAX_LOG = 200


def strip_emoji(text: str) -> str:
    return EMOJI_PATTERN.sub("", text).strip()


def add_log(msg: str):
    ts = datetime.now(timezone(timedelta(hours=7))).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    STATUS_LOG.append(line)
    if len(STATUS_LOG) > MAX_LOG:
        STATUS_LOG.pop(0)
    logger.info(msg)


client = None
_started = False


async def run_bot():
    global client, _started
    if _started:
        add_log("Бот уже запущен")
        return
    _started = True

    if not SESSION_STRING:
        add_log("SESSION_STRING не задан!")
        return
    add_log(f"SESSION_STRING длина: {len(SESSION_STRING)} символов")

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        add_log(f"Сессия не авторизована (len={len(SESSION_STRING)}, starts={SESSION_STRING[:10]}...)")
        add_log("Попробуем повторно...")
        await client.disconnect()
        await asyncio.sleep(2)
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            add_log("Повторная попытка не удалась. Проверьте SESSION_STRING")
            return
    me = await client.get_me()
    if not me:
        add_log("Не удалось получить данные пользователя")
        return
    add_log(f"Авторизован как {me.first_name} ({me.phone})")

    target = await client.get_entity(TARGET_CHANNEL)
    add_log(f"Целевой канал: @{TARGET_CHANNEL} (id={target.id})")

    add_log("Очистка старых сообщений из канала...")
    old_ids = []
    async for msg in client.iter_messages(target):
        old_ids.append(msg.id)
    if old_ids:
        for i in range(0, len(old_ids), 100):
            batch = old_ids[i:i+100]
            try:
                await client.delete_messages(target, batch)
            except Exception as e:
                add_log(f"  Ошибка удаления: {e}")
            await asyncio.sleep(0.5)
        add_log(f"Удалено {len(old_ids)} старых сообщений")
    else:
        add_log("Канал уже пуст")

    source_entities = {}
    for username, city in SOURCE_GROUPS.items():
        try:
            ent = await client.get_entity(username)
            source_entities[ent.id] = (username, city)
            add_log(f"Источник: @{username} ({city}) id={ent.id}")
        except Exception as e:
            add_log(f"Не удалось подключить @{username}: {e}")

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total_synced = 0
    for ent_id, (username, city) in source_entities.items():
        try:
            add_log(f"Синхронизация сегодняшних сообщений из @{username} ({city})...")
            count = 0
            async for msg in client.iter_messages(ent_id, offset_date=today_start, reverse=True):
                if msg.date < today_start:
                    continue
                text = msg.text or msg.message or ""
                if not text.strip():
                    if msg.media:
                        try:
                            await client.send_message(target, file=msg.media, message=f"[{city}]")
                            count += 1
                        except Exception as e:
                            add_log(f"  Ошибка пересылки медиа #{msg.id}: {e}")
                    continue
                clean = strip_emoji(text)
                if not clean:
                    continue
                fwd_text = f"[{city}]\n{clean}"
                try:
                    if msg.media:
                        await client.send_message(target, fwd_text, file=msg.media)
                    else:
                        await client.send_message(target, fwd_text)
                    count += 1
                except Exception as e:
                    add_log(f"  Ошибка пересылки #{msg.id}: {e}")
                await asyncio.sleep(1.5)
            add_log(f"  @{username}: переслано {count} сообщений за сегодня")
            total_synced += count
        except Exception as e:
            add_log(f"  Ошибка синхронизации @{username}: {e}")

    add_log(f"Синхронизация завершена: {total_synced} сообщений")

    source_ids = set(source_entities.keys())

    @client.on(events.NewMessage(chats=list(source_ids)))
    async def handler(event):
        chat_id = event.chat_id
        info = source_entities.get(chat_id)
        if not info:
            return
        username, city = info
        text = event.message.text or event.message.message or ""
        if not text.strip():
            if event.message.media:
                try:
                    await client.send_message(target, file=event.message.media, message=f"[{city}]")
                    add_log(f"Медиа из @{username} → @{TARGET_CHANNEL}")
                except Exception as e:
                    add_log(f"Ошибка медиа @{username}: {e}")
            return
        clean = strip_emoji(text)
        if not clean:
            return
        fwd_text = f"[{city}]\n{clean}"
        try:
            if event.message.media:
                await client.send_message(target, fwd_text, file=event.message.media)
            else:
                await client.send_message(target, fwd_text)
            add_log(f"@{username} → @{TARGET_CHANNEL}: {clean[:60]}")
        except Exception as e:
            add_log(f"Ошибка @{username}: {e}")

    add_log("Мониторинг новых сообщений запущен...")
    await client.run_until_disconnected()


def start_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_bot())


def get_status():
    return "\n".join(STATUS_LOG[-50:]) if STATUS_LOG else "Бот ещё не запущен"


import threading
bot_thread = threading.Thread(target=start_bot, daemon=True)
bot_thread.start()

with gr.Blocks(title="Finance Exchange Monitor") as demo:
    gr.Markdown("## 💱 Exchange Monitor → @obmenvietnam")
    gr.Markdown(
        "Источники: @exchange_vn_dn (Дананг) · "
        "@obmen_valyut_Nhatrang (Нячанг) · "
        "@obmendengi24 (Фукуок)"
    )
    status = gr.Textbox(label="Лог", lines=20, interactive=False, value=get_status)
    refresh_btn = gr.Button("Обновить лог")
    refresh_btn.click(fn=get_status, outputs=status)
    timer = gr.Timer(10)
    timer.tick(fn=get_status, outputs=status)

demo.launch(server_name="0.0.0.0", server_port=7860)
