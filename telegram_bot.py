import os
import asyncio
import requests
import json
import threading

def _get_token():
    return os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()

def get_webapp_url():
    # 1. Explicit override
    explicit = os.environ.get('WEBAPP_URL', '').strip()
    if explicit:
        return explicit.rstrip('/')
    # 2. Replit published domain (production)
    domains = os.environ.get('REPLIT_DOMAINS', '')
    if domains:
        return f"https://{domains.split(',')[0]}"
    # 3. Replit dev domain
    dev_domain = os.environ.get('REPLIT_DEV_DOMAIN', '')
    if dev_domain:
        return f"https://{dev_domain}"
    # 4. Railway auto-domain
    railway = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '').strip()
    if railway:
        return f"https://{railway}"
    return "https://goldantelope-asia.replit.app"

def send_message(chat_id, text, reply_markup=None):
    url = f'https://api.telegram.org/bot{_get_token()}/sendMessage'
    data = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML'
    }
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    try:
        return requests.post(url, data=data, timeout=10).json()
    except Exception:
        return {}

def set_bot_description():
    token = _get_token()
    desc = (
        "Визаран Вьетнам — быстрые поездки на границу из Нячанга, Дананга и Хошимина.\n"
        "Также в приложении:\n"
        "• Аренда жилья во Вьетнаме\n"
        "• Обмен денег (USDT/VND)\n"
        "• Развлечения и экскурсии"
    )
    requests.post(
        f'https://api.telegram.org/bot{token}/setMyDescription',
        json={'description': desc},
        timeout=10
    )
    requests.post(
        f'https://api.telegram.org/bot{token}/setMyShortDescription',
        json={'short_description': 'Визаран, аренда жилья, обмен денег и развлечения во Вьетнаме'},
        timeout=10
    )

def set_bot_commands():
    url = f'https://api.telegram.org/bot{_get_token()}/setMyCommands'
    commands = [
        {"command": "start",    "description": "Запустить бота: меню и услуги во Вьетнаме"},
        {"command": "visarun",  "description": "Визаран Вьетнам: запись и актуальные цены"},
        {"command": "rent",     "description": "Аренда жилья: Нячанг, Дананг, Хошимин"},
        {"command": "exchange", "description": "Обмен денег: наличные и USDT (VND)"},
        {"command": "tours",    "description": "Развлечения: экскурсии, дайвинг, билеты"},
        {"command": "crypto",   "description": "Трейдинг: сигналы и личный кабинет"},
    ]
    data = {'commands': json.dumps(commands)}
    return requests.post(url, data=data).json()

def set_menu_button():
    url = f'https://api.telegram.org/bot{_get_token()}/setChatMenuButton'
    webapp_url = get_webapp_url()
    menu_button = {
        "type": "web_app",
        "text": "Открыть",
        "web_app": {"url": webapp_url}
    }
    data = {'menu_button': json.dumps(menu_button)}
    return requests.post(url, data=data).json()

def handle_start(chat_id, user_name):
    name = user_name or "друг"
    site_url = "https://goldantelopeasia.com"

    text = (
        f'🎭 <b>Развлекательный портал Юго-Восточной Азии</b>\n\n'
        f'Привет, {name}!\n\n'
        f'Афиша · События · Рестораны · Туры · Жильё · Транспорт\n\n'
        f'🇻🇳 Вьетнам  🇹🇭 Таиланд  🇮🇳 Индия  🇮🇩 Индонезия\n\n'
        f'Тысячи актуальных объявлений из проверенных Telegram-каналов — '
        f'в одном месте, с фото и контактами.\n\n'
        f'🌐 <a href="{site_url}">{site_url}</a>'
    )

    keyboard = {
        "inline_keyboard": [
            [{"text": "🌐 Открыть приложение", "url": site_url}],
            [
                {"text": "🇻🇳 Вьетнам", "url": f"{site_url}/?country=vietnam"},
                {"text": "🇹🇭 Таиланд", "url": f"{site_url}/?country=thailand"}
            ],
            [
                {"text": "🇮🇳 Индия", "url": f"{site_url}/?country=india"},
                {"text": "🇮🇩 Индонезия", "url": f"{site_url}/?country=indonesia"}
            ]
        ]
    }

    # Отправляем сообщение СРАЗУ — не блокируемся
    result = send_message(chat_id, text, keyboard)

    # Открепляем старые сообщения в фоне — не задерживает ответ
    def _unpin():
        try:
            requests.post(
                f'https://api.telegram.org/bot{_get_token()}/unpinAllChatMessages',
                json={'chat_id': chat_id},
                timeout=5
            )
        except Exception:
            pass
    threading.Thread(target=_unpin, daemon=True).start()

    return result

def handle_app(chat_id):
    site_url = "https://goldantelopeasia.com"
    
    text = "🚀 Нажмите кнопку, чтобы открыть приложение:"
    
    keyboard = {
        "inline_keyboard": [
            [{"text": "🌐 Открыть Goldantelope ASIA", "url": site_url}]
        ]
    }
    
    return send_message(chat_id, text, keyboard)

def setup_bot():
    print("Setting up bot...")

    set_bot_description()
    print("Description: set")

    result1 = set_bot_commands()
    print(f"Commands: {result1}")

    result2 = set_menu_button()
    print(f"Menu button: {result2}")

    print(f"Web App URL: {get_webapp_url()}")
    print("Bot setup complete!")

if __name__ == "__main__":
    setup_bot()
