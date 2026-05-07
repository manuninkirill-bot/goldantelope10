import os
import asyncio
import requests
import json

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
    return requests.post(url, data=data).json()

def set_bot_commands():
    url = f'https://api.telegram.org/bot{_get_token()}/setMyCommands'
    commands = [
        {"command": "start", "description": "Запустить бота"},
        {"command": "app", "description": "Открыть мини-приложение"},
        {"command": "thailand", "description": "Каналы Тайланда"},
        {"command": "vietnam", "description": "Каналы Вьетнама"},
        {"command": "help", "description": "Помощь"}
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
    webapp_url = get_webapp_url()
    name = user_name or "друг"

    text = f'''🎭 <b>Развлекательный портал Юго-Восточной Азии</b>

Привет, {name}!

Афиша · События · Рестораны · Туры · Жильё · Транспорт

🇻🇳 Вьетнам  🇹🇭 Таиланд  🇮🇳 Индия  🇮🇩 Индонезия

Тысячи актуальных объявлений из проверенных Telegram-каналов — в одном месте, с фото и контактами.

👇 Выберите страну:'''

    keyboard = {
        "inline_keyboard": [
            [{"text": "🌏 Открыть каталог / Open catalog", "web_app": {"url": webapp_url}}],
            [
                {"text": "🇻🇳 Вьетнам", "web_app": {"url": f"{webapp_url}/?country=vietnam&lang=ru"}},
                {"text": "🇹🇭 Таиланд", "web_app": {"url": f"{webapp_url}/?country=thailand&lang=ru"}}
            ],
            [
                {"text": "🇮🇳 Индия", "web_app": {"url": f"{webapp_url}/?country=india&lang=ru"}},
                {"text": "🇮🇩 Индонезия", "web_app": {"url": f"{webapp_url}/?country=indonesia&lang=ru"}}
            ]
        ]
    }

    result = send_message(chat_id, text, keyboard)
    # Pin the welcome message so it stays at the top
    msg_id = result.get('result', {}).get('message_id') if result.get('ok') else None
    if msg_id:
        requests.post(
            f'https://api.telegram.org/bot{_get_token()}/pinChatMessage',
            json={'chat_id': chat_id, 'message_id': msg_id, 'disable_notification': True},
            timeout=10
        )
    
    return result

def handle_app(chat_id):
    webapp_url = get_webapp_url()
    
    text = "🚀 Нажмите кнопку, чтобы открыть мини-приложение:"
    
    keyboard = {
        "inline_keyboard": [
            [{"text": "📱 Открыть Goldantelope ASIA", "web_app": {"url": webapp_url}}]
        ]
    }
    
    return send_message(chat_id, text, keyboard)

def setup_bot():
    print("Setting up bot...")
    
    result1 = set_bot_commands()
    print(f"Commands: {result1}")
    
    result2 = set_menu_button()
    print(f"Menu button: {result2}")
    
    print(f"Web App URL: {get_webapp_url()}")
    print("Bot setup complete!")

if __name__ == "__main__":
    setup_bot()
