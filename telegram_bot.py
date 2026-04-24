import os
import asyncio
import requests
import json

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

def get_webapp_url():
    # 1. Explicit override (set this in Railway/production)
    explicit = os.environ.get('WEBAPP_URL', '').strip()
    if explicit:
        return explicit.rstrip('/')
    # 2. Railway auto-domain
    railway = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '').strip()
    if railway:
        return f"https://{railway}"
    # 3. Replit dev domain
    domains = os.environ.get('REPLIT_DOMAINS', '')
    if domains:
        return f"https://{domains.split(',')[0]}"
    return "https://goldantelope-asia.replit.app"

def send_message(chat_id, text, reply_markup=None):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    data = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML'
    }
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    return requests.post(url, data=data).json()

def set_bot_commands():
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands'
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
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/setChatMenuButton'
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

    text = f'''🦌 <b>Goldantelope ASIA</b>

👋 Добрый день, {name}! / Xin chào buổi sáng, {name}!

━━━━━━━━━━━━━━━━━━━━
🇷🇺 <b>Крупнейший русскоязычный агрегатор объявлений по Вьетнаму и Таиланду.</b>

Мы автоматически собираем тысячи актуальных предложений из десятков Telegram-каналов — всё в одном удобном месте, с фото, ценами и контактами.

🏠 <b>Недвижимость</b> — 5 000+ объектов аренды и покупки
   📍 Нячанг · Дананг · Хошимин · Ханой · Фукуок
   📍 Пхукет · Бангкок · Паттайя · Самуи

🍽 <b>Рестораны</b> — 650+ заведений с описанием и адресами

🛵 <b>Транспорт</b> — байки, авто, трансферы

🎯 <b>Экскурсии</b> — туры и активности

💱 <b>Обмен валют</b> — курсы VND и THB

🏥 <b>Сервисы</b> — медицина, визы, детям, барахолка

━━━━━━━━━━━━━━━━━━━━
🇻🇳 <b>Nền tảng tổng hợp tin đăng lớn nhất bằng tiếng Nga về Việt Nam và Thái Lan.</b>

Chúng tôi tự động thu thập hàng nghìn tin đăng từ nhiều kênh Telegram — tất cả ở một nơi, đầy đủ ảnh, giá và liên hệ.

🏠 <b>Bất động sản</b> — 5 000+ tin cho thuê và mua bán
   📍 Nha Trang · Đà Nẵng · TP.HCM · Hà Nội · Phú Quốc
   📍 Phuket · Bangkok · Pattaya · Koh Samui

🍽 <b>Nhà hàng</b> — 650+ địa điểm ẩm thực

🛵 <b>Phương tiện</b> — xe máy, ô tô, đưa đón

🎯 <b>Tour</b> — các tour và hoạt động giải trí

💱 <b>Đổi tiền</b> — tỷ giá VND và THB cập nhật

🏥 <b>Dịch vụ</b> — y tế, visa, trẻ em, chợ đồ cũ

━━━━━━━━━━━━━━━━━━━━
👇 Выберите страну / Choose your country / Chọn quốc gia:'''

    keyboard = {
        "inline_keyboard": [
            [{"text": "🌏 Open catalog / Открыть каталог", "url": webapp_url}],
            [
                {"text": "🇻🇳 Vietnam", "url": f"{webapp_url}/?country=vietnam&lang=vi"},
                {"text": "🇹🇭 Thailand", "url": f"{webapp_url}/?country=thailand&lang=ru"}
            ],
            [
                {"text": "🇷🇺 Russia", "url": f"{webapp_url}/?lang=ru"},
                {"text": "🇬🇧 England", "url": f"{webapp_url}/?lang=en"}
            ]
        ]
    }

    result = send_message(chat_id, text, keyboard)
    # Pin the welcome message so it stays at the top
    msg_id = result.get('result', {}).get('message_id') if result.get('ok') else None
    if msg_id:
        requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/pinChatMessage',
            json={'chat_id': chat_id, 'message_id': msg_id, 'disable_notification': True},
            timeout=10
        )
    
    return result

def handle_app(chat_id):
    webapp_url = get_webapp_url()
    
    text = "🚀 Нажмите кнопку, чтобы открыть мини-приложение:"
    
    keyboard = {
        "inline_keyboard": [
            [{"text": "📱 Открыть Goldantelope ASIA", "url": webapp_url}]
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
