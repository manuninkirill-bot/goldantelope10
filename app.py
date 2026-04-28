from flask import Flask, render_template, jsonify, request, Response, redirect
from flask_compress import Compress
from datetime import datetime, timedelta
import json
import os
import time
import requests
import re
import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
import threading

# Lock for file operations to prevent race conditions
file_lock = threading.Lock()

# Data cache to prevent heavy disk I/O
data_cache = {}
DATA_CACHE_TTL = 300 # Cache data for 5 minutes

GOOGLE_AI_API_KEY = os.environ.get('GOOGLE_AI_API_KEY', '')
translation_cache = {}

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.environ.get("SESSION_SECRET")
Compress(app)

from datetime import datetime as _dt
@app.template_filter('timestamp_to_date')
def _ts_to_date(ts):
    try:
        return _dt.utcfromtimestamp(int(ts)).strftime('%d.%m.%Y %H:%M')
    except Exception:
        return ''

online_users = {}
ONLINE_TIMEOUT = 60
BASE_ONLINE = 287

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip().strip()
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

def send_telegram_notification(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Telegram notification error: {e}")
        return False

def send_telegram_message(chat_id, message, reply_markup=None):
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        response = requests.post(url, data=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Telegram message error: {e}")
        return False

WELCOME_MESSAGE = """🌏 Крупнейший русскоязычный гид, он же сервис-хаб, телеграмм объявлений в Юго-Восточной Азии.

<b>Наши страны:</b>
🇻🇳 Вьетнам (5,800+ объявлений)
🇹🇭 Таиланд (2,400+ объявлений)
🇮🇳 Индия (1,200+ объявлений)
🇮🇩 Индонезия (800+ объявлений)

<b>Категории:</b>
🏠 Недвижимость - аренда и продажа
🍽️ Рестораны и кафе
🧳 Экскурсии и туры
🏍️ Транспорт - байки, авто, яхты
🎮 Развлечения
💱 Обмен валют
🛍️ Барахолка
🏥 Медицина
📰 Новости
💬 Чат сообщества

В нашем мини приложении вы можете добавить объявление или услугу!
"""

# Данные хранятся в JSON файле по странам
DATA_FILE = "listings_data.json"

def create_empty_data():
    return {
        "restaurants": [],
        "tours": [],
        "transport": [],
        "real_estate": [],
        "money_exchange": [],
        "entertainment": [],
        "marketplace": [],
        "visas": [],
        "news": [],
        "chat": []
    }

def load_data(country='vietnam'):
    now = time.time()
    if country in data_cache and now - data_cache[country]['time'] < DATA_CACHE_TTL:
        return data_cache[country]['data']
    
    country_file = f"listings_{country}.json"
    result = create_empty_data()
    
    if os.path.exists(country_file):
        try:
            with open(country_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    result = data
                else:
                    # Если данные в файле - список, распределяем по категориям
                    category_map = {
                        'bikes': 'transport',
                        'real_estate': 'real_estate',
                        'exchange': 'money_exchange',
                        'money_exchange': 'money_exchange',
                        'food': 'restaurants',
                        'restaurants': 'restaurants'
                    }
                    for item in data:
                        if not isinstance(item, dict): continue
                        cat = item.get('category', 'chat')
                        mapped_cat = category_map.get(cat, cat)
                        if mapped_cat in result:
                            result[mapped_cat].append(item)
        except Exception as e:
            print(f"Error loading country file {country_file}: {e}")
    
    elif os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                all_data = json.load(f)
                if country in all_data:
                    result = all_data[country]
        except Exception as e:
            print(f"Error loading DATA_FILE for {country}: {e}")
            
    data_cache[country] = {'data': result, 'time': now}
    return result

def load_all_data():
    now = time.time()
    if 'all' in data_cache and now - data_cache['all']['time'] < DATA_CACHE_TTL:
        return data_cache['all']['data']
        
    result = {
        'vietnam': create_empty_data(),
        'thailand': create_empty_data(),
        'india': create_empty_data(),
        'indonesia': create_empty_data()
    }
    
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                result = json.load(f)
        except Exception as e:
            print(f"Error loading DATA_FILE: {e}")
            # Try to recover from country files if DATA_FILE is corrupted
            for country in result.keys():
                result[country] = load_data(country)
            
    data_cache['all'] = {'data': result, 'time': now}
    return result

def save_data(country='vietnam', data=None):
    if not data or not isinstance(data, dict):
        return
    
    with file_lock:
        # Инвалидируем кэш
        if country in data_cache:
            del data_cache[country]
        if 'all' in data_cache:
            del data_cache['all']
            
        # Сохраняем в файл страны
        country_file = f"listings_{country}.json"
        try:
            with open(country_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving country file {country_file}: {e}")
        
        # Синхронизируем с общим файлом listings_data.json
        try:
            # Load current all_data without using load_all_data to avoid recursion or stale cache
            all_data = {}
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    all_data = json.load(f)
            
            all_data[country] = data
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(all_data, f, ensure_ascii=False, indent=2)
            
            # Update cache
            data_cache['all'] = {'data': all_data, 'time': time.time()}
            data_cache[country] = {'data': data, 'time': time.time()}
        except Exception as e:
            print(f"Error syncing with listings_data.json: {e}")

@app.errorhandler(500)
def handle_500(e):
    return jsonify({'error': 'Internal Server Error', 'message': str(e)}), 500

@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not Found', 'message': 'API route not found'}), 404
    return render_template('dashboard.html')

@app.route('/')
def index():
    return render_template('dashboard.html')

def _translate_via_mymemory(text: str, target_lang: str) -> str:
    """Translate a single text using MyMemory API (free, no key needed)."""
    try:
        lang_map = {'en': 'en', 'vi': 'vi', 'ru': 'ru', 'th': 'th'}
        tgt = lang_map.get(target_lang, 'en')
        r = requests.get(
            'https://api.mymemory.translated.net/get',
            params={'q': text[:450], 'langpair': f'ru|{tgt}'},
            timeout=6
        )
        if r.ok:
            data = r.json()
            # Reject quota warning responses (429 or status != 200)
            if data.get('responseStatus') not in (200, '200'):
                return text
            translated = data.get('responseData', {}).get('translatedText', '')
            # Reject MyMemory quota warning messages
            if translated and 'MYMEMORY WARNING' in translated.upper():
                return text
            if translated and translated.upper() != text.upper():
                return translated
    except Exception as e:
        logging.debug(f"MyMemory error: {e}")
    return text


def _translate_via_lingva(text: str, target_lang: str) -> str:
    """Translate via multiple Lingva/Google Translate proxy instances (free)."""
    try:
        import urllib.parse
        lang_map = {'en': 'en', 'vi': 'vi', 'ru': 'ru', 'th': 'th'}
        tgt = lang_map.get(target_lang, 'en')
        # Lingva uses path-based routing; colon in path causes 404. Replace with space.
        clean = re.sub(r':\s*', ' ', text[:1000])
        encoded = urllib.parse.quote(clean, safe='')
        instances = [
            f'https://lingva.ml/api/v1/ru/{tgt}/{encoded}',
            f'https://lingva.garudalinux.org/api/v1/ru/{tgt}/{encoded}',
            f'https://translate.plausibility.cloud/api/v1/ru/{tgt}/{encoded}',
            f'https://lingva.lunar.icu/api/v1/ru/{tgt}/{encoded}',
        ]
        for url in instances:
            try:
                r = requests.get(url, timeout=10)
                if r.ok:
                    result = r.json().get('translation', '')
                    if result and result.strip() and result.upper() != text.upper():
                        return result
            except Exception:
                continue
    except Exception as e:
        logging.debug(f"Lingva error: {e}")
    return text


def _translate_one(text: str, target_lang: str) -> str:
    """Translate one text, with cache check. Lingva first (no quota), MyMemory fallback."""
    if not text or not text.strip():
        return text
    cache_key = hashlib.md5(f"{text}:{target_lang}".encode()).hexdigest()
    if cache_key in translation_cache:
        return translation_cache[cache_key]
    # Try MyMemory first (fast ~0.7s) then Lingva (more reliable, no daily quota)
    translated = _translate_via_mymemory(text, target_lang)
    if translated == text:
        translated = _translate_via_lingva(text, target_lang)
    # Only cache successful translations (not unchanged originals)
    if translated != text:
        translation_cache[cache_key] = translated
    return translated


@app.route('/api/translate', methods=['POST'])
def translate_text():
    data = request.get_json()
    texts = data.get('texts', [])
    target_lang = data.get('lang', 'en')

    if not texts:
        return jsonify({'translations': []})

    texts = texts[:30]
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = [None] * len(texts)
    with ThreadPoolExecutor(max_workers=min(30, len(texts))) as executor:
        future_to_idx = {executor.submit(_translate_one, t, target_lang): i for i, t in enumerate(texts)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception:
                results[idx] = texts[idx]

    return jsonify({'translations': results})

ANALYTICS_FILE = 'analytics.json'
analytics_lock = threading.Lock()

def load_analytics():
    try:
        if os.path.exists(ANALYTICS_FILE):
            with open(ANALYTICS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return {'daily': {}, 'visitors': {}}

def save_analytics(data):
    with analytics_lock:
        with open(ANALYTICS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def track_visit(user_id, country=None, category=None, referrer=None, is_mobile=False):
    try:
        analytics = load_analytics()
        today = datetime.now().strftime('%Y-%m-%d')
        hour = datetime.now().strftime('%H')

        if today not in analytics['daily']:
            analytics['daily'][today] = {
                'unique_visitors': [],
                'page_views': 0,
                'countries': {},
                'categories': {},
                'hours': {},
                'referrers': {},
                'devices': {'mobile': 0, 'desktop': 0}
            }

        day = analytics['daily'][today]
        day['page_views'] += 1

        if user_id and user_id not in day['unique_visitors']:
            day['unique_visitors'].append(user_id)

        if country:
            day['countries'][country] = day['countries'].get(country, 0) + 1
        skip_categories = {'admin', 'submit-restaurant', 'submit-tour', 'submit-transport', 'submit-exchange', 'submit-visas', 'submit-realestate', 'submit-entertainment'}
        if category and category not in skip_categories:
            day['categories'][category] = day['categories'].get(category, 0) + 1

        day['hours'][hour] = day['hours'].get(hour, 0) + 1

        if referrer:
            day['referrers'][referrer] = day['referrers'].get(referrer, 0) + 1

        if is_mobile:
            day['devices']['mobile'] += 1
        else:
            day['devices']['desktop'] += 1

        if user_id:
            if user_id not in analytics['visitors']:
                analytics['visitors'][user_id] = {'first_seen': today, 'visits': 0, 'last_seen': today}
            analytics['visitors'][user_id]['visits'] += 1
            analytics['visitors'][user_id]['last_seen'] = today

        old_days = sorted(analytics['daily'].keys())
        if len(old_days) > 90:
            for d in old_days[:-90]:
                del analytics['daily'][d]

        save_analytics(analytics)
    except Exception as e:
        logger.error(f"Analytics track error: {e}")

@app.route('/api/ping')
def ping():
    user_id = request.args.get('uid', request.remote_addr)
    online_users[user_id] = time.time()
    now = time.time()
    active = sum(1 for t in online_users.values() if now - t < ONLINE_TIMEOUT)
    country = request.args.get('country', '')
    category = request.args.get('category', '')
    referrer = request.args.get('ref', '')
    ua = request.headers.get('User-Agent', '').lower()
    is_mobile = any(m in ua for m in ['mobile', 'android', 'iphone', 'ipad'])
    threading.Thread(target=track_visit, args=(user_id, country, category, referrer, is_mobile), daemon=True).start()
    return jsonify({'online': active})

@app.route('/api/online')
def get_online():
    now = time.time()
    active = sum(1 for t in online_users.values() if now - t < ONLINE_TIMEOUT)
    return jsonify({'online': active})

@app.route('/api/analytics')
def get_analytics():
    analytics = load_analytics()
    today = datetime.now().strftime('%Y-%m-%d')
    now = datetime.now()

    days_7 = [(now - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
    days_30 = [(now - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(30)]

    def aggregate(days_list):
        total_views = 0
        all_visitors = set()
        countries = {}
        categories = {}
        hours = {}
        devices = {'mobile': 0, 'desktop': 0}
        daily_chart = []

        for d in sorted(days_list):
            day_data = analytics['daily'].get(d, {})
            views = day_data.get('page_views', 0)
            visitors = day_data.get('unique_visitors', [])
            total_views += views
            all_visitors.update(visitors)
            for k, v in day_data.get('countries', {}).items():
                countries[k] = countries.get(k, 0) + v
            for k, v in day_data.get('categories', {}).items():
                categories[k] = categories.get(k, 0) + v
            for k, v in day_data.get('hours', {}).items():
                hours[k] = hours.get(k, 0) + v
            dev = day_data.get('devices', {})
            devices['mobile'] += dev.get('mobile', 0)
            devices['desktop'] += dev.get('desktop', 0)
            daily_chart.append({'date': d, 'views': views, 'visitors': len(visitors)})

        return {
            'total_views': total_views,
            'unique_visitors': len(all_visitors),
            'countries': dict(sorted(countries.items(), key=lambda x: -x[1])),
            'categories': dict(sorted(categories.items(), key=lambda x: -x[1])),
            'peak_hours': dict(sorted(hours.items(), key=lambda x: -x[1])[:5]),
            'devices': devices,
            'daily_chart': daily_chart
        }

    today_data = analytics['daily'].get(today, {})

    return jsonify({
        'today': {
            'views': today_data.get('page_views', 0),
            'visitors': len(today_data.get('unique_visitors', [])),
            'countries': today_data.get('countries', {}),
            'categories': today_data.get('categories', {}),
            'devices': today_data.get('devices', {'mobile': 0, 'desktop': 0})
        },
        'week': aggregate(days_7),
        'month': aggregate(days_30),
        'total_all_time_visitors': len(analytics.get('visitors', {}))
    })

weather_cache = {}
WEATHER_CACHE_TTL = 3600

@app.route('/api/weather')
def get_weather():
    city = request.args.get('city', 'Ho Chi Minh')
    cache_key = city.lower()
    now = time.time()
    
    if cache_key in weather_cache:
        cached = weather_cache[cache_key]
        if now - cached['time'] < WEATHER_CACHE_TTL:
            return jsonify({'temp': cached['temp'], 'cached': True})
    
    try:
        response = requests.get(f'https://wttr.in/{city}?format=%t&m', timeout=5, headers={'User-Agent': 'curl/7.68.0'})
        if response.status_code == 200:
            temp = response.content.decode('utf-8').strip().replace('+', '').replace('°', ' °')
            weather_cache[cache_key] = {'temp': temp, 'time': now}
            return jsonify({'temp': temp, 'cached': False})
    except Exception as e:
        print(f"Weather error: {e}")
    
    if cache_key in weather_cache:
        return jsonify({'temp': weather_cache[cache_key]['temp'], 'cached': True})
    
    return jsonify({'temp': '--°C', 'error': True})

@app.route('/api/telegram-webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'ok': True})
        
        message = data.get('message', {})
        text = message.get('text', '')
        chat_id = message.get('chat', {}).get('id')
        
        if chat_id and text:
            if text == '/start':
                webapp_url = f"https://{os.environ.get('REPLIT_DEV_DOMAIN', '')}"
                keyboard = {
                    "inline_keyboard": [
                        [{"text": "🚀 Открыть мини приложение", "web_app": {"url": webapp_url}}],
                        [{"text": "🇹🇭 Тайланд", "callback_data": "country_thailand"}, 
                         {"text": "🇻🇳 Вьетнам", "callback_data": "country_vietnam"}],
                        [{"text": "🇮🇳 Индия", "callback_data": "country_india"}, 
                         {"text": "🇮🇩 Индонезия", "callback_data": "country_indonesia"}]
                    ]
                }
                send_telegram_message(chat_id, WELCOME_MESSAGE, keyboard)
            elif text == '/help':
                help_text = """<b>Команды бота:</b>

/start - Приветствие и информация о портале
/help - Список команд
/contact - Контакты для связи
/categories - Список категорий"""
                send_telegram_message(chat_id, help_text)
            elif text == '/contact':
                contact_text = """<b>Контакты GoldAntelope ASIA:</b>

✈️ Telegram: @radimiralubvi

Мы всегда рады помочь!"""
                send_telegram_message(chat_id, contact_text)
            elif text == '/categories':
                categories_text = """<b>Категории объявлений:</b>

🏠 Недвижимость
🍽️ Рестораны
🧳 Экскурсии
🏍️ Транспорт
👶 Дети
💱 Обмен валют
🛍️ Барахолка
🏥 Медицина
📰 Новости
💬 Чат"""
                send_telegram_message(chat_id, categories_text)
        
        return jsonify({'ok': True})
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'ok': True})

@app.route('/api/set-telegram-webhook')
def set_telegram_webhook():
    if not TELEGRAM_BOT_TOKEN:
        return jsonify({'error': 'Bot token not configured'})
    
    domain = os.environ.get('REPLIT_DEV_DOMAIN', '')
    if not domain:
        return jsonify({'error': 'Domain not found'})
    
    webhook_url = f"https://{domain}/api/telegram-webhook"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    
    try:
        response = requests.post(url, data={"url": webhook_url}, timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/groups-stats')
def groups_stats():
    """Статистика по группам: охват, онлайн, объявления"""
    country = request.args.get('country', 'thailand')
    data = load_data(country)

    
    # Подсчет объявлений по категориям
    listings_count = {}
    for cat, items in data.items():
        if cat != 'chat':
            listings_count[cat] = len(items)
    
    # Загружаем статистику групп для конкретной страны
    stats_file = f'groups_stats_{country}.json'
    groups = []
    updated = None
    
    # ЗАЩИТА: Не загружаем статистику если файл не существует или пуст для этой страны
    if os.path.exists(stats_file):
        with open(stats_file, 'r', encoding='utf-8') as f:
            stats_data = json.load(f)
            groups = stats_data.get('groups', [])
            updated = stats_data.get('updated')
            
            # Если для этой страны нет данных, НЕ показываем данные от других стран
            if not groups and country != 'thailand':
                # Возвращаем пустой результат вместо fallback на другую страну
                return jsonify({
                    'updated': datetime.now().isoformat(),
                    'categories': {},
                    'groups': [],
                    'total_participants': 0,
                    'total_online': 0,
                    'message': f'Статистика по {country} еще собирается...'
                })
    
    # Агрегируем по категориям
    category_stats = {}
    for g in groups:
        cat = g.get('category', 'Другое')
        if cat not in category_stats:
            category_stats[cat] = {'participants': 0, 'online': 0, 'groups': 0, 'listings': 0}
        category_stats[cat]['participants'] += g.get('participants', 0)
        category_stats[cat]['online'] += g.get('online', 0)
        category_stats[cat]['groups'] += 1
    
    # Добавляем количество объявлений
    cat_key_map = {
        'Недвижимость': 'real_estate',
        'Чат': 'chat',
        'Рестораны': 'restaurants',
        'Дети': 'entertainment',
        'Барахолка': 'marketplace',
        'Новости': 'news',
        'Визаран': 'visas',
        'Экскурсии': 'tours',
        'Финансы': 'money_exchange',
        'Транспорт': 'transport',
    }
    
    for cat_name, cat_key in cat_key_map.items():
        if cat_name in category_stats:
            category_stats[cat_name]['listings'] = listings_count.get(cat_key, 0)
    
    return jsonify({
        'updated': updated,
        'categories': category_stats,
        'groups': groups,
        'total_participants': sum(g.get('participants', 0) for g in groups),
        'total_online': sum(g.get('online', 0) for g in groups)
    })

def load_ads_channels(country):
    """Загрузить рекламные каналы"""
    filename = f'ads_channels_{country}.json'
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'channels': []}

def save_ads_channels(country, data):
    """Сохранить рекламные каналы"""
    filename = f'ads_channels_{country}.json'
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route('/api/ads-channels')
def get_ads_channels():
    """Получить список одобренных рекламных каналов"""
    country = request.args.get('country', 'vietnam')
    show_pending = request.args.get('pending', '') == '1'
    city_filter = request.args.get('city', '')
    data = load_ads_channels(country)
    
    if show_pending:
        # Для админа - показать ожидающие модерации
        pending = [ch for ch in data.get('channels', []) if not ch.get('approved', False)]
        return jsonify({'channels': pending})
    else:
        # Для пользователей - только одобренные
        approved = [ch for ch in data.get('channels', []) if ch.get('approved', False)]
        # Фильтр по городу
        if city_filter:
            approved = [ch for ch in approved if ch.get('city', '') == city_filter]
        return jsonify({'channels': approved})

@app.route('/api/ads-channels/add', methods=['POST'])
def add_ads_channel():
    """Добавить канал для рекламы"""
    try:
        req = request.json
        country = req.get('country', 'vietnam')
        name = req.get('name', '').strip()
        category = req.get('category', 'chat')
        members = int(req.get('members', 0))
        price = int(req.get('price', 30))
        contact = req.get('contact', '').strip()
        
        if not name or not contact:
            return jsonify({'success': False, 'error': 'Укажите название и контакт'})
        
        data = load_ads_channels(country)
        
        # Проверяем дубликаты
        for ch in data['channels']:
            if ch['name'].lower() == name.lower():
                return jsonify({'success': False, 'error': 'Канал уже добавлен'})
        
        city = req.get('city', '').strip()
        
        new_channel = {
            'id': f'ad_{int(time.time())}',
            'name': name,
            'category': category,
            'city': city,
            'members': members,
            'price': price,
            'contact': contact,
            'added': datetime.now().isoformat(),
            'approved': False
        }
        
        data['channels'].append(new_channel)
        save_ads_channels(country, data)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/ads-channels/approve', methods=['POST'])
def approve_ads_channel():
    """Одобрить или отклонить канал"""
    try:
        req = request.json
        admin_key = req.get('password', '')
        expected_key = os.environ.get('ADMIN_KEY', 'goldantelope2025')
        
        if admin_key != expected_key:
            return jsonify({'success': False, 'error': 'Неверный пароль'})
        
        country = req.get('country', 'vietnam')
        channel_id = req.get('channel_id', '')
        action = req.get('action', 'approve')  # approve или reject
        
        data = load_ads_channels(country)
        
        if action == 'reject':
            # Удаляем канал
            data['channels'] = [ch for ch in data['channels'] if ch['id'] != channel_id]
            save_ads_channels(country, data)
            return jsonify({'success': True, 'message': 'Канал отклонён'})
        else:
            # Одобряем канал
            for ch in data['channels']:
                if ch['id'] == channel_id:
                    ch['approved'] = True
                    save_ads_channels(country, data)
                    return jsonify({'success': True, 'message': 'Канал одобрен'})
            
            return jsonify({'success': False, 'error': 'Канал не найден'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/ads-channels/delete', methods=['POST'])
def delete_ads_channel():
    """Удалить рекламный канал"""
    try:
        req = request.json
        admin_key = req.get('password', '')
        expected_key = os.environ.get('ADMIN_KEY', 'goldantelope2025')
        
        if admin_key != expected_key:
            return jsonify({'success': False, 'error': 'Неверный пароль'})
        
        country = req.get('country', 'vietnam')
        channel_id = req.get('channel_id', '')
        
        data = load_ads_channels(country)
        original_count = len(data['channels'])
        data['channels'] = [ch for ch in data['channels'] if ch['id'] != channel_id]
        
        if len(data['channels']) < original_count:
            save_ads_channels(country, data)
            return jsonify({'success': True, 'message': 'Канал удалён'})
        else:
            return jsonify({'success': False, 'error': 'Канал не найден'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/ads-channels/edit', methods=['POST'])
def edit_ads_channel():
    """Редактировать рекламный канал"""
    try:
        req = request.json
        admin_key = req.get('password', '')
        expected_key = os.environ.get('ADMIN_KEY', 'goldantelope2025')
        
        if admin_key != expected_key:
            return jsonify({'success': False, 'error': 'Неверный пароль'})
        
        country = req.get('country', 'vietnam')
        channel_id = req.get('channel_id', '')
        new_data = req.get('data', {})
        
        data = load_ads_channels(country)
        
        for ch in data['channels']:
            if ch['id'] == channel_id:
                if 'name' in new_data:
                    ch['name'] = new_data['name']
                if 'category' in new_data:
                    ch['category'] = new_data['category']
                if 'members' in new_data:
                    ch['members'] = int(new_data['members'])
                if 'price' in new_data:
                    ch['price'] = float(new_data['price'])
                if 'contact' in new_data:
                    ch['contact'] = new_data['contact']
                if 'city' in new_data:
                    ch['city'] = new_data['city']
                
                save_ads_channels(country, data)
                return jsonify({'success': True, 'message': 'Канал обновлён'})
        
        return jsonify({'success': False, 'error': 'Канал не найден'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/status')
def status():
    country = request.args.get('country', 'vietnam')
    data = load_data(country)

    total_items = sum(len(v) for v in data.values())
    total_listings = sum(len(v) for k, v in data.items() if k != 'chat')
    
    # Количество людей на портале по странам
    online_counts = {
        'vietnam': 342,
        'thailand': 287,
        'india': 156,
        'indonesia': 419
    }
    
    return jsonify({
        'parser_status': 'connected',
        'total_items': total_items,
        'total_listings': total_listings,
        'categories': {k: len(v) for k, v in data.items()},
        'last_update': datetime.now().isoformat(),
        'channels_active': 0,
        'country': country,
        'online_count': online_counts.get(country, 100)
    })

@app.route('/api/city-counts/<category>')
def get_city_counts(category):
    country = request.args.get('country', 'vietnam')
    data = load_data(country)

    
    category_aliases = {
        'exchange': 'money_exchange',
        'money_exchange': 'money_exchange',
        'bikes': 'transport',
        'realestate': 'real_estate'
    }
    category = category_aliases.get(category, category)
    
    if category not in data:
        return jsonify({})
    
    listings = data[category]
    listings = [x for x in listings if not x.get('hidden', False)]
    
    if country == 'thailand':
        th_city_mapping = {
            'Пхукет': 'Пхукет', 'пхукет': 'Пхукет', 'Phuket': 'Пхукет', 'phuket': 'Пхукет',
            'Паттайя': 'Паттайя', 'паттайя': 'Паттайя', 'Pattaya': 'Паттайя', 'pattaya': 'Паттайя',
            'Бангкок': 'Бангкок', 'бангкок': 'Бангкок', 'Bangkok': 'Бангкок', 'bangkok': 'Бангкок',
            'Самуи': 'Самуи', 'самуи': 'Самуи', 'Samui': 'Самуи', 'samui': 'Самуи', 'Koh Samui': 'Самуи',
            'Чиангмай': 'Чиангмай', 'чиангмай': 'Чиангмай', 'Chiang Mai': 'Чиангмай', 'chiangmai': 'Чиангмай',
            'Хуахин': 'Хуахин', 'хуахин': 'Хуахин', 'Hua Hin': 'Хуахин', 'huahin': 'Хуахин',
            'Краби': 'Краби', 'краби': 'Краби', 'Krabi': 'Краби', 'krabi': 'Краби',
        }
        cities = ['Пхукет', 'Паттайя', 'Бангкок', 'Самуи', 'Чиангмай', 'Хуахин', 'Краби']
        counts = {city: 0 for city in cities}
        for item in listings:
            raw_city = str(item.get('city', '') or item.get('location', '') or '').strip()
            ru_city = th_city_mapping.get(raw_city)
            if ru_city and ru_city in counts:
                counts[ru_city] += 1
        return jsonify(counts)

    if country == 'india':
        india_city_keywords = {
            'Гоа': ['goa', 'anjuna', 'arambol', 'vagator', 'morjim', 'palolem', 'calangute', 'candolim', 'гоа'],
            'Касол': ['kasol', 'касол'],
            'Мумбаи': ['mumbai', 'мумбаи'],
            'Дели': ['delhi', 'new delhi', 'gurugram', 'noida', 'дели'],
            'Бангалор': ['bangalore', 'bengaluru', 'бангалор'],
        }
        cities = list(india_city_keywords.keys())
        counts = {city: 0 for city in cities}
        for item in listings:
            raw_realestate = str(item.get('realestate_city', '') or '').strip().lower()
            raw_city = str(item.get('city', '') or '').strip().lower()
            raw_location = str(item.get('location', '') or '').strip().lower()
            matched = False
            for ru_city, keywords in india_city_keywords.items():
                for kw in keywords:
                    if kw in raw_realestate or kw in raw_city or kw in raw_location:
                        counts[ru_city] += 1
                        matched = True
                        break
                if matched:
                    break
        return jsonify(counts)

    # Vietnam city mapping
    city_name_mapping = {
        # Нячанг
        'Nha Trang': 'Нячанг', 'nha trang': 'Нячанг', 'nhatrang': 'Нячанг', 'nha_trang': 'Нячанг', 'Нячанг': 'Нячанг', 'нячанг': 'Нячанг',
        # Хошимин
        'Saigon': 'Хошимин', 'Ho Chi Minh': 'Хошимин', 'saigon': 'Хошимин', 'hcm': 'Хошимин', 'ho_chi_minh': 'Хошимин', 'Хошимин': 'Хошимин', 'хошимин': 'Хошимин', 'Сайгон': 'Хошимин', 'сайгон': 'Хошимин', 'HCM': 'Хошимин', 'Ho chi minh': 'Хошимин',
        # Дананг
        'Da Nang': 'Дананг', 'danang': 'Дананг', 'da_nang': 'Дананг', 'Danang': 'Дананг', 'Дананг': 'Дананг', 'дананг': 'Дананг', 'Da nang': 'Дананг',
        # Ханой
        'Hanoi': 'Ханой', 'hanoi': 'Ханой', 'Ha Noi': 'Ханой', 'ha_noi': 'Ханой', 'Ханой': 'Ханой', 'ханой': 'Ханой',
        # Фукуок
        'Phu Quoc': 'Фукуок', 'phuquoc': 'Фукуок', 'phu_quoc': 'Фукуок', 'Phuquoc': 'Фукуок', 'Фукуок': 'Фукуок', 'фукуок': 'Фукуок', 'Phu quoc': 'Фукуок',
        # Фантьет
        'Phan Thiet': 'Фантьет', 'phanthiet': 'Фантьет', 'phan_thiet': 'Фантьет', 'Phanthiet': 'Фантьет', 'Фантьет': 'Фантьет', 'фантьет': 'Фантьет',
        # Муйне
        'Mui Ne': 'Муйне', 'muine': 'Муйне', 'mui_ne': 'Муйне', 'Muine': 'Муйне', 'Муйне': 'Муйне', 'муйне': 'Муйне',
        # Камрань
        'Cam Ranh': 'Камрань', 'camranh': 'Камрань', 'cam_ranh': 'Камрань', 'Camranh': 'Камрань', 'Камрань': 'Камрань', 'камрань': 'Камрань',
        # Далат
        'Da Lat': 'Далат', 'dalat': 'Далат', 'da_lat': 'Далат', 'Dalat': 'Далат', 'Далат': 'Далат', 'далат': 'Далат',
        # Хойан
        'Hoi An': 'Хойан', 'hoian': 'Хойан', 'hoi_an': 'Хойан', 'Hoian': 'Хойан', 'Хойан': 'Хойан', 'хойан': 'Хойан'
    }
    
    # Ключевые слова для поиска в тексте
    city_keywords = {
        'Нячанг': ['нячанг', 'nha trang', 'nhatrang', 'nha_trang'],
        'Хошимин': ['хошимин', 'сайгон', 'saigon', 'ho chi minh', 'hcm', 'ho_chi_minh'],
        'Дананг': ['дананг', 'da nang', 'danang', 'da_nang'],
        'Ханой': ['ханой', 'hanoi', 'ha_noi'],
        'Фукуок': ['фукуок', 'phu quoc', 'phuquoc', 'phu_quoc'],
        'Фантьет': ['фантьет', 'phan thiet', 'phanthiet', 'phan_thiet'],
        'Муйне': ['муйне', 'mui ne', 'muine', 'mui_ne'],
        'Камрань': ['камрань', 'cam ranh', 'camranh', 'cam_ranh'],
        'Далат': ['далат', 'da lat', 'dalat', 'da_lat'],
        'Хойан': ['хойан', 'hoi an', 'hoian', 'hoi_an']
    }
    
    cities = ['Нячанг', 'Хошимин', 'Ханой', 'Фукуок', 'Фантьет', 'Муйне', 'Дананг', 'Камрань', 'Далат', 'Хойан']
    counts = {city: 0 for city in cities}
    
    for item in listings:
        raw_city = str(item.get('city', '') or item.get('location', '') or '').strip()
        ru_city = city_name_mapping.get(raw_city)
        if ru_city and ru_city in counts:
            counts[ru_city] += 1
        else:
            # Fallback: search city keywords in text/description
            txt = (str(item.get('text', '') or '') + ' ' + str(item.get('description', '') or '')).lower()
            for city, keywords in city_keywords.items():
                if any(kw in txt for kw in keywords):
                    counts[city] += 1
                    break
    
    return jsonify(counts)

VIETNAM_REALESTATE_GROUPS = {
    '@arenda_v_danang', '@danang_arenda', '@rent_nha_trang', '@nychang_arenda',
    '@nedvizimost_nhatrang', '@danangrentaflat', '@rent_appart_nha',
    '@nyachang_nedvizhimost', '@danang_house', '@danag_viet_life_rent',
    '@nhatrangforrent79', '@phuquoc_rent_wt', '@danangapartmentrent',
    '@rentnhatrang', '@tanrealtorgh', '@viet_life_phu_quoc_rent',
    '@nhatrang_luxury', '@megasforrentnhatrang', '@viet_life_niachang',
    '@hanoirentapartment', '@luckyhome_nhatrang', '@nhatrangapartment',
    '@nhatrang_rental', '@hanoirentl', '@phyquocnedvigimost', '@hcmc_arenda',
    '@hanoi_rent', '@nhatrangrentl', '@arenda_v_nyachang',
    '@arenda_nyachang_zhilye', '@phuquocrentl', '@vietnam_arenda',
    '@realestatebythesea_1', '@hochiminhrenti', '@huynhtruonq', '@viethome',
}

@app.route('/api/realestate-groups')
def get_realestate_groups():
    """Return unique contact groups for real_estate listings of given country."""
    country = request.args.get('country', 'vietnam')
    data = load_data(country)
    listings = data.get('real_estate', [])
    groups = set()
    for x in listings:
        if not isinstance(x, dict):
            continue
        contact = x.get('contact') or ''
        if contact and contact.startswith('@'):
            groups.add(contact)
        else:
            g = x.get('source_group') or x.get('channel') or x.get('group') or x.get('contact_name') or ''
            if g:
                groups.add(g)
    if country == 'vietnam':
        groups = groups & VIETNAM_REALESTATE_GROUPS
    return jsonify(sorted(groups))


_INTERNAL_CHANNELS = {
    'parsing_in', 'parsing_indo', 'parsing_th', 'parsing_vn',
    'dom_vn', 'doma_th',
    'bikeparsing_vn', 'bikeparsing_th', 'bikeparsing_in', 'bikeparsing_indo',
    'chatparsing_vn', 'chatparsing_in',
    'tusaparsing_vn', 'tusaparsing_th', 'tusaparsing_in', 'tusaparsing_indo',
    'vietnamparsing', 'thailandparsing',
    'media_vn', 'banner_vn',
    'excursii_vn', 'excursii_th',
    'restoranparsing_all',
}

def _mask_internal_channels(items):
    """Скрывает внутренние агрегаторные каналы из всех публичных полей перед отдачей пользователю."""
    import re as _re
    # Паттерн для ссылок вида t.me/parsing_vn или t.me/parsing_vn/123
    _tme_pat = _re.compile(
        r'https?://t\.me/(' + '|'.join(_INTERNAL_CHANNELS) + r')(/\d+)?\S*',
        _re.IGNORECASE
    )
    # Паттерн для @username внутренних каналов
    _at_pat = _re.compile(
        r'@(' + '|'.join(_INTERNAL_CHANNELS) + r')\b',
        _re.IGNORECASE
    )

    for item in items:
        # Поля contact/contact_name — зачищаем если совпадает с внутренним каналом
        for field in ('contact', 'contact_name'):
            val = (item.get(field) or '').lstrip('@').lower()
            if val in _INTERNAL_CHANNELS:
                item[field] = ''

        # Поля с ссылками — зачищаем целиком если содержат t.me/internal_ch/
        for field in ('telegram', 'telegram_link', 'link', 'url', 'source_link'):
            tg = (item.get(field) or '')
            for ch in _INTERNAL_CHANNELS:
                if f't.me/{ch}' in tg.lower():
                    item[field] = ''
                    break

        # source_group / source_channel / channel_id — скрываем если внутренний канал
        for sf in ('source_group', 'source_channel', 'channel_username'):
            sv = (item.get(sf) or '').lstrip('@').lower()
            if sv in _INTERNAL_CHANNELS:
                item[sf] = ''
        # channel_id — скрываем числовые ID наших каналов
        _OUR_CHANNEL_IDS = {
            '-1003987939980', '-1003411602924', '-1003948057945', '-1003872341008',
            '-1003922185577', '-1003894914160', '-1003811252596', '-1003603825848',
        }
        cid = str(item.get('channel_id') or '')
        if cid in _OUR_CHANNEL_IDS:
            item['channel_id'] = ''

        # Описание, заголовок и text — вырезаем t.me/parsing_vn/... и @parsing_vn
        for field in ('description', 'title', 'text'):
            txt = item.get(field) or ''
            if not txt:
                continue
            txt = _tme_pat.sub('', txt)
            txt = _at_pat.sub('', txt)
            txt = txt.strip()
            if txt != (item.get(field) or ''):
                item[field] = txt


def _gurl(ch, pid):
    """Нейтральный URL фото: /g/<alias>/<msg_id>  (имя канала скрыто)."""
    # _CHANNEL_ALIAS определён ниже по файлу, читается при вызове функции
    alias = globals().get('_CHANNEL_ALIAS', {}).get(ch, ch)
    return f'/g/{alias}/{pid}'

def _ggurl(ch, pid, idx):
    """Нейтральный URL группового фото: /gg/<alias>/<msg_id>/<idx>."""
    alias = globals().get('_CHANNEL_ALIAS', {}).get(ch, ch)
    return f'/gg/{alias}/{pid}/{idx}'


def _enrich_tg_images(items):
    """Подставляет рабочие URL фото для ресторанов и других объявлений.
    api.telegram.org URL передаются напрямую (файл отдаётся через /tg_file/ redirect).
    CDN telesco.pe URL конвертируются через /g/ прокси (CDN истекает через ~24ч).
    Для ресторанов @restoranvietnam: приоритет photo_msg_ids → tg_file_ids."""
    for item in items:
        url = item.get('image_url', '') or ''
        # Прямые Bot API ссылки (api.telegram.org) оставляем как есть — /tg_file/ redirect
        is_bot_api_direct = 'api.telegram.org' in url
        # CDN telesco.pe истекают → нужен прокси /tg_img/
        need_replace = not url or 'telesco.pe' in url

        # Рестораны: фото берём из публичного канала @restoranvietnam по ID поста
        msg_ids = item.get('photo_msg_ids') or []
        # Auto-populate photo_msg_ids from telegram_link for old restaurant items
        is_restaurant = (
            item.get('category') == 'restaurants'
            or item.get('source_group') == 'restoranvietnam'
            or 'restoranvietnam' in (item.get('source', '') or '')
            or item.get('source', item.get('category')) in ('restoranvietnam', 'restaurants')
        )
        if not msg_ids and is_restaurant:
            tg_link = item.get('telegram_link', '') or ''
            m = re.search(r't\.me/restoranvietnam/(\d+)', tg_link)
            if m:
                msg_ids = [int(m.group(1))]
                item['photo_msg_ids'] = msg_ids  # сохраняем для JS
        if msg_ids and is_restaurant:
            base_pid = msg_ids[0]
            if need_replace:
                item['image_url'] = _gurl('restoranvietnam', base_pid)
            # Реальное количество фото из кэш-файла (после первого скрапа) или оценка из photo_count
            count_path = os.path.join(_TG_DISK_CACHE_DIR, f'restoranvietnam_{base_pid}_grp_count.txt')
            if os.path.exists(count_path):
                try:
                    with open(count_path) as _cf:
                        n_photos = int(_cf.read().strip())
                except Exception:
                    n_photos = item.get('photo_count') or 4
            else:
                n_photos = item.get('photo_count') or 4
            n_photos = min(max(int(n_photos), 1), 4)
            # Все фото через групповой прокси (один запрос к t.me/s/ = все CDN URL)
            grp_urls = [_ggurl('restoranvietnam', base_pid, i) for i in range(n_photos)]
            item['all_images'] = grp_urls
            item['photo_msg_ids'] = [base_pid]  # для совместимости JS
            item['photo_album_urls'] = grp_urls  # явный список для JS
            for key in ('images', 'photos'):
                item[key] = grp_urls
            continue

        # Real estate: прямые CDN/BotAPI URL оставляем как есть — браузер грузит напрямую
        # Прокси /tg_img/ используем только если нет прямой ссылки
        item_id = item.get('id', '') or ''
        tg_link = item.get('telegram_link', '') or ''

        # api.telegram.org URL — прямые ссылки, не трогаем (кроме неполных base-URL)
        if is_bot_api_direct:
            for key in ('photos', 'all_images', 'images'):
                lst = item.get(key)
                if isinstance(lst, list):
                    item[key] = [u for u in lst if u]
            # Если image_url — базовый путь без файла, берём первый фото из photos
            is_incomplete = not url or url.endswith('/') or '.' not in url.rsplit('/', 1)[-1]
            if is_incomplete:
                best = next((u for u in (item.get('photos') or []) + (item.get('all_images') or [])
                             if u and ('api.telegram.org' in u or 'telesco.pe' in u) and '.' in u.rsplit('/', 1)[-1]), '')
                if best:
                    item['image_url'] = best
            continue

        m_vn = re.search(r't\.me/(vietnamparsing|dom_vn)/(\d+)', tg_link)
        if not m_vn:
            m_vn = re.match(r'(vietnamparsing|dom_vn)_(\d+)', item_id)
        m_th = re.search(r't\.me/(thailandparsing|doma_th)/(\d+)', tg_link)
        if not m_th:
            m_th = re.match(r'(thailandparsing|doma_th)_(\d+)', item_id)
        m_chan = m_vn or m_th

        # Fallback: use source_group + source_id for any channel not matched above
        _AGGREGATOR_CH = {'parsing_vn', 'parsing_th', 'parsing_in', 'parsing_indo',
                          'bikeparsing_vn', 'bikeparsing_th'}
        if not m_chan:
            source_channel_raw = (item.get('source_channel') or '').strip()
            source_grp = (item.get('source_group') or '').strip()
            # For aggregator channels use source_channel + message_id (not source_group)
            if source_channel_raw in _AGGREGATOR_CH:
                source_grp = source_channel_raw
            source_id_raw = item.get('source_id') or ''
            if not source_id_raw and item_id:
                # For aggregator channels, msg_id is stored directly on item
                if source_channel_raw in _AGGREGATOR_CH and item.get('message_id'):
                    source_id_raw = str(item['message_id'])
                else:
                    parts = item_id.rsplit('_', 1)
                    source_id_raw = parts[-1] if len(parts) == 2 else ''
            if source_grp and source_id_raw:
                try:
                    fb_pid = int(source_id_raw)
                    iu_fb = item.get('image_url', '') or ''
                    # Также конвертируем сырые t.me/{internal_ch}/ ссылки в прокси
                    _tme_img_m = re.match(r'^https://t\.me/([^/]+)/(\d+)$', iu_fb)
                    # CDN/BotAPI URLs → /g/ прокси (делает Bot API redirect, без скачивания байт)
                    needs_conv = ('api.telegram.org' in iu_fb or 'telesco.pe' in iu_fb
                                  or 'cdn' in iu_fb.lower()
                                  or (_tme_img_m and _tme_img_m.group(1).lower() in _INTERNAL_CHANNELS))
                    if needs_conv:
                        if _tme_img_m and _tme_img_m.group(1).lower() in _INTERNAL_CHANNELS:
                            item['image_url'] = _gurl(_tme_img_m.group(1), int(_tme_img_m.group(2)))
                        else:
                            item['image_url'] = _gurl(source_grp, fb_pid)
                    for key in ('photos', 'all_images', 'images'):
                        lst = item.get(key)
                        if isinstance(lst, list) and lst:
                            fixed_fb = []
                            for i_fb, u_fb in enumerate(lst):
                                if u_fb and ('api.telegram.org' in u_fb
                                             or 'telesco.pe' in u_fb
                                             or 'cdn' in str(u_fb).lower()):
                                    # CDN/BotAPI ссылки → прокси (Bot API redirect)
                                    if i_fb == 0:
                                        fixed_fb.append(_gurl(source_grp, fb_pid))
                                    else:
                                        fixed_fb.append(_ggurl(source_grp, fb_pid, i_fb))
                                elif u_fb and isinstance(u_fb, str) and (u_fb.startswith('/tg_img/') or u_fb.startswith('/g/')):
                                    # Конвертируем старые /tg_img/ URL в /g/
                                    if u_fb.startswith('/tg_img/'):
                                        parts = u_fb.split('/')
                                        if len(parts) >= 4:
                                            fixed_fb.append(_gurl(parts[2], int(parts[3])))
                                        else:
                                            fixed_fb.append(u_fb)
                                    else:
                                        fixed_fb.append(u_fb)
                                elif u_fb and isinstance(u_fb, str) and re.match(r'^https://t\.me/([^/]+)/(\d+)$', u_fb):
                                    tm_fb = re.match(r'^https://t\.me/([^/]+)/(\d+)$', u_fb)
                                    fixed_fb.append(_gurl(tm_fb.group(1), int(tm_fb.group(2))))
                                else:
                                    fixed_fb.append(u_fb)
                            item[key] = fixed_fb
                except (ValueError, TypeError):
                    pass

        if m_chan:
            chan = m_chan.group(1)
            base_pid = int(m_chan.group(2))
            def _fix_telesco(urls, chan, base_pid):
                fixed = []
                for i, u in enumerate(urls):
                    if u and ('telesco.pe' in u or 'cdn' in str(u) or 'api.telegram.org' in str(u)):
                        # CDN/BotAPI ссылки → прокси (Bot API redirect, без скачивания байт)
                        if i == 0:
                            fixed.append(_gurl(chan, base_pid))
                        else:
                            fixed.append(_ggurl(chan, base_pid, i))
                    elif u and isinstance(u, str) and u.startswith('/tg_img/'):
                        parts = u.split('/')
                        fixed.append(_gurl(parts[2], int(parts[3])) if len(parts) >= 4 else u)
                    elif u and isinstance(u, str) and u.startswith('/g/'):
                        fixed.append(u)
                    elif u and isinstance(u, str) and re.match(r'^https://t\.me/([^/]+)/(\d+)$', u):
                        tm = re.match(r'^https://t\.me/([^/]+)/(\d+)$', u)
                        fixed.append(_gurl(tm.group(1), int(tm.group(2))))
                    else:
                        fixed.append(u)
                return fixed
            for key in ('photos', 'all_images', 'images'):
                lst = item.get(key)
                if isinstance(lst, list) and lst:
                    item[key] = _fix_telesco(lst, chan, base_pid)
            iu = item.get('image_url', '') or ''
            # CDN/BotAPI ссылки → прокси (Bot API redirect, без скачивания байт)
            if iu and ('telesco.pe' in iu or 'cdn' in iu.lower() or 'api.telegram.org' in iu):
                item['image_url'] = _gurl(chan, base_pid)
            elif iu and re.match(r'^https://t\.me/([^/]+)/(\d+)$', iu):
                tm = re.match(r'^https://t\.me/([^/]+)/(\d+)$', iu)
                item['image_url'] = _gurl(tm.group(1), int(tm.group(2)))

        # Остальные: используем tg_file_ids как запасной вариант
        fids = item.get('tg_file_ids') or []
        if fids and need_replace:
            item['image_url'] = f'/tg_file/{fids[0]}'
            for i, fid in enumerate(fids[1:4], start=2):
                key = f'image_url_{i}'
                cur = item.get(key, '') or ''
                if not cur or 'telesco.pe' in cur or 'cdn' in cur.lower():
                    item[key] = f'/tg_file/{fid}'


@app.route('/api/exchange-rates-local')
def get_exchange_rates_local():
    import requests as _req
    from datetime import datetime as _dt
    country = request.args.get('country', 'thailand')
    currency_map = {'vietnam': 'VND', 'thailand': 'THB', 'india': 'INR', 'indonesia': 'IDR'}
    local_currency = currency_map.get(country, 'THB')
    flag_map = {
        'RUB': '🇷🇺', 'USD': '🇺🇸', 'USDT': '🏴', 'EUR': '🇪🇺',
        'KZT': '🇰🇿', 'KRW': '🇰🇷', 'CNY': '🇨🇳'
    }
    want = [
        ('USD', 100), ('EUR', 100), ('RUB', 10000), ('KZT', 100000), ('CNY', 100), ('KRW', 100000)
    ]
    try:
        resp = _req.get('https://open.er-api.com/v6/latest/USD', timeout=6)
        raw = resp.json()
        usd_rates = raw.get('rates', {})
        usd_to_local = usd_rates.get(local_currency)
        if not usd_to_local:
            raise ValueError('No local rate')
        rates = []
        for cur, amount in want:
            usd_to_cur = usd_rates.get(cur)
            if usd_to_cur and usd_to_cur > 0:
                local_val = round(amount * usd_to_local / usd_to_cur, 2)
                local_int = int(local_val) if local_val >= 10 else round(local_val, 2)
                rates.append({
                    'currency': cur,
                    'flag': flag_map.get(cur, ''),
                    'amount_num': amount,
                    'local_num': local_int,
                    'local_currency': local_currency,
                })
        return jsonify({'date': _dt.now().strftime('%d.%m.%Y %H:%M'), 'rates': rates,
                        'local_currency': local_currency, 'source': 'open.er-api.com'})
    except Exception as e:
        logging.warning(f'[exchange-rates-local] error: {e}')
        return jsonify({'rates': [], 'local_currency': local_currency, 'error': str(e)})


@app.route('/api/exchange-rates')
def get_exchange_rates():
    import re as _re
    country = request.args.get('country', 'vietnam')
    data = load_data(country)
    listings = data.get('money_exchange', [])
    rates = []
    date_str = ''
    from datetime import datetime as _dt
    for item in listings:
        src = (item.get('source_group') or '').lstrip('@')
        if src != 'paymens_vn':
            continue
        text = item.get('text', '') or item.get('description', '')
        text_clean = text.replace('\xa0', ' ')
        date_m = _re.search(r'курс на (\d{2}\.\d{2}\.\d{4})', text_clean)
        if date_m:
            date_str = date_m.group(1)
        flag_map = {
            'RUB': '🇷🇺', 'USD': '🇺🇸', 'USDT': '🏴', 'EUR': '🇪🇺',
            'KZT': '🇰🇿', 'KRW': '🇰🇷', 'CNY': '🇨🇳', 'THB': '🇹🇭', 'MYR': '🇲🇾'
        }
        patterns = [
            (r'([\d.,\s]+)\s*RUB\s*➤\s*([\d.,\s]+)\s*VNĐ', 'RUB'),
            (r'(\d+)\$?\s*USD\s*➤\s*([\d.,\s]+)\s*VNĐ', 'USD'),
            (r'(\d+)\$?\s*USDT\s*➤\s*([\d.,\s]+)\s*VNĐ', 'USDT'),
            (r'(\d+)[€]?\s*EUR\s*➤\s*([\d.,\s]+)\s*VNĐ', 'EUR'),
            (r'([\d.,\s]+)\s*KZT\s*➤\s*([\d.,\s]+)\s*VNĐ', 'KZT'),
            (r'([\d\s]+)\s*[KК]RW\s*➤\s*([\d.,\s]+)\s*VNĐ', 'KRW'),
            (r'(\d+)[¥]?\s*CNY\s*➤\s*([\d.,\s]+)\s*VNĐ', 'CNY'),
            (r'(\d+)[฿]?\s*THB\s*➤\s*([\d.,\s]+)\s*VNĐ', 'THB'),
            (r'(\d+)\s*MYR\s*➤\s*([\d.,\s]+)\s*VNĐ', 'MYR'),
        ]
        for pat, cur in patterns:
            m = _re.search(pat, text_clean)
            if m:
                raw_amt = m.group(1).strip()
                raw_vnd = m.group(2).strip()
                amt = _re.sub(r'[^\d]', '', raw_amt)
                vnd = _re.sub(r'[^\d]', '', raw_vnd)
                rates.append({
                    'currency': cur,
                    'flag': flag_map.get(cur, ''),
                    'amount': m.group(1).strip(),
                    'vnd': m.group(2).strip(),
                    'amount_num': int(amt) if amt.isdigit() else 0,
                    'vnd_num': int(vnd) if vnd.isdigit() else 0,
                })
        if rates:
            break
    if not date_str:
        date_str = _dt.now().strftime('%d.%m.%Y')

    # Если из базы курсов нет — берём с open.er-api.com
    if not rates:
        import requests as _req2
        flag_map2 = {'RUB': '🇷🇺', 'USD': '🇺🇸', 'USDT': '🏴', 'EUR': '🇪🇺',
                     'KZT': '🇰🇿', 'KRW': '🇰🇷', 'CNY': '🇨🇳', 'THB': '🇹🇭'}
        want2 = [('USD', 100), ('EUR', 100), ('RUB', 10000), ('KZT', 100000), ('CNY', 100), ('KRW', 100000)]
        try:
            resp2 = _req2.get('https://open.er-api.com/v6/latest/USD', timeout=6)
            raw2 = resp2.json()
            usd_rates2 = raw2.get('rates', {})
            usd_to_vnd = usd_rates2.get('VND')
            if usd_to_vnd:
                for cur2, amount2 in want2:
                    usd_to_cur2 = usd_rates2.get(cur2)
                    if usd_to_cur2 and usd_to_cur2 > 0:
                        vnd_val = int(round(amount2 * usd_to_vnd / usd_to_cur2))
                        rates.append({
                            'currency': cur2,
                            'flag': flag_map2.get(cur2, ''),
                            'amount': str(amount2),
                            'vnd': f'{vnd_val:,}'.replace(',', ' '),
                            'amount_num': amount2,
                            'vnd_num': vnd_val,
                        })
                date_str = _dt.now().strftime('%d.%m.%Y %H:%M')
        except Exception as _e2:
            logger.warning(f'[exchange-rates fallback] {_e2}')

    return jsonify({
        'date': date_str,
        'rates': rates,
        'source': '@paymens_vn',
        'telegram_link': 'https://t.me/paymens_vn',
    })


@app.route('/api/listings/<category>')
def get_listings(category):
    country = request.args.get('country', 'vietnam')
    data = load_data(country)
    
    # Handle subcategories for Vietnam marketplace and exchange - return listings by default
    # Subcategory info moved to separate endpoint


    
    

    
    
    category_aliases = {
        'exchange': 'money_exchange',
        'money_exchange': 'money_exchange',
        'bikes': 'transport',
        'realestate': 'real_estate',
        'stats': 'restaurants'
    }
    
    if category == 'admin':
        all_listings = []
        for cat_name, cat_data in data.items():
            if isinstance(cat_data, list):
                for item in cat_data:
                    item_copy = item.copy()
                    item_copy['_category'] = cat_name
                    all_listings.append(item_copy)
        show_hidden = request.args.get('show_hidden', '0') == '1'
        if not show_hidden:
            all_listings = [x for x in all_listings if not x.get('hidden', False)]
        return jsonify(all_listings)
    
    category = category_aliases.get(category, category)
    
    if category not in data:
        return jsonify([])
    
    listings = data[category]

    # Дедупликация по id (на случай повторов в JSON)
    _seen_ids = set()
    _deduped = []
    for _it in listings:
        _iid = _it.get('id', '')
        if _iid and _iid in _seen_ids:
            continue
        _seen_ids.add(_iid)
        _deduped.append(_it)
    listings = _deduped

    # Фильтры
    filters = request.args
    
    # Фильтруем скрытые объявления (если не запрошено show_hidden=1)
    # Для Нячанга показываем все объявления включая скрытые
    show_hidden = request.args.get('show_hidden', '0') == '1'
    realestate_city = request.args.get('realestate_city', '')
    if show_hidden:
        filtered = listings  # Показываем все включая скрытые (только для админа)
    else:
        filtered = [x for x in listings if not x.get('hidden', False)]
    
    _GA_TRUSTED_SOURCES = {
        'gavibeshub', 'gavisarun', 'gatours', 'gafoods', 'gapayments',
        'tusaparsing_vn', 'tusaparsing_th', 'tusaparsing_in', 'tusaparsing_indo',
        'vibeshub_vn', 'excursii_th',
    }

    # Туры Вьетнама — только из группы GAtours_vn
    if category == 'tours' and country == 'vietnam':
        filtered = [x for x in filtered if x.get('source_group') == 'GAtours_vn']

    # Недвижимость — убираем посты где contact совпадает с каналом-агрегатором
    # (старые записи до миграции; parsing_vn/parsing_th — теперь легитимные агрегаторы)
    _OWN_PARSE_CH = {'@chatparsing_vn', '@tusaparsing_vn'}
    if category == 'real_estate':
        filtered = [x for x in filtered if x.get('contact', '').lower() not in {c.lower() for c in _OWN_PARSE_CH}]

    # Недвижимость — определяем логотип канала (фото встречающееся в 30+ объявлениях) и удаляем его
    if category == 'real_estate':
        from collections import Counter as _Counter
        _fp_counter = _Counter()
        for _x in filtered:
            _seen = set()
            for _p in ([_x.get('image_url')] if _x.get('image_url') else []) + list(_x.get('photos', [])):
                if not _p:
                    continue
                _fp = _p.split('/file/')[-1][:40] if '/file/' in str(_p) else str(_p)[:40]
                if _fp not in _seen:
                    _fp_counter[_fp] += 1
                    _seen.add(_fp)
        _logo_fps = {fp for fp, cnt in _fp_counter.items() if cnt >= 30}

        def _strip_logos(item):
            if not _logo_fps:
                return item
            item = dict(item)
            def _is_logo(url):
                if not url:
                    return False
                fp = url.split('/file/')[-1][:40] if '/file/' in str(url) else str(url)[:40]
                return fp in _logo_fps
            if _is_logo(item.get('image_url')):
                # Попробуем взять следующее фото
                remaining = [p for p in item.get('photos', []) if not _is_logo(p)]
                item['image_url'] = remaining[0] if remaining else None
                item['photos'] = remaining
            else:
                item['photos'] = [p for p in item.get('photos', []) if not _is_logo(p)]
            return item

        filtered = [_strip_logos(x) for x in filtered]

    # Недвижимость всех стран — только с фото
    if category == 'real_estate':
        filtered = [x for x in filtered if x.get('image_url') and str(x.get('image_url', '')).strip()]

    # Развлечения Индии — только события в окне 14 дней (не прошедшие и не слишком далёкие)
    if category == 'entertainment' and country == 'india':
        from datetime import timezone as _tz14, timedelta as _td14
        _now_ent = datetime.now(_tz14.utc)
        _cut_ent = _now_ent + _td14(days=14)
        def _india_ent_ok(item):
            d_str = item.get('date')
            if not d_str:
                return True
            try:
                import re as _re2
                _ds = _re2.sub(r'[+-]\d{2}:\d{2}$', '', str(d_str).strip()).rstrip('Z').replace(' ', 'T')
                _d = datetime.fromisoformat(_ds).replace(tzinfo=_tz14.utc)
                return _now_ent <= _d <= _cut_ent
            except Exception:
                return True
        filtered = [x for x in filtered if _india_ent_ok(x)]

    if category == 'entertainment':
        _ENT_KEYWORDS = [
            'вечеринк', 'party', 'клуб', 'club', 'ночной клуб', 'night club',
            'мероприят', 'event', 'выступлен', 'perform', 'концерт', 'concert',
            'open air', 'опен эйр', 'фестиваль', 'festival', 'шоу', 'show',
            'диджей', 'dj ', 'музык', 'music', 'танц', 'dance',
            'бар ', 'bar ', 'караоке', 'karaoke', 'дискотек', 'disco',
            'stand up', 'стендап', 'комеди', 'comedy', 'квиз', 'quiz',
            'кино', 'cinema', 'театр', 'theater', 'theatre',
            'развлечен', 'entertain', 'афиша', 'poster',
            'живая музыка', 'live music', 'выставк', 'exhibition',
            'ярмарк', 'fair', 'маркет', 'market',
            'пляжн', 'beach party', 'pool party',
            'рыбалк', 'fishing', 'экскурси', 'excursion',
        ]
        filtered = [x for x in filtered if
            x.get('source_group', '').lower() in _GA_TRUSTED_SOURCES or
            x.get('source_channel', '').lower() in _GA_TRUSTED_SOURCES or
            x.get('channel', '').lower() in _GA_TRUSTED_SOURCES or
            any(kw in (
                (x.get('description') or '') + ' ' +
                (x.get('title') or '') + ' ' +
                (x.get('text') or '')
            ).lower() for kw in _ENT_KEYWORDS)
        ]
        # Только объявления с фото
        def _ent_has_photo(x):
            photos = x.get('photos') or x.get('all_images') or []
            if photos:
                return True
            if x.get('image_url') or x.get('photo') or x.get('photo_url'):
                return True
            return False
        filtered = [x for x in filtered if _ent_has_photo(x)]

    subcategory = request.args.get('subcategory')
    if subcategory:
        if category == 'marketplace':
            filtered = [x for x in filtered if x.get('marketplace_category') == subcategory]
        else:
            filtered = [x for x in filtered if x.get('subcategory') == subcategory]

    source_channel = request.args.get('source_channel')
    if source_channel:
        ch = source_channel.lstrip('@').lower()
        filtered = [x for x in filtered if (x.get('source_channel', '') or '').lstrip('@').lower() == ch]
    
    # Маппинг русских названий городов на английские
    city_name_mapping = {
        'Нячанг': 'Nha Trang',
        'Хошимин': 'Saigon',
        'Сайгон': 'Saigon',
        'Saigon': 'Saigon',
        'Ho Chi Minh': 'Saigon',
        'Дананг': 'Da Nang',
        'Ханой': 'Hanoi',
        'Фукуок': 'Phu Quoc',
        'Фантьет': 'Phan Thiet',
        'Муйне': 'Mui Ne',
        'Камрань': 'Cam Ranh',
        'Далат': 'Da Lat',
        'Хойан': 'Hoi An'
    }
    
    # Универсальный фильтр по городу для категорий, где он есть (restaurants, tours, entertainment, marketplace, visas)
    if category in ['restaurants', 'tours', 'entertainment', 'marketplace', 'visas']:
        if 'city' in filters and filters['city']:
            city_filter = filters['city']
            
            # Расширенный маппинг с подчёркиваниями и всеми вариантами
            city_keywords_map = {
                'Нячанг': ['нячанг', 'nha trang', 'nhatrang', 'nha_trang'],
                'Хошимин': ['хошимин', 'сайгон', 'saigon', 'ho chi minh', 'hcm', 'ho_chi_minh', 'hochiminh'],
                'Дананг': ['дананг', 'da nang', 'danang', 'da_nang'],
                'Ханой': ['ханой', 'hanoi', 'ha_noi'],
                'Фукуок': ['фукуок', 'phu quoc', 'phuquoc', 'phu_quoc'],
                'Фантьет': ['фантьет', 'phan thiet', 'phanthiet', 'phan_thiet'],
                'Муйне': ['муйне', 'mui ne', 'muine', 'mui_ne'],
                'Камрань': ['камрань', 'cam ranh', 'camranh', 'cam_ranh'],
                'Далат': ['далат', 'da lat', 'dalat', 'da_lat'],
                'Хойан': ['хойан', 'hoi an', 'hoian', 'hoi_an'],
                'Бангкок': ['бангкок', 'bangkok'],
                'Пхукет': ['пхукет', 'phuket'],
                'Паттайя': ['паттайя', 'pattaya'],
                'Самуи': ['самуи', 'koh samui', 'ko samui', 'kohsamui', 'samui'],
                'Чиангмай': ['чиангмай', 'chiang mai', 'chiangmai', 'chiang_mai'],
                'Хуахин': ['хуахин', 'hua hin', 'huahin', 'hua_hin'],
                'Краби': ['краби', 'krabi'],
                'Гоа': ['гоа', 'goa', 'anjuna', 'arambol', 'vagator', 'morjim', 'palolem', 'calangute', 'candolim'],
                'Касол': ['касол', 'kasol'],
                'Мумбаи': ['мумбаи', 'mumbai'],
                'Дели': ['дели', 'delhi', 'new delhi', 'gurugram', 'noida'],
                'Бангалор': ['бангалор', 'bangalore', 'bengaluru'],
            }
            
            # Нормализуем city_filter: 'nhatrang' → 'Нячанг' через обратный маппинг
            _alias_to_ru = {}
            for _ru_name, _variants in city_keywords_map.items():
                for _v in _variants:
                    _alias_to_ru[_v] = _ru_name
            city_filter_norm = _alias_to_ru.get(city_filter.lower(), city_filter)
            targets = city_keywords_map.get(city_filter_norm, city_keywords_map.get(city_filter, [city_filter.lower()]))

            def matches_city(item):
                item_city = str(item.get('city', '')).lower()
                item_location = str(item.get('location', '')).lower()
                item_realestate_city = str(item.get('realestate_city', '')).lower()
                # Ищем в title + description + text (description может быть пустым)
                search_text = (
                    str(item.get('title', '')) + ' ' +
                    str(item.get('description', '')) + ' ' +
                    str(item.get('text', ''))
                ).lower()

                # Если город не указан — показываем для любого города
                if not item_city and not item_location and not item_realestate_city:
                    return True

                # Страновой уровень ("вьетнам", "таиланд") — показываем для любого города этой страны
                _country_level = ['вьетнам', 'vietnam', 'thailand', 'таиланд', 'india', 'индия', 'indonesia', 'индонезия']
                if any(cl in item_city for cl in _country_level):
                    # Ищем город хотя бы в тексте объявления
                    for t in targets:
                        if t in search_text:
                            return True
                    # Если в тексте не нашли — всё равно показываем (общестрановое объявление)
                    return True

                # Проверяем поля city, location, realestate_city
                for t in targets:
                    if t in item_city or t in item_location or t in item_realestate_city:
                        return True
                # Проверяем в тексте
                for t in targets:
                    if t in search_text:
                        return True
                return False
            
            filtered = [x for x in filtered if matches_city(x)]
            print(f"DEBUG: Category {category}, City Filter {city_filter}, Targets {targets}, Found {len(filtered)} items")
    
    if category == 'visas':
        # Фильтр по направлению (Камбоджа/Лаос) - используем параметр destination
        if 'destination' in filters and filters['destination']:
            dest_filter = filters['destination'].lower()
            # Маппинг русских названий на английские
            dest_mapping = {
                'камбоджа': ['cambodia', 'камбодж', 'кампучия'],
                'лаос': ['laos', 'лаос'],
                'малайзия': ['malaysia', 'малайзия'],
                'непал': ['nepal', 'непал'],
                'шри-ланка': ['sri lanka', 'srilanka', 'шри-ланка', 'шриланка'],
                'сингапур': ['singapore', 'сингапур']
            }
            targets = dest_mapping.get(dest_filter, [dest_filter])
            filtered = [x for x in filtered if 
                any(t in str(x.get('destination', '')).lower() for t in targets) or
                any(t in str(x.get('title', '')).lower() for t in targets) or
                any(t in str(x.get('description', '')).lower() for t in targets)]
        
        # Фильтр по гражданству (россия/казахстан)
        if 'nationality' in filters and filters['nationality']:
            nationality = filters['nationality'].lower()
            citizenship_mapping = {
                'russia': ['российское', 'россия', 'рф', 'russia', 'russian'],
                'kazakhstan': ['казахское', 'казахстан', 'kz', 'kazakhstan'],
                'belarus': ['белорусское', 'беларусь', 'беларуси', 'belarus', 'belarusian'],
                'ukraine': ['украинское', 'украина', 'украины', 'ukraine', 'ukrainian']
            }
            nationality_keywords = {
                'russia': ['росси', 'россиян', 'рф', 'russia', 'russian', 'для русских', 'для рф', 'российск'],
                'kazakhstan': ['казах', 'казакстан', 'kz', 'kazakhstan', 'для казахов', 'кз', 'казахск'],
                'belarus': ['белорус', 'беларус', 'belarus', 'belarusian', 'для белорусов', 'рб'],
                'ukraine': ['украин', 'ukraine', 'ukrainian', 'для украинцев', 'ua']
            }
            citizenship_values = citizenship_mapping.get(nationality, [])
            keywords = nationality_keywords.get(nationality, [])
            
            def matches_nationality(item):
                citizen = item.get('citizenship', '').lower()
                if citizen and citizen in citizenship_values:
                    return True
                text = (item.get('description', '') + ' ' + item.get('title', '')).lower()
                return any(kw in text for kw in keywords)
            
            filtered = [x for x in filtered if matches_nationality(x)]
        
        # Фильтр по сроку (45 / 90 дней)
        if 'days' in filters and filters['days']:
            days = filters['days']
            filtered = [x for x in filtered if days in (x.get('description', '') + ' ' + x.get('title', ''))]

    # Фильтры для фотосессии (news)
    if category == 'news':
        if 'city' in filters and filters['city']:
            city_filter = filters['city'].lower()
            filtered = [x for x in filtered if city_filter in str(x.get('city', '')).lower() or city_filter in str(x.get('title', '')).lower() or city_filter in str(x.get('description', '')).lower()]

    if category == 'money_exchange':
        import re as _re_ex
        def _is_rates_only_post(item):
            src = (item.get('source_group') or '').lstrip('@')
            if src != 'paymens_vn':
                return False
            text = (item.get('text', '') or item.get('description', '') or '')
            text_clean = text.replace('\xa0', ' ').strip()
            lines = [l.strip() for l in text_clean.split('\n') if l.strip()]
            if not lines:
                return False
            rate_count = sum(1 for l in lines if _re_ex.search(r'➤.*VNĐ', l))
            return rate_count >= 4 and rate_count >= len(lines) * 0.5
        filtered = [x for x in filtered if not _is_rates_only_post(x)]

        if 'city' in filters and filters['city']:
            city_filter = filters['city']
            city_keywords_map = {
                'Нячанг': ['нячанг', 'nha trang', 'nhatrang', 'nha_trang'],
                'Хошимин': ['хошимин', 'сайгон', 'saigon', 'ho chi minh', 'hcm', 'ho_chi_minh'],
                'Дананг': ['дананг', 'da nang', 'danang', 'da_nang'],
                'Фукуок': ['фукуок', 'phu quoc', 'phuquoc', 'phu_quoc'],
            }
            targets = city_keywords_map.get(city_filter, [city_filter.lower()])
            
            def matches_city(item):
                search_text = f"{item.get('city', '')} {item.get('title', '')} {item.get('description', '')} {item.get('text', '')} {item.get('address', '')}".lower()
                return any(t in search_text for t in targets)
            
            filtered = [x for x in filtered if matches_city(x)]

    # Фильтры для медицины
    if category == 'transport':
        # Спам-фильтр: исключаем явно нетранспортный контент
        _TRANSPORT_SPAM_IDS = {
            'baykivietnam_3390', 'baykivietnam_3388', 'baykivietnam_3387',
            'baykivietnam_3386', 'baykivietnam_3383', 'baykivietnam_3374',
            'baykivietnam_3373', 'baykivietnam_3371', 'baykivietnam_2630',
            'baykivietnam_2607',
        }
        _TRANSPORT_SPAM_KEYWORDS = [
            'продажа готового бизнеса',
            'apple watch',
            'освободился номер',
            'освободились апартаменты',
            'медицинское страхование',
            'верификации аккаунта казино',
            '/app rentgo',
            'казино за',
            'нужны люди для',
            'двухкомнатная студия',
            'сравнение цен',
            'цены для сравнения',
            'цены для сравнение',
            'vnd.день',
            'vnd.day',
            'для управления этим байком не требуются права',
            'доступно для аренды сегодня',
            'уважаемые подписчики',
            'не знаете, чем заняться',
            'внимание внимание',
            'апартаменты с джакузи',
            'для управления этого байка не требуются',
            'доступен дешевый велосипед',
            '600.vnd',
            'обучение езде на мотобайке',
            'доступен шоссейный велосипед',
            'свободен для аренды мотоцикл',
            'honda win100',
            'у тебя день рождения',
            'лови скидку',
        ]
        def _is_transport_spam(x):
            item_id = x.get('id', '')
            if item_id in _TRANSPORT_SPAM_IDS:
                return True
            text = ((x.get('title', '') or '') + ' ' + (x.get('description', '') or '') + ' ' + (x.get('text', '') or '')).lower()
            return any(kw in text for kw in _TRANSPORT_SPAM_KEYWORDS)
        filtered = [x for x in filtered if not _is_transport_spam(x)]

        # Фильтр по типу транспорта (bikes, cars, yachts, bicycles)
        if 'transport_type' in filters and filters['transport_type']:
            transport_type = filters['transport_type']
            filtered = [x for x in filtered if x.get('transport_type') == transport_type]
        
        # Фильтр по городу для transport
        if 'city' in filters and filters['city']:
            city_filter = filters['city']
            
            # Расширенный маппинг с русскими ключами
            city_keywords_map = {
                'Нячанг': ['нячанг', 'nha trang', 'nhatrang', 'nha_trang'],
                'Хошимин': ['хошимин', 'сайгон', 'saigon', 'ho chi minh', 'hcm', 'ho_chi_minh', 'hochiminh'],
                'Дананг': ['дананг', 'da nang', 'danang', 'da_nang'],
                'Ханой': ['ханой', 'hanoi', 'ha_noi'],
                'Фукуок': ['фукуок', 'phu quoc', 'phuquoc', 'phu_quoc'],
                'Фантьет': ['фантьет', 'phan thiet', 'phanthiet', 'phan_thiet'],
                'Муйне': ['муйне', 'mui ne', 'muine', 'mui_ne'],
                'Камрань': ['камрань', 'cam ranh', 'camranh', 'cam_ranh'],
                'Далат': ['далат', 'da lat', 'dalat', 'da_lat'],
                'Хойан': ['хойан', 'hoi an', 'hoian', 'hoi_an'],
                'Пхукет': ['пхукет', 'phuket', 'pkhuket'],
                'Паттайя': ['паттай', 'pattaya', 'паттая'],
                'Самуи': ['самуи', 'samui', 'koh samui'],
                'Бангкок': ['бангкок', 'bangkok', 'бкк'],
                'Хуахин': ['хуахин', 'hua hin', 'huahin', 'hua_hin'],
                'Краби': ['краби', 'krabi'],
            }
            
            targets = city_keywords_map.get(city_filter, [city_filter.lower()])
            
            def matches_city(item):
                item_city = str(item.get('city', '')).lower()
                item_location = str(item.get('location', '')).lower()
                search_text = f"{item.get('title', '')} {item.get('description', '')}".lower()
                
                for t in targets:
                    if t in item_city or t in item_location or t in search_text:
                        return True
                return False
            
            filtered = [x for x in filtered if matches_city(x)]
        
        # Фильтр по типу (sale, rent)
        if 'type' in filters and filters['type']:
            type_filter = filters['type'].lower()
            if type_filter == 'sale':
                keywords = ['продаж', 'куплю', 'продам', 'цена', '$', '₫', 'доллар']
                filtered = [x for x in filtered if any(kw in x.get('description', '').lower() for kw in keywords)]
            elif type_filter == 'rent':
                keywords = ['аренд', 'сдам', 'сдаю', 'наём', 'прокат', 'почасово']
                filtered = [x for x in filtered if any(kw in x.get('description', '').lower() for kw in keywords)]
        
        if 'model' in filters and filters['model']:
            filtered = [x for x in filtered if filters['model'].lower() in (x.get('model') or '').lower()]
        if 'year' in filters and filters['year']:
            filtered = [x for x in filtered if str(x.get('year', '')) == filters['year']]
        if 'price_min' in filters and 'price_max' in filters and filters['price_min'] and filters['price_max']:
            try:
                min_p, max_p = float(filters['price_min']), float(filters['price_max'])
                filtered = [x for x in filtered if min_p <= x.get('price', 0) <= max_p]
            except:
                pass
    
    elif category == 'real_estate':
        group_filter = filters.get('source_group', '')
        
        if group_filter:
            # Group selected — filter by group only, ignore city
            filtered = [x for x in filtered if (
                x.get('contact') == group_filter or
                x.get('source_group') == group_filter or
                x.get('channel') == group_filter or
                x.get('contact_name') == group_filter or
                x.get('group') == group_filter or
                group_filter in ' '.join(x.get('photos', [])) or
                group_filter in (x.get('photo_url') or '')
            )]
        else:
            # No group selected — apply city filter if present
            if 'realestate_city' in filters and filters['realestate_city']:
                city_filter = filters['realestate_city'].lower()
                city_mapping = {
                    'nhatrang': ['nhatrang', 'nha trang', 'нячанг', 'нячанга', 'нячанге',
                                 'khanh hoa', 'bai dai', 'hon tre', 'vinh nguyen', 'cam ranh', 'камрань'],
                    'danang': ['danang', 'da nang', 'дананг', 'da-nang', 'son tra', 'sơn trà',
                               'lien chieu', 'lienchieu', 'my khe', 'mykhe', 'bac my an',
                               'hoa khanh', 'hai chau', 'thanh khe', 'ngu hanh son', 'nam o'],
                    'hochiminh': ['hochiminh', 'ho chi minh', 'hcm', 'хошимин', 'сайгон',
                                  'saigon', 'binh thanh', 'thu duc', 'tan binh', 'go vap'],
                    'hanoi': ['hanoi', 'ha noi', 'ханой', 'hà nội', 'tay ho', 'hoan kiem', 'ba dinh'],
                    'phuquoc': ['phuquoc', 'phu quoc', 'фукуок', 'phú quốc', 'duong dong', 'long beach'],
                    'dalat': ['dalat', 'da lat', 'далат', 'đà lạt', 'lam dong'],
                    'muine': ['muine', 'mui ne', 'муйне', 'phan thiet', 'фантьет'],
                    'hoian': ['hoian', 'hoi an', 'хойан', 'hội an'],
                    # Thailand cities
                    'бангкок': ['бангкок', 'bangkok'],
                    'пхукет': ['пхукет', 'phuket'],
                    'паттайя': ['паттайя', 'pattaya'],
                    'самуи': ['самуи', 'samui', 'ko samui', 'koh samui'],
                    'чиангмай': ['чиангмай', 'chiang mai', 'chiangmai'],
                    'краби': ['краби', 'krabi'],
                    'хуахин': ['хуахин', 'hua hin'],
                    'чианграй': ['чианграй', 'chiang rai'],
                    'удон тхани': ['удон тхани', 'udon thani'],
                    'тайланд': ['тайланд', 'thailand'],
                }
                targets = city_mapping.get(city_filter, [city_filter])
                _country_lvl = ['вьетнам', 'vietnam', 'thailand', 'таиланд', 'india', 'индия', 'indonesia', 'индонезия']
                def _re_city_match(x):
                    item_city = str(x.get('city', '')).lower()
                    item_city_ru = str(x.get('city_ru', '')).lower()
                    item_re_city = str(x.get('realestate_city', '') or '').lower()
                    _txt = (str(x.get('title', '')) + ' ' + str(x.get('text', ''))).lower()
                    # Если задан realestate_city — проверяем точно
                    if item_re_city and any(t in item_re_city for t in targets):
                        return True
                    # Проверяем city/city_ru (только специфичные, не страновые)
                    if not any(cl in item_city or cl in item_city_ru for cl in _country_lvl):
                        if any(t in item_city or t in item_city_ru for t in targets):
                            return True
                    # Всегда проверяем в тексте объявления
                    return any(t in _txt for t in targets)
                filtered = [x for x in filtered if _re_city_match(x)]
        
        if 'listing_type' in filters and filters['listing_type']:
            type_filter = filters['listing_type']
            filtered = [x for x in filtered if type_filter in (x.get('listing_type') or '')]
        
        def get_price_int(item):
            # Сначала пробуем поле price
            price = item.get('price')
            if price is not None:
                if isinstance(price, (int, float)) and price > 0:
                    return int(price)
                try:
                    price_str = str(price).lower()
                    multiplier = 1
                    if 'млн' in price_str or 'mln' in price_str or 'миллион' in price_str:
                        multiplier = 1000000
                    price_str = price_str.replace(',', '.')
                    cleaned = re.sub(r'[^\d.]', '', price_str)
                    parts = cleaned.split('.')
                    if len(parts) > 2:
                        cleaned = parts[0] + '.' + ''.join(parts[1:])
                    if cleaned:
                        val = int(float(cleaned) * multiplier)
                        if val > 0:
                            return val
                except:
                    pass
            
            # Если поле price пустое или 0, извлекаем из описания
            desc = (item.get('description') or '').lower()
            
            # Ищем паттерны: "7,5 миллион", "7.5 млн", "Цена: 7 500 000"
            patterns = [
                r'(\d+[,.]?\d*)\s*(?:миллион|млн|mln)',  # 7,5 миллион
                r'цена[:\s]*(\d[\d\s]*)\s*(?:vnd|донг|₫)?',  # Цена: 7 500 000
                r'(\d[\d\s]{2,})\s*(?:vnd|донг|₫)',  # 7 500 000 VND
            ]
            
            for pattern in patterns:
                match = re.search(pattern, desc)
                if match:
                    price_str = match.group(1).replace(' ', '').replace(',', '.')
                    try:
                        val = float(price_str)
                        # Если число маленькое и паттерн с млн/миллион
                        if val < 1000 and ('млн' in pattern or 'миллион' in pattern):
                            val = val * 1000000
                        elif val < 100:
                            val = val * 1000000
                        # Minimum reasonable RE price: 1,000,000 VND (~$40)
                        if val >= 1000000:
                            return int(val)
                    except:
                        pass
            
            return 0

        # Price filtering
        if 'price_max' in filters and filters['price_max']:
            try:
                max_p = int(filters['price_max'])
                filtered = [x for x in filtered if 0 < get_price_int(x) <= max_p]
            except:
                pass
        
        if 'price_min' in filters and filters['price_min']:
            try:
                min_p = int(filters['price_min'])
                filtered = [x for x in filtered if get_price_int(x) >= min_p]
            except:
                pass
        
        sort_type = filters.get('sort')
        if sort_type == 'price_desc':
            filtered.sort(key=get_price_int, reverse=True)
        elif sort_type == 'price_asc':
            filtered.sort(key=lambda x: (get_price_int(x) == 0, get_price_int(x)))
        elif sort_type == 'date_asc':
            filtered.sort(key=lambda x: x.get('date', x.get('added_at', '1970-01-01')) or '1970-01-01', reverse=False)
        else:
            # Default: date_desc — newest first
            filtered.sort(key=lambda x: x.get('date', x.get('added_at', '1970-01-01')) or '1970-01-01', reverse=True)
        
        # Пагинация
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 0))
        if limit > 0:
            filtered = filtered[offset:offset + limit]
        _enrich_tg_images(filtered)
        _mask_internal_channels(filtered)
        return jsonify(filtered)
    
    # Для категории chat — подмешиваем живые данные из chatiparsing
    if category == 'chat':
        try:
            import time as _time
            now = _time.time()
            if now - _chatiparsing_cache['ts'] > 5 or not _chatiparsing_cache['data']:
                from bs4 import BeautifulSoup
                import re as _re
                resp = requests.get('https://t.me/s/chatiparsing', timeout=8,
                                    headers={'User-Agent': 'Mozilla/5.0'})
                soup = BeautifulSoup(resp.text, 'html.parser')
                live = []
                for wrap in soup.find_all('div', class_='tgme_widget_message_wrap'):
                    text_el = wrap.find('div', class_='tgme_widget_message_text')
                    date_el = wrap.find('time')
                    if not text_el:
                        continue
                    raw = text_el.get_text('\n', strip=True)
                    tg_links = re.findall(r'https://t\.me/([\w_]+)/(\d+)', raw)
                    src_ch = tg_links[-1][0] if tg_links else ''
                    src_link = f'https://t.me/{tg_links[-1][0]}/{tg_links[-1][1]}' if tg_links else ''
                    display = re.sub(r'https://t\.me/\S+', '', raw).strip()
                    live.append({
                        'description': display,
                        'title': display[:60],
                        'source_channel': f'@{src_ch}' if src_ch else '',
                        'tg_link': src_link,
                        'date': date_el.get('datetime', '') if date_el else '',
                        'category': 'chat',
                    })
                _chatiparsing_cache['data'] = live
                _chatiparsing_cache['ts'] = now
            live_items = _chatiparsing_cache.get('data', [])
            existing_descs = set((x.get('description','') or '')[:50] for x in filtered)
            for item in live_items:
                if (item.get('description','') or '')[:50] not in existing_descs:
                    filtered.append(item)
        except Exception as e:
            print(f'chatiparsing merge error: {e}')
        filtered = [m for m in filtered if not _is_spam(m.get('description', '') or m.get('title', ''))]

    _TH_ONLY_CHANNELS = {
        'rent_thailand_chat', 'rentinthai', 'chat_phuket', 'chats_phuket',
        'phuket_chatbg', 'barakholka_pkhuket', 'huahinrus',
        'bangkok_chat_znakomstva', 'bangkok_market_bg', 'vse_svoi_bangkok',
        'visa_thailand_chat', 'thailand_4at', 'thailand_chatt1',
        'thailandchat_inf', 'chat_thailand', 'bangkok_chatbg',
        'chat_bangkok', 'bangkok_chats', 'pattayasale',
        'pattayachatonline', 'pattayapar', 'chats_pattaya',
        'phuketdating', 'krabichat',
    }
    if country == 'vietnam' and category == 'chat':
        filtered = [m for m in filtered if (m.get('source_channel', '') or '').replace('@', '').lower() not in _TH_ONLY_CHANNELS]

    # Сортировка по дате - новые сверху
    filtered.sort(key=lambda x: x.get('date', x.get('added_at', '1970-01-01')) or '1970-01-01', reverse=True)
    
    # Пагинация
    offset = int(request.args.get('offset', 0))
    limit = int(request.args.get('limit', 0))
    if limit > 0:
        filtered = filtered[offset:offset + limit]
    
    _enrich_tg_images(filtered)
    _mask_internal_channels(filtered)
    return jsonify(filtered)

@app.route('/api/add-listing', methods=['POST'])
def add_listing():
    country = request.json.get('country', 'vietnam')
    data = load_data(country)

    listing = request.json
    
    category = listing.get('category')
    if category and category in data:
        # Запрет: недвижимость без фото не принимаем
        if category == 'real_estate':
            photos = listing.get('photos', []) or []
            img = listing.get('image_url', '') or ''
            all_imgs = listing.get('all_images', []) or []
            if not photos and not img and not all_imgs:
                return jsonify({'error': 'Объявления недвижимости без фото не принимаются'}), 400
        listing['added_at'] = datetime.now().isoformat()
        data[category].append(listing)
        save_data(country, data)
        return jsonify({'success': True, 'message': 'Объявление добавлено'})
    
    return jsonify({'error': 'Invalid category'}), 400

import shutil
from werkzeug.utils import secure_filename
import requests

BANNER_CONFIG_FILE = "banner_config.json"
UPLOAD_FOLDER = 'static/images/banners'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def load_banner_config():
    if os.path.exists(BANNER_CONFIG_FILE):
        with open(BANNER_CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            # Миграция старого формата в новый (web/mobile)
            migrated = False
            for country in config:
                if isinstance(config[country], list):
                    # Старый формат - мигрируем
                    config[country] = {
                        'web': config[country],
                        'mobile': []
                    }
                    migrated = True
            if migrated:
                save_banner_config(config)
            return config
    return {
        'vietnam': {'web': [], 'mobile': []},
        'thailand': {'web': [], 'mobile': []},
        'india': {'web': [], 'mobile': []},
        'indonesia': {'web': [], 'mobile': []}
    }

def save_banner_config(config):
    with open(BANNER_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

_BANNER_TG_GROUP = 'banner_vn'
_BANNER_TG_CHAT_ID = -1003825420004
_BANNER_DATA_FILE = 'banner_data.json'

def _load_banner_data():
    try:
        if os.path.exists(_BANNER_DATA_FILE):
            with open(_BANNER_DATA_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_banner_data(data):
    with open(_BANNER_DATA_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def handle_banner_channel_photo(msg_id, file_id):
    data = _load_banner_data()
    data[str(msg_id)] = {'file_id': file_id, 'ts': int(datetime.now().timestamp())}
    _save_banner_data(data)
    with _msg_to_file_id_lock:
        _msg_to_file_id[(_BANNER_TG_GROUP, msg_id)] = file_id
    _update_banner_config_from_data(data)
    logger.info(f'[banner_sync] Фото баннер добавлен: msg_id={msg_id}')

def handle_banner_channel_delete(msg_id):
    data = _load_banner_data()
    if str(msg_id) in data:
        del data[str(msg_id)]
        _save_banner_data(data)
        _update_banner_config_from_data(data)
        logger.info(f'[banner_sync] Баннер удалён: msg_id={msg_id}')

def _update_banner_config_from_data(data):
    if not data:
        config = load_banner_config()
        config['vietnam']['mobile'] = []
        config['vietnam']['web'] = []
        save_banner_config(config)
        return
    sorted_ids = sorted(data.keys(), key=lambda x: int(x))
    new_banners = []
    for mid in sorted_ids:
        # Используем прокси /api/banner-img/<msg_id> — Bot API redirect, без CDN
        new_banners.append(f'/api/banner-img/{mid}')
    config = load_banner_config()
    config['vietnam']['mobile'] = new_banners
    config['vietnam']['web'] = new_banners
    save_banner_config(config)
    logger.info(f'[banner_sync] Обновлено: {len(new_banners)} прокси-ссылок для баннеров')

def _load_banner_file_ids_to_cache():
    import time as _t
    _t.sleep(3)
    data = _load_banner_data()
    loaded = 0
    for mid_str, info in data.items():
        fid = info.get('file_id', '')
        if fid:
            try:
                with _msg_to_file_id_lock:
                    _msg_to_file_id[(_BANNER_TG_GROUP, int(mid_str))] = fid
                loaded += 1
            except NameError:
                pass
    if loaded:
        _update_banner_config_from_data(data)
        logger.info(f'[banner_sync] Загружено {loaded} баннеров file_id из кеша')
    else:
        _update_banner_config_from_data(data)

def _prewarm_banner_cache(data):
    pass

_BANNER_EXCLUDE_IDS = {4}

def _do_sync_media_vn_banners():
    """Скрейпит t.me/s/banner_vn, получает свежие CDN URLs и сохраняет их в banner_data.json."""
    import time as _t
    try:
        from bs4 import BeautifulSoup as _BS
        channel = _BANNER_TG_GROUP
        base_url = f'https://t.me/s/{channel}'
        page_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        og_headers = {'User-Agent': 'TelegramBot (like TwitterBot)'}
        data = _load_banner_data()
        added = 0
        updated_urls = 0
        before = None
        max_pages = 20
        for _page in range(max_pages):
            params = {}
            if before:
                params['before'] = before
            try:
                resp = requests.get(base_url, params=params, headers=page_headers, timeout=20)
                if resp.status_code != 200:
                    break
                soup = _BS(resp.text, 'html.parser')
                ids_on_page = []
                for msg_div in soup.select('.tgme_widget_message'):
                    data_post = msg_div.get('data-post', '')
                    if '/' not in data_post:
                        continue
                    try:
                        mid = int(data_post.split('/')[-1])
                    except ValueError:
                        continue
                    ids_on_page.append(mid)
                    if mid in _BANNER_EXCLUDE_IDS:
                        continue
                    cdn_url = ''
                    photo_wraps = msg_div.select('.tgme_widget_message_photo_wrap')
                    if not photo_wraps:
                        photo_wraps = msg_div.select('a.tgme_widget_message_photo_wrap')
                    for pw in photo_wraps:
                        style = pw.get('style', '')
                        m = re.search(r"background-image:\s*url\('([^']+)'\)", style)
                        if m:
                            cdn_url = m.group(1)
                            break
                    has_photo = bool(photo_wraps)
                    if has_photo:
                        mid_str = str(mid)
                        now_ts = int(_t.time())
                        if mid_str not in data:
                            data[mid_str] = {'file_id': '', 'cdn_url': cdn_url, 'cdn_ts': now_ts, 'ts': mid}
                            added += 1
                        elif cdn_url:
                            data[mid_str]['cdn_url'] = cdn_url
                            data[mid_str]['cdn_ts'] = now_ts
                            updated_urls += 1
                if not ids_on_page:
                    break
                before = min(ids_on_page)
                if before <= 1:
                    break
                _t.sleep(1)
            except Exception as e:
                logger.warning(f'[banner_sync] Ошибка скрейпинга страницы: {e}')
                break
        # Для записей без CDN URL — пробуем og:image
        for mid_str, info in data.items():
            if not info.get('cdn_url'):
                try:
                    og_resp = requests.get(f'https://t.me/{channel}/{mid_str}', headers=og_headers, timeout=10)
                    if og_resp.status_code == 200:
                        img_m = re.search(r'<meta property="og:image" content="([^"]+)"', og_resp.text)
                        if img_m:
                            data[mid_str]['cdn_url'] = img_m.group(1)
                            data[mid_str]['cdn_ts'] = int(_t.time())
                            updated_urls += 1
                except Exception:
                    pass
        if added > 0 or updated_urls > 0:
            _save_banner_data(data)
        _update_banner_config_from_data(data)
        logger.info(f'[banner_sync] Синк @{channel}: +{added} новых, {updated_urls} URL обновлено, всего {len(data)}')
    except Exception as e:
        logger.error(f'[banner_sync] Ошибка синка: {e}')

def _sync_media_vn_banners():
    import time as _t
    _t.sleep(3)
    _do_sync_media_vn_banners()

def _banner_refresh_scheduler():
    """Обновляет CDN URLs баннеров каждые 6 часов."""
    import time as _t
    while True:
        _t.sleep(6 * 3600)
        logger.info('[banner_refresh] Периодическое обновление CDN URLs баннеров...')
        _do_sync_media_vn_banners()

threading.Thread(target=_sync_media_vn_banners, daemon=True, name='BannerMediaVnSync').start()
threading.Thread(target=_banner_refresh_scheduler, daemon=True, name='BannerRefreshScheduler').start()
logger.info('[banner_sync] Синхронизация баннеров из @banner_vn запущена (обновление каждые 6ч)')

_banner_og_cache = {}

def _get_banner_file_id(msg_id):
    """Возвращает file_id для баннера из banner_data.json если есть."""
    try:
        data = _load_banner_data()
        entry = data.get(str(msg_id), {})
        return entry.get('file_id', '')
    except Exception:
        return ''

@app.route('/api/banner-img/<int:msg_id>')
def banner_image_proxy(msg_id):
    # 0) Приоритет — локальный файл полного качества
    try:
        banner_data = _load_banner_data()
        entry = banner_data.get(str(msg_id), {})
        local_url = entry.get('local_url', '')
        if local_url:
            local_path = local_url.lstrip('/')
            if os.path.exists(local_path):
                return redirect(local_url, code=302)
    except Exception:
        pass

    cache_key = msg_id
    if cache_key in _banner_og_cache:
        cached = _banner_og_cache[cache_key]
        # Validate cache is not expired (telesco.pe URLs expire in ~24h, keep for 1h)
        if isinstance(cached, tuple):
            url, ts = cached
            if time.time() - ts < 3600:
                return redirect(url)
        else:
            return redirect(cached)

    # 1) Попробуем Bot API (полное качество) если есть file_id
    file_id = _get_banner_file_id(msg_id)
    tg_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if file_id and tg_token:
        try:
            gf = requests.get(
                f'https://api.telegram.org/bot{tg_token}/getFile',
                params={'file_id': file_id}, timeout=8
            )
            if gf.status_code == 200:
                gf_json = gf.json()
                if gf_json.get('ok'):
                    fp = gf_json['result']['file_path']
                    img_url = f'https://api.telegram.org/file/bot{tg_token}/{fp}'
                    _banner_og_cache[cache_key] = (img_url, time.time())
                    return redirect(img_url)
        except Exception as e:
            logger.warning(f'[banner-img] Bot API error for {msg_id}: {e}')

    # 2) Fallback: og:image с t.me (работает без file_id, ниже качество)
    try:
        og_headers = {'User-Agent': 'TelegramBot (like TwitterBot)'}
        og_resp = requests.get(f'https://t.me/{_BANNER_TG_GROUP}/{msg_id}', headers=og_headers, timeout=10)
        if og_resp.status_code == 200:
            img_m = re.search(r'<meta property="og:image" content="([^"]+)"', og_resp.text)
            if img_m:
                img_url = img_m.group(1)
                _banner_og_cache[cache_key] = (img_url, time.time())
                return redirect(img_url)
    except Exception as e:
        logger.warning(f'[banner-img] og:image error for {msg_id}: {e}')
    return '', 404

@app.route('/api/banners')
def get_banners():
    config = load_banner_config()
    return jsonify(config)

@app.route('/api/admin/sync-banners', methods=['POST'])
def admin_sync_banners():
    """Ручной запуск синка баннеров из @banner_vn."""
    password = request.json.get('password', '') if request.json else request.form.get('password', '')
    if not password:
        return jsonify({'error': 'Unauthorized'}), 401
    valid_pw = os.environ.get('ADMIN_PASSWORD', '')
    if password != valid_pw:
        return jsonify({'error': 'Unauthorized'}), 401
    threading.Thread(target=_do_sync_media_vn_banners, daemon=True, name='BannerManualSync').start()
    return jsonify({'ok': True, 'message': f'Синк @{_BANNER_TG_GROUP} запущен в фоне'})

@app.route('/api/admin/upload-banner', methods=['POST'])
def admin_upload_banner():
    password = request.form.get('password', '')
    country = request.form.get('country', 'vietnam')
    banner_type = request.form.get('banner_type', 'web')  # web or mobile
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file:
        filename = secure_filename(f"{country}_{banner_type}_{int(time.time())}_{file.filename}")
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(file_path)
        url = f'/static/images/banners/{filename}'
        config = load_banner_config()
        if country not in config:
            config[country] = {'web': [], 'mobile': []}
        if banner_type not in config[country]:
            config[country][banner_type] = []
        config[country][banner_type].append(url)
        save_banner_config(config)
        
        return jsonify({'success': True, 'url': url})
    
    return jsonify({'error': 'Unknown error'}), 500

@app.route('/api/admin/delete-banner', methods=['POST'])
def admin_delete_banner():
    password = request.json.get('password', '')
    country = request.json.get('country')
    url = request.json.get('url')
    banner_type = request.json.get('banner_type', 'web')  # web or mobile
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    
    config = load_banner_config()
    if country in config and banner_type in config[country] and url in config[country][banner_type]:
        config[country][banner_type].remove(url)
        save_banner_config(config)
        return jsonify({'success': True})
    return jsonify({'error': 'Banner not found'}), 404

@app.route('/api/admin/reorder-banners', methods=['POST'])
def admin_reorder_banners():
    password = request.json.get('password', '')
    country = request.json.get('country')
    urls = request.json.get('urls')
    banner_type = request.json.get('banner_type', 'web')  # web or mobile
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    
    config = load_banner_config()
    if country in config:
        if banner_type not in config[country]:
            config[country][banner_type] = []
        config[country][banner_type] = urls
        save_banner_config(config)
        return jsonify({'success': True})
    return jsonify({'error': 'Country not found'}), 404

ADMIN_PASSWORDS = {
    'vietnam': 'BB888888!',
    'thailand': 'OO888888!',
    'india': 'GG666666!',
    'indonesia': 'XX111111!'
}

SUPER_ADMIN_PASSWORD = 'Shengli1983!'

def check_admin_password(password, country=None):
    """Check if password is valid for the given country or any country"""
    # Супер-админ имеет доступ ко всем странам
    if password == SUPER_ADMIN_PASSWORD:
        return True, 'all'
    
    if country and country in ADMIN_PASSWORDS:
        return password == ADMIN_PASSWORDS[country], country
    for c, pwd in ADMIN_PASSWORDS.items():
        if password == pwd:
            return True, c
    return False, None

DELIVERY_ORDERS_FILE = 'delivery_orders.json'

def _save_delivery_order(order: dict):
    try:
        orders = []
        if os.path.exists(DELIVERY_ORDERS_FILE):
            with open(DELIVERY_ORDERS_FILE, 'r', encoding='utf-8') as f:
                orders = json.load(f)
        orders.append(order)
        with open(DELIVERY_ORDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        logger.warning(f'Failed to save delivery order: {ex}')

@app.route('/api/delivery-order', methods=['POST'])
def delivery_order():
    import datetime as _dt
    data = request.json or {}
    tg = data.get('telegram', '').strip()
    amount = data.get('amount', '').strip()
    city = data.get('city', '').strip()
    address = data.get('address', '').strip()
    info = data.get('info', '').strip()
    if not tg or not amount or not city or not address:
        return jsonify(ok=False, error='Заполните все поля')

    order = {
        'ts': _dt.datetime.utcnow().isoformat(),
        'telegram': tg, 'amount': amount,
        'city': city, 'address': address, 'info': info
    }
    _save_delivery_order(order)

    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    admin_chat = os.environ.get('DELIVERY_ADMIN_CHAT', '-1003927701676')
    msg_text = (
        f"💸 Заявка на доставку наличных\n\n"
        f"👤 Telegram: {tg}\n"
        f"💰 Сумма: {amount}\n"
        f"🏙 Город: {city}\n"
        f"📍 Адрес: {address}"
    )
    if info:
        msg_text += f"\n📝 Доп. информация: {info}"
    tg_ok = False
    tg_error = ''
    if bot_token:
        try:
            resp = requests.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json={'chat_id': admin_chat, 'text': msg_text, 'parse_mode': 'HTML'},
                timeout=10
            )
            rj = resp.json()
            if rj.get('ok'):
                tg_ok = True
            else:
                tg_error = rj.get('description', str(resp.status_code))
                logger.warning(f'Delivery TG failed: {tg_error}')
        except Exception as ex:
            tg_error = str(ex)
            logger.warning(f'Delivery TG notify failed: {ex}')
    logger.info(f'[delivery] tg_sent={tg_ok} | {tg} | {amount} | {city} | {address}')
    return jsonify(ok=True, tg_sent=tg_ok, tg_error=tg_error if not tg_ok else '')


@app.route('/api/admin/delivery-orders', methods=['POST'])
def admin_delivery_orders():
    password = (request.json or {}).get('password', '')
    valid_pw = os.environ.get('ADMIN_PASSWORD', '')
    if not password or password != valid_pw:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        if os.path.exists(DELIVERY_ORDERS_FILE):
            with open(DELIVERY_ORDERS_FILE, 'r', encoding='utf-8') as f:
                orders = json.load(f)
        else:
            orders = []
    except Exception:
        orders = []
    return jsonify({'orders': orders})

TOUR_ORDERS_FILE = 'tour_orders.json'

def _save_tour_order(order: dict):
    try:
        orders = []
        if os.path.exists(TOUR_ORDERS_FILE):
            with open(TOUR_ORDERS_FILE, 'r', encoding='utf-8') as f:
                orders = json.load(f)
        orders.append(order)
        with open(TOUR_ORDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        logger.warning(f'Failed to save tour order: {ex}')

@app.route('/api/book-tour', methods=['POST'])
def book_tour():
    import datetime as _dt
    data = request.json or {}
    tg = data.get('telegram', '').strip()
    tour_name = data.get('tour_name', '').strip()
    city = data.get('city', '').strip()
    people = data.get('people', '').strip()
    date = data.get('date', '').strip()
    departure_point = data.get('departure_point', '').strip()
    extra = data.get('extra', '').strip()

    if not tg or not tour_name or not city or not people or not date or not departure_point:
        return jsonify(ok=False, error='Заполните все обязательные поля')

    order = {
        'ts': _dt.datetime.utcnow().isoformat(),
        'telegram': tg,
        'tour_name': tour_name,
        'city': city,
        'people': people,
        'date': date,
        'departure_point': departure_point,
        'extra': extra
    }
    _save_tour_order(order)

    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    admin_chat = os.environ.get('TOUR_ADMIN_CHAT', os.environ.get('DELIVERY_ADMIN_CHAT', '-1003927701676'))
    msg_text = (
        f"🧳 Заявка на Экскурсию\n\n"
        f"👤 Telegram: {tg}\n"
        f"🗺 Экскурсия: {tour_name}\n"
        f"🏙 Город: {city}\n"
        f"👥 Количество человек: {people}\n"
        f"📅 Дата: {date}\n"
        f"📍 Адрес / Google-локация: {departure_point}"
    )
    if extra:
        msg_text += f"\n📝 Доп. информация: {extra}"

    tg_ok = False
    tg_error = ''
    if bot_token:
        try:
            resp = requests.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json={'chat_id': admin_chat, 'text': msg_text, 'parse_mode': 'HTML'},
                timeout=10
            )
            rj = resp.json()
            if rj.get('ok'):
                tg_ok = True
            else:
                tg_error = rj.get('description', str(resp.status_code))
                logger.warning(f'Tour TG failed: {tg_error}')
        except Exception as ex:
            tg_error = str(ex)
            logger.warning(f'Tour TG notify failed: {ex}')
    logger.info(f'[tour] tg_sent={tg_ok} | {tg} | {tour_name} | {people}p | {date}')
    return jsonify(ok=True, tg_sent=tg_ok, tg_error=tg_error if not tg_ok else '')

VISARUN_ORDERS_FILE = 'visarun_orders.json'

def _save_visarun_order(order: dict):
    try:
        orders = []
        if os.path.exists(VISARUN_ORDERS_FILE):
            with open(VISARUN_ORDERS_FILE, 'r', encoding='utf-8') as f:
                orders = json.load(f)
        orders.append(order)
        with open(VISARUN_ORDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        logger.warning(f'Failed to save visarun order: {ex}')

@app.route('/api/book-visarun', methods=['POST'])
def book_visarun():
    import datetime as _dt
    data = request.json or {}
    tg = data.get('telegram', '').strip()
    direction = data.get('direction', '').strip()
    days = data.get('days', '').strip()
    nationality = data.get('nationality', '').strip()
    departure_date = data.get('departure_date', '').strip()
    departure_point = data.get('departure_point', '').strip()
    extra = data.get('extra', '').strip()

    if not tg or not direction or not days or not nationality or not departure_date or not departure_point:
        return jsonify(ok=False, error='Заполните все обязательные поля')

    order = {
        'ts': _dt.datetime.utcnow().isoformat(),
        'telegram': tg,
        'direction': direction,
        'days': days,
        'nationality': nationality,
        'departure_date': departure_date,
        'departure_point': departure_point,
        'extra': extra
    }
    _save_visarun_order(order)

    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    admin_chat = os.environ.get('VISARUN_ADMIN_CHAT', os.environ.get('DELIVERY_ADMIN_CHAT', '-1003927701676'))
    msg_text = (
        f"🛂 Заявка на Визаран\n\n"
        f"👤 Telegram: {tg}\n"
        f"🌏 Направление: {direction}\n"
        f"🗓️ Срок: {days} дней\n"
        f"🌍 Гражданство: {nationality}\n"
        f"📅 Дата выезда: {departure_date}\n"
        f"🚌 Выезд из: {departure_point}"
    )
    if extra:
        msg_text += f"\n📝 Доп. информация: {extra}"

    tg_ok = False
    tg_error = ''
    if bot_token:
        try:
            resp = requests.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json={'chat_id': admin_chat, 'text': msg_text, 'parse_mode': 'HTML'},
                timeout=10
            )
            rj = resp.json()
            if rj.get('ok'):
                tg_ok = True
            else:
                tg_error = rj.get('description', str(resp.status_code))
                logger.warning(f'Visarun TG failed: {tg_error}')
        except Exception as ex:
            tg_error = str(ex)
            logger.warning(f'Visarun TG notify failed: {ex}')
    logger.info(f'[visarun] tg_sent={tg_ok} | {tg} | {direction} | {days}d | {departure_date}')
    return jsonify(ok=True, tg_sent=tg_ok, tg_error=tg_error if not tg_ok else '')

@app.route('/api/admin/auth', methods=['POST'])
def admin_auth():
    password = request.json.get('password', '')
    country = request.json.get('country')
    
    is_valid, admin_country = check_admin_password(password, country)
    
    if is_valid:
        return jsonify({'success': True, 'authenticated': True, 'country': admin_country})
    return jsonify({'success': False, 'error': 'Invalid password'}), 401

@app.route('/api/admin/delete-listing', methods=['POST'])
def admin_delete():
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.json.get('category')
    listing_id = request.json.get('listing_id')
    
    # Маппинг категорий
    category_map = {'exchange': 'money_exchange', 'realestate': 'real_estate'}
    category = category_map.get(category, category)
    
    data = load_data(country)

    
    if category in data:
        data[category] = [x for x in data[category] if x.get('id') != listing_id]
        save_data(country, data)
        return jsonify({'success': True, 'message': f'Объявление {listing_id} удалено'})
    
    return jsonify({'error': 'Category not found'}), 404

@app.route('/api/admin/move-listing', methods=['POST'])
def admin_move():
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    from_category = request.json.get('from_category')
    to_category = request.json.get('to_category')
    listing_id = request.json.get('listing_id')
    
    data = load_data(country)

    
    if from_category not in data or to_category not in data:
        return jsonify({'error': 'Invalid category'}), 404
    
    # Найти объявление
    listing = None
    if from_category in data:
        for i, item in enumerate(data[from_category]):
            if item.get('id') == listing_id:
                listing = data[from_category].pop(i)
                break
    
    if not listing:
        return jsonify({'success': False, 'error': 'Listing not found'}), 404
    
    # Обновить категорию и переместить
    listing['category'] = to_category
    if to_category not in data:
        data[to_category] = []
    data[to_category].insert(0, listing)
    save_data(country, data)
    
    return jsonify({'success': True, 'message': f'Объявление перемещено в {to_category}'})

@app.route('/api/admin/toggle-visibility', methods=['POST'])
def admin_toggle_visibility():
    """Скрыть/показать объявление"""
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.json.get('category')
    listing_id = request.json.get('listing_id')
    
    # Маппинг категорий (exchange -> money_exchange)
    category_map = {'exchange': 'money_exchange', 'realestate': 'real_estate'}
    category = category_map.get(category, category)
    
    data = load_data(country)

    
    if category not in data:
        return jsonify({'error': 'Category not found'}), 404
    
    for item in data[category]:
        if item.get('id') == listing_id:
            current = item.get('hidden', False)
            item['hidden'] = not current
            save_data(country, data)
            status = 'скрыто' if item['hidden'] else 'видимо'
            return jsonify({'success': True, 'hidden': item['hidden'], 'message': f'Объявление {status}'})
    
    return jsonify({'error': 'Listing not found'}), 404

@app.route('/api/admin/bulk-hide', methods=['POST'])
def admin_bulk_hide():
    """Массовое скрытие объявлений по контакту"""
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.json.get('category')
    contact_name = request.json.get('contact_name')
    hide = request.json.get('hide', True)
    
    data = load_data(country)

    count = 0
    
    if category and category in data:
        categories = [category]
    else:
        categories = data.keys()
    
    for cat in categories:
        if cat in data:
            for item in data[cat]:
                cn = (item.get('contact_name') or item.get('contact') or '').lower()
                if contact_name.lower() in cn:
                    item['hidden'] = hide
                    count += 1
    
    save_data(country, data)
    action = 'скрыто' if hide else 'показано'
    return jsonify({'success': True, 'count': count, 'message': f'{count} объявлений {action}'})

@app.route('/api/admin/edit-listing', methods=['POST'])
def admin_edit():
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.json.get('category')
    listing_id = request.json.get('listing_id')
    updates = request.json.get('updates', {})
    
    # Маппинг категорий (exchange -> money_exchange)
    category_map = {'exchange': 'money_exchange', 'realestate': 'real_estate'}
    category = category_map.get(category, category)
    
    data = load_data(country)

    
    if category not in data:
        return jsonify({'error': 'Category not found'}), 404
    
    for item in data[category]:
        if item.get('id') == listing_id:
            if 'title' in updates:
                item['title'] = updates['title']
            if 'description' in updates:
                item['description'] = updates['description']
            if 'price' in updates:
                try:
                    item['price'] = int(updates['price']) if updates['price'] else 0
                except:
                    item['price'] = 0
            if 'rooms' in updates:
                item['rooms'] = updates['rooms'] if updates['rooms'] else None
            if 'area' in updates:
                try:
                    item['area'] = float(updates['area']) if updates['area'] else None
                except:
                    item['area'] = None
            if 'date' in updates:
                item['date'] = updates['date'] if updates['date'] else None
            if 'whatsapp' in updates:
                item['whatsapp'] = updates['whatsapp'] if updates['whatsapp'] else None
            if 'telegram' in updates:
                item['telegram'] = updates['telegram'] if updates['telegram'] else None
            if 'contact_name' in updates:
                item['contact_name'] = updates['contact_name'] if updates['contact_name'] else None
            if 'listing_type' in updates:
                item['listing_type'] = updates['listing_type'] if updates['listing_type'] else None
            if 'city' in updates:
                item['city'] = updates['city'] if updates['city'] else None
            if 'google_maps' in updates:
                item['google_maps'] = updates['google_maps'] if updates['google_maps'] else None
            if 'google_rating' in updates:
                item['google_rating'] = updates['google_rating'] if updates['google_rating'] else None
            if 'kitchen' in updates:
                item['kitchen'] = updates['kitchen'] if updates['kitchen'] else None
            if 'restaurant_type' in updates:
                item['restaurant_type'] = updates['restaurant_type'] if updates['restaurant_type'] else None
            if 'price_category' in updates:
                item['price_category'] = updates['price_category'] if updates['price_category'] else None
            if 'kids_age' in updates:
                item['kids_age'] = updates['kids_age'] if updates['kids_age'] else None
                item['age'] = updates['kids_age'] if updates['kids_age'] else None
            if 'currency_pairs' in updates:
                item['currency_pairs'] = updates['currency_pairs'] if updates['currency_pairs'] else None
            if 'image_url' in updates and updates['image_url']:
                image_url = updates['image_url']
                if image_url.startswith('data:'):
                    try:
                        import base64
                        header, b64_data = image_url.split(',', 1)
                        image_data = base64.b64decode(b64_data)
                        caption = f"📷 {item.get('title', 'Объявление')}"
                        file_id = send_photo_to_channel(image_data, caption)
                        if file_id:
                            item['telegram_file_id'] = file_id
                            item['telegram_photo'] = True
                            fresh_url = get_telegram_photo_url(file_id)
                            if fresh_url:
                                item['image_url'] = fresh_url
                    except Exception as e:
                        print(f"Error uploading new photo: {e}")
                        item['image_url'] = image_url
                else:
                    item['image_url'] = image_url
            
            save_data(country, data)
            return jsonify({'success': True, 'message': 'Объявление обновлено'})
    
    return jsonify({'error': 'Listing not found'}), 404

@app.route('/api/admin/update-listing-with-photo', methods=['POST'])
def admin_update_listing_with_photo():
    password = request.form.get('password', '')
    country = request.form.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.form.get('category')
    listing_id = request.form.get('listing_id')
    
    category_map = {'exchange': 'money_exchange', 'realestate': 'real_estate'}
    category = category_map.get(category, category)
    
    data = load_data(country)
    
    if category not in data:
        return jsonify({'error': 'Category not found'}), 404
    
    for item in data[category]:
        if item.get('id') == listing_id:
            if request.form.get('title'):
                item['title'] = request.form.get('title')
            if request.form.get('description'):
                item['description'] = request.form.get('description')
            if request.form.get('city'):
                item['city'] = request.form.get('city')
            if request.form.get('currency_pairs'):
                item['currency_pairs'] = request.form.get('currency_pairs')
            if request.form.get('marketplace_category'):
                item['marketplace_category'] = request.form.get('marketplace_category')
            if request.form.get('destination'):
                item['destination'] = request.form.get('destination')
            if request.form.get('photo_type'):
                item['photo_type'] = request.form.get('photo_type')
            if request.form.get('contact_name'):
                item['contact_name'] = request.form.get('contact_name')
            if request.form.get('whatsapp'):
                item['whatsapp'] = request.form.get('whatsapp')
            if request.form.get('telegram'):
                item['telegram'] = request.form.get('telegram')
            
            # Additional category-specific fields
            if request.form.get('price'):
                item['price'] = request.form.get('price')
            if request.form.get('location'):
                item['location'] = request.form.get('location')
            if request.form.get('days'):
                item['days'] = request.form.get('days')
            if request.form.get('engine'):
                item['engine'] = request.form.get('engine')
            if request.form.get('year'):
                item['year'] = request.form.get('year')
            if request.form.get('transport_type'):
                item['transport_type'] = request.form.get('transport_type')
            if request.form.get('kitchen'):
                item['kitchen'] = request.form.get('kitchen')
            if request.form.get('google_maps'):
                item['google_maps'] = request.form.get('google_maps')
            if request.form.get('google_rating'):
                item['google_rating'] = request.form.get('google_rating')
            if request.form.get('restaurant_type'):
                item['restaurant_type'] = request.form.get('restaurant_type')
            if request.form.get('property_type'):
                item['property_type'] = request.form.get('property_type')
            if request.form.get('rooms'):
                item['rooms'] = request.form.get('rooms')
            if request.form.get('area'):
                item['area'] = request.form.get('area')
            if request.form.get('listing_type'):
                item['listing_type'] = request.form.get('listing_type')
            
            # Handle single photo (backwards compatibility)
            photo = request.files.get('photo')
            if photo and photo.filename:
                try:
                    image_data = photo.read()
                    caption = f"📷 {item.get('title', 'Объявление')}"
                    file_id = send_photo_to_channel(image_data, caption)
                    if file_id:
                        item['telegram_file_id'] = file_id
                        item['telegram_photo'] = True
                        fresh_url = get_telegram_photo_url(file_id)
                        if fresh_url:
                            item['image_url'] = fresh_url
                except Exception as e:
                    print(f"Error uploading photo: {e}")
            
            # Handle 4 photos (photo_0, photo_1, photo_2, photo_3)
            photo_fields = ['image_url', 'image_url_2', 'image_url_3', 'image_url_4']
            for i in range(4):
                photo_file = request.files.get(f'photo_{i}')
                if photo_file and photo_file.filename:
                    try:
                        image_data = photo_file.read()
                        print(f"DEBUG: Processing photo_{i}, size={len(image_data)} bytes")
                        caption = f"📷 {item.get('title', 'Объявление')} - фото {i+1}"
                        file_id = send_photo_to_channel(image_data, caption)
                        print(f"DEBUG: photo_{i} uploaded, file_id={file_id[:50] if file_id else 'None'}...")
                        if file_id:
                            fresh_url = get_telegram_photo_url(file_id)
                            print(f"DEBUG: photo_{i} fresh_url={fresh_url}")
                            if fresh_url:
                                old_url = item.get(photo_fields[i])
                                item[photo_fields[i]] = fresh_url
                                print(f"DEBUG: Updated {photo_fields[i]}: {old_url} -> {fresh_url}")
                                if i == 0:
                                    item['telegram_file_id'] = file_id
                                    item['telegram_photo'] = True
                            else:
                                print(f"DEBUG: fresh_url is empty/None for photo_{i}")
                    except Exception as e:
                        print(f"Error uploading photo_{i}: {e}")
            
            save_data(country, data)
            return jsonify({'success': True, 'message': 'Объявление обновлено'})
    
    return jsonify({'error': 'Listing not found'}), 404

@app.route('/api/admin/get-listing', methods=['POST'])
def admin_get_listing():
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.json.get('category')
    listing_id = request.json.get('listing_id')
    
    # Маппинг категорий (exchange -> money_exchange)
    category_map = {'exchange': 'money_exchange', 'realestate': 'real_estate'}
    category = category_map.get(category, category)
    
    data = load_data(country)

    
    if category not in data:
        return jsonify({'error': 'Category not found'}), 404
    
    for item in data[category]:
        if item.get('id') == listing_id:
            return jsonify(item)
    
    return jsonify({'error': 'Listing not found'}), 404

def load_pending_listings(country='vietnam'):
    pending_file = f"pending_{country}.json"
    if os.path.exists(pending_file):
        with open(pending_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_pending_listings(country, listings):
    pending_file = f"pending_{country}.json"
    with open(pending_file, 'w', encoding='utf-8') as f:
        json.dump(listings, f, ensure_ascii=False, indent=2)

@app.route('/api/submit-listing', methods=['POST'])
def submit_listing():
    try:
        captcha_answer = request.form.get('captcha_answer', '')
        captcha_token = request.form.get('captcha_token', '')
        
        if captcha_token:
            expected = captcha_storage.get(captcha_token)
            if not expected or captcha_answer != expected:
                return jsonify({'error': 'Неверная капча'}), 400
            if captcha_token in captcha_storage:
                del captcha_storage[captcha_token]
        
        country = request.form.get('country', 'vietnam')
        category = request.form.get('category', 'other')
        title = request.form.get('title', '')
        description = request.form.get('description', '')
        price = request.form.get('price', '')
        city = request.form.get('city', '')
        whatsapp = request.form.get('whatsapp', '')
        telegram = request.form.get('telegram', '')
        
        rooms = request.form.get('rooms', '')
        area = request.form.get('area', '')
        location = request.form.get('location', '')
        listing_type = request.form.get('listing_type', '')
        contact_name = request.form.get('contact_name', '')
        
        if not title or not description:
            return jsonify({'error': 'Заполните название и описание'}), 400
        
        if not telegram:
            return jsonify({'error': 'Заполните Telegram контакт'}), 400
        
        images = []
        photos = request.files.getlist('photos')
        if photos:
            for i, file in enumerate(photos):
                if file and file.filename:
                    import base64
                    file_data = file.read()
                    if len(file_data) > 20 * 1024 * 1024:
                        return jsonify({'error': f'Фото {i+1} превышает 20 МБ'}), 400
                    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
                    data_url = f"data:image/{ext};base64,{base64.b64encode(file_data).decode()}"
                    images.append(data_url)
        
        if not images:
            for i in range(4):
                file = request.files.get(f'photo_{i}')
                if file and file.filename:
                    import base64
                    file_data = file.read()
                    if len(file_data) > 20 * 1024 * 1024:
                        return jsonify({'error': f'Фото {i+1} превышает 20 МБ'}), 400
                    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
                    data_url = f"data:image/{ext};base64,{base64.b64encode(file_data).decode()}"
                    images.append(data_url)
        
        listing_id = f"pending_{category}_{country}_{int(time.time())}_{len(load_pending_listings(country))}"
        
        new_listing = {
            'id': listing_id,
            'title': title,
            'description': description,
            'price': int(price) if price.isdigit() else price if price else 0,
            'city': city if city else None,
            'whatsapp': whatsapp,
            'telegram': telegram,
            'category': category,
            'image_url': images[0] if images else None,
            'all_images': images if len(images) > 1 else None,
            'date': datetime.now().isoformat(),
            'status': 'pending'
        }
        
        if rooms:
            new_listing['rooms'] = rooms
        if area:
            new_listing['area'] = float(area) if area else None
        if location:
            new_listing['location'] = location
        if listing_type:
            new_listing['listing_type'] = listing_type
        if contact_name:
            new_listing['contact_name'] = contact_name
        
        if category == 'money_exchange':
            new_listing['pairs'] = request.form.get('pairs', '')
            new_listing['address'] = request.form.get('address', '')
        elif category == 'visas':
            new_listing['destination'] = request.form.get('destination', '')
            new_listing['citizenship'] = request.form.get('citizenship', '')
        elif category == 'marketplace':
            new_listing['marketplace_category'] = request.form.get('marketplace_category', '')
        elif category == 'photosession' or category == 'news':
            new_listing['photo_type'] = request.form.get('photo_type', '')
        
        pending = load_pending_listings(country)
        pending.append(new_listing)
        save_pending_listings(country, pending)
        
        category_names = {
            'money_exchange': 'Финансы',
            'marketplace': 'Барахолка',
            'visas': 'Визаран',
            'photosession': 'Фотосессия',
            'news': 'Фотосессия',
            'real_estate': 'Недвижимость',
            'other': 'Другое'
        }
        cat_name = category_names.get(category, category)
        
        send_telegram_notification(f"<b>Новое объявление ({cat_name})</b>\n\n<b>{title}</b>\n{description[:200]}...\n\nГород: {city}\nЦена: {price}\n\n✈️ Telegram: {telegram}")
        
        return jsonify({'success': True, 'message': 'Объявление отправлено на модерацию'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/submit-restaurant', methods=['POST'])
def submit_restaurant():
    try:
        captcha_answer = request.form.get('captcha_answer', '')
        captcha_token = request.form.get('captcha_token', '')
        
        expected = captcha_storage.get(captcha_token)
        if not expected or captcha_answer != expected:
            return jsonify({'error': 'Неверная капча'}), 400
        
        if captcha_token in captcha_storage:
            del captcha_storage[captcha_token]
        
        country = request.form.get('country', 'vietnam')
        title = request.form.get('title', '')
        description = request.form.get('description', '')
        kitchen = request.form.get('kitchen', '')
        location = request.form.get('location', '')
        city = request.form.get('city', '')
        google_maps = request.form.get('google_maps', '')
        contact_name = request.form.get('contact_name', '')
        whatsapp = request.form.get('whatsapp', '')
        telegram = request.form.get('telegram', '')
        price_category = request.form.get('price_category', 'normal')
        restaurant_type = request.form.get('restaurant_type', 'ресторан')
        
        if not title or not description:
            return jsonify({'error': 'Заполните название и описание'}), 400
        
        images = []
        for i in range(4):
            file = request.files.get(f'photo_{i}')
            if file and file.filename:
                import base64
                file_data = file.read()
                if len(file_data) > 20 * 1024 * 1024:
                    return jsonify({'error': f'Фото {i+1} превышает 20 МБ'}), 400
                
                ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
                data_url = f"data:image/{ext};base64,{base64.b64encode(file_data).decode()}"
                images.append(data_url)
        
        listing_id = f"pending_restaurant_{country}_{int(time.time())}_{len(load_pending_listings(country))}"
        
        new_listing = {
            'id': listing_id,
            'title': title,
            'description': description,
            'kitchen': kitchen if kitchen else None,
            'location': location if location else None,
            'city': city if city else None,
            'google_maps': google_maps if google_maps else None,
            'restaurant_type': restaurant_type if restaurant_type else 'ресторан',
            'contact_name': contact_name,
            'whatsapp': whatsapp,
            'telegram': telegram,
            'price_category': price_category,
            'category': 'restaurants',
            'image_url': images[0] if images else None,
            'all_images': images if len(images) > 1 else None,
            'date': datetime.now().isoformat(),
            'status': 'pending'
        }
        
        pending = load_pending_listings(country)
        pending.append(new_listing)
        save_pending_listings(country, pending)
        
        send_telegram_notification(f"<b>Новый ресторан</b>\n\n<b>{title}</b>\n{description[:200]}...\n\nКухня: {kitchen}\n\n✈️ Написать в Telegram: @radimiralubvi")
        
        return jsonify({'success': True, 'message': 'Ресторан отправлен на модерацию'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/submit-entertainment', methods=['POST'])
def submit_entertainment():
    try:
        captcha_answer = request.form.get('captcha_answer', '')
        captcha_token = request.form.get('captcha_token', '')
        
        expected = captcha_storage.get(captcha_token)
        if not expected or captcha_answer != expected:
            return jsonify({'error': 'Неверная капча'}), 400
        
        if captcha_token in captcha_storage:
            del captcha_storage[captcha_token]
        
        country = request.form.get('country', 'vietnam')
        title = request.form.get('title', '')
        description = request.form.get('description', '')
        feature = request.form.get('feature', '')
        location = request.form.get('location', '')
        city = request.form.get('city', '')
        contact_name = request.form.get('contact_name', '')
        whatsapp = request.form.get('whatsapp', '')
        telegram = request.form.get('telegram', '')
        capacity = request.form.get('capacity', '50')
        
        if not title or not description:
            return jsonify({'error': 'Заполните название и описание'}), 400
        
        images = []
        for i in range(4):
            file = request.files.get(f'photo_{i}')
            if file and file.filename:
                import base64
                file_data = file.read()
                if len(file_data) > 20 * 1024 * 1024:
                    return jsonify({'error': f'Фото {i+1} превышает 20 МБ'}), 400
                
                ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
                data_url = f"data:image/{ext};base64,{base64.b64encode(file_data).decode()}"
                images.append(data_url)
        
        listing_id = f"pending_entertainment_{country}_{int(time.time())}_{len(load_pending_listings(country))}"
        
        new_listing = {
            'id': listing_id,
            'title': title,
            'description': description,
            'feature': feature if feature else None,
            'location': location if location else None,
            'city': city if city else None,
            'contact_name': contact_name,
            'whatsapp': whatsapp,
            'telegram': telegram,
            'capacity': capacity,
            'category': 'entertainment',
            'image_url': images[0] if images else None,
            'all_images': images if len(images) > 1 else None,
            'date': datetime.now().isoformat(),
            'status': 'pending'
        }
        
        pending = load_pending_listings(country)
        pending.append(new_listing)
        save_pending_listings(country, pending)
        
        send_telegram_notification(f"<b>Новое развлечение</b>\n\n<b>{title}</b>\n{description[:200]}...\n\nФишка: {feature}\n\n✈️ Написать в Telegram: @radimiralubvi")
        
        return jsonify({'success': True, 'message': 'Развлечение отправлено на модерацию'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/submit-tour', methods=['POST'])
def submit_tour():
    try:
        captcha_answer = request.form.get('captcha_answer', '')
        captcha_token = request.form.get('captcha_token', '')
        
        expected = captcha_storage.get(captcha_token)
        if not expected or captcha_answer != expected:
            return jsonify({'error': 'Неверная капча'}), 400
        
        if captcha_token in captcha_storage:
            del captcha_storage[captcha_token]
        
        country = request.form.get('country', 'vietnam')
        title = request.form.get('title', '')
        description = request.form.get('description', '')
        days = request.form.get('days', '1')
        price = request.form.get('price', '')
        location = request.form.get('location', '')
        city = request.form.get('city', '')
        contact_name = request.form.get('contact_name', '')
        whatsapp = request.form.get('whatsapp', '')
        telegram = request.form.get('telegram', '')
        group_size = request.form.get('group_size', '5')
        
        if not title or not description:
            return jsonify({'error': 'Заполните название и описание'}), 400
        
        images = []
        for i in range(4):
            file = request.files.get(f'photo_{i}')
            if file and file.filename:
                import base64
                file_data = file.read()
                if len(file_data) > 20 * 1024 * 1024:
                    return jsonify({'error': f'Фото {i+1} превышает 20 МБ'}), 400
                
                ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
                data_url = f"data:image/{ext};base64,{base64.b64encode(file_data).decode()}"
                images.append(data_url)
        
        listing_id = f"pending_tour_{country}_{int(time.time())}_{len(load_pending_listings(country))}"
        
        new_listing = {
            'id': listing_id,
            'title': title,
            'description': description,
            'days': days,
            'price': int(price) if price.isdigit() else 0,
            'location': location if location else None,
            'city': city if city else None,
            'contact_name': contact_name,
            'whatsapp': whatsapp,
            'telegram': telegram,
            'group_size': group_size,
            'category': 'tours',
            'image_url': images[0] if images else None,
            'all_images': images if len(images) > 1 else None,
            'date': datetime.now().isoformat(),
            'status': 'pending'
        }
        
        pending = load_pending_listings(country)
        pending.append(new_listing)
        save_pending_listings(country, pending)
        
        send_telegram_notification(f"<b>Новая экскурсия</b>\n\n<b>{title}</b>\n{description[:200]}...\n\nДней: {days}, Цена: ${price}\n\n✈️ Написать в Telegram: @radimiralubvi")
        
        return jsonify({'success': True, 'message': 'Экскурсия отправлена на модерацию'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/submit-transport', methods=['POST'])
def submit_transport():
    try:
        captcha_answer = request.form.get('captcha_answer', '')
        captcha_token = request.form.get('captcha_token', '')
        
        expected = captcha_storage.get(captcha_token)
        if not expected or captcha_answer != expected:
            return jsonify({'error': 'Неверная капча'}), 400
        
        if captcha_token in captcha_storage:
            del captcha_storage[captcha_token]
        
        country = request.form.get('country', 'vietnam')
        title = request.form.get('title', '')
        description = request.form.get('description', '')
        engine = request.form.get('engine', '')
        year = request.form.get('year', '')
        price = request.form.get('price', '')
        transport_type = request.form.get('transport_type', 'bikes')
        location = request.form.get('location', '')
        city = request.form.get('city', '')
        contact_name = request.form.get('contact_name', '')
        whatsapp = request.form.get('whatsapp', '')
        telegram = request.form.get('telegram', '')
        
        if not title or not description:
            return jsonify({'error': 'Заполните название и описание'}), 400
        
        images = []
        for i in range(4):
            file = request.files.get(f'photo_{i}')
            if file and file.filename:
                import base64
                file_data = file.read()
                if len(file_data) > 20 * 1024 * 1024:
                    return jsonify({'error': f'Фото {i+1} превышает 20 МБ'}), 400
                
                ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
                data_url = f"data:image/{ext};base64,{base64.b64encode(file_data).decode()}"
                images.append(data_url)
        
        listing_id = f"pending_transport_{country}_{int(time.time())}_{len(load_pending_listings(country))}"
        
        new_listing = {
            'id': listing_id,
            'title': title,
            'description': description,
            'engine': engine,
            'year': int(year) if year.isdigit() else None,
            'price': int(price) if price.isdigit() else 0,
            'transport_type': transport_type,
            'location': location if location else None,
            'city': city if city else None,
            'contact_name': contact_name,
            'whatsapp': whatsapp,
            'telegram': telegram,
            'category': 'transport',
            'image_url': images[0] if images else None,
            'all_images': images if len(images) > 1 else None,
            'date': datetime.now().isoformat(),
            'status': 'pending'
        }
        
        pending = load_pending_listings(country)
        pending.append(new_listing)
        save_pending_listings(country, pending)
        
        send_telegram_notification(f"<b>Новый транспорт</b>\n\n<b>{title}</b>\n{description[:200]}...\n\nДвигатель: {engine}cc, Год: {year}, Цена: ${price}\n\n✈️ Написать в Telegram: @radimiralubvi")
        
        return jsonify({'success': True, 'message': 'Транспорт отправлен на модерацию'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/submit-realestate', methods=['POST'])
def submit_realestate():
    try:
        captcha_answer = request.form.get('captcha_answer', '')
        captcha_token = request.form.get('captcha_token', '')
        
        expected = captcha_storage.get(captcha_token)
        if not expected or captcha_answer != expected:
            return jsonify({'error': 'Неверная капча'}), 400
        
        if captcha_token in captcha_storage:
            del captcha_storage[captcha_token]
        
        country = request.form.get('country', 'vietnam')
        title = request.form.get('title', '')
        description = request.form.get('description', '')
        realestate_type = request.form.get('realestate_type', 'apartment')
        rooms = request.form.get('rooms', '')
        area = request.form.get('area', '')
        price = request.form.get('price', '')
        city = request.form.get('city', '')
        location = request.form.get('location', '')
        google_maps = request.form.get('google_maps', '')
        contact_name = request.form.get('contact_name', '')
        whatsapp = request.form.get('whatsapp', '')
        telegram = request.form.get('telegram', '')
        
        if not title or not description:
            return jsonify({'error': 'Заполните название и описание'}), 400
        
        images = []
        for i in range(4):
            file = request.files.get(f'photo_{i}')
            if file and file.filename:
                import base64
                file_data = file.read()
                if len(file_data) > 20 * 1024 * 1024:
                    return jsonify({'error': f'Фото {i+1} превышает 20 МБ'}), 400
                
                ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
                data_url = f"data:image/{ext};base64,{base64.b64encode(file_data).decode()}"
                images.append(data_url)
        
        listing_id = f"pending_realestate_{country}_{int(time.time())}_{len(load_pending_listings(country))}"
        
        new_listing = {
            'id': listing_id,
            'title': title,
            'description': description,
            'realestate_type': realestate_type,
            'rooms': rooms,
            'area': int(area) if area and area.isdigit() else None,
            'price': int(price) if price.isdigit() else 0,
            'city': city if city else None,
            'location': location if location else None,
            'google_maps': google_maps if google_maps else None,
            'contact_name': contact_name,
            'whatsapp': whatsapp,
            'telegram': telegram,
            'category': 'real_estate',
            'image_url': images[0] if images else None,
            'all_images': images if len(images) > 1 else None,
            'date': datetime.now().isoformat(),
            'status': 'pending'
        }
        
        pending = load_pending_listings(country)
        pending.append(new_listing)
        save_pending_listings(country, pending)
        
        send_telegram_notification(f"<b>Новая недвижимость</b>\n\n<b>{title}</b>\n{description[:200]}...\n\nКомнат: {rooms}, Площадь: {area}м², Цена: {price} VND\n\n✈️ Telegram: {telegram}")
        
        return jsonify({'success': True, 'message': 'Недвижимость отправлена на модерацию'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/pending', methods=['POST'])
def admin_get_pending():
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    
    pending = load_pending_listings(country)
    return jsonify(pending)

@app.route('/api/admin/moderate', methods=['POST'])
def admin_moderate():
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    listing_id = request.json.get('listing_id')
    action = request.json.get('action')
    
    pending = load_pending_listings(country)
    listing = None
    
    for i, item in enumerate(pending):
        if item.get('id') == listing_id:
            listing = pending.pop(i)
            break
    
    if not listing:
        return jsonify({'error': 'Listing not found'}), 404
    
    save_pending_listings(country, pending)
    
    if action == 'approve':
        category = listing.get('category', 'real_estate')
        listing['id'] = f"{country}_{category}_{int(time.time())}"
        listing['status'] = 'approved'
        
        CATEGORY_CHANNELS = {
            'entertainment': {
                'vietnam': '@gavibeshub',
            },
            'restaurants': {
                'vietnam': '@restoranvietnam',
            },
            'real_estate': {
                'vietnam': '@dom_vn',
                'thailand': '@doma_th',
            },
            'transport': {
                'vietnam': '@bayk_vn',
                'thailand': '@bayk_th',
            },
            'visas': {
                'vietnam': '@vizaranvietnam',
            },
            'tours': {
                'vietnam': -5047481419,
            },
            'exchange': {
                'vietnam': -5095538636,
            },
            'money_exchange': {
                'vietnam': -5095538636,
            },
        }
        
        target_channel = CATEGORY_CHANNELS.get(category, {}).get(country)
        
        print(f"MODERATION: Checking image_url for listing {listing.get('id')}")
        print(f"MODERATION: image_url exists: {bool(listing.get('image_url'))}")
        if listing.get('image_url'):
            try:
                import base64
                image_url = listing['image_url']
                image_data = None
                print(f"MODERATION: image_url type: {image_url[:50] if image_url else 'None'}...")
                
                if image_url.startswith('data:'):
                    print("MODERATION: Decoding base64 image...")
                    header, b64_data = image_url.split(',', 1)
                    image_data = base64.b64decode(b64_data)
                    print(f"MODERATION: Decoded {len(image_data)} bytes")
                elif image_url.startswith('/static/') or image_url.startswith('static/'):
                    file_path = image_url.lstrip('/')
                    if os.path.exists(file_path):
                        with open(file_path, 'rb') as f:
                            image_data = f.read()
                elif image_url.startswith('http'):
                    try:
                        resp = requests.get(image_url, timeout=30)
                        if resp.status_code == 200:
                            image_data = resp.content
                    except:
                        pass
                
                if image_data:
                    if target_channel:
                        tg_msg = send_photo_to_group(image_data, listing, target_channel)
                        if tg_msg:
                            msg_id = tg_msg.get('message_id')
                            is_private_group = isinstance(target_channel, int) or (isinstance(target_channel, str) and not target_channel.startswith('@'))
                            if is_private_group:
                                photo_list = tg_msg.get('photo', [])
                                if photo_list:
                                    file_id = photo_list[-1].get('file_id', '')
                                    listing['telegram_file_id'] = file_id
                                    listing['telegram_photo'] = True
                                    fresh_url = get_telegram_photo_url(file_id)
                                    if fresh_url:
                                        listing['image_url'] = fresh_url
                                    listing['photos'] = [listing.get('image_url', '')]
                                    listing['all_images'] = listing['photos']
                                    print(f"MODERATION: Photo sent to private group {target_channel}, file_id saved")
                            else:
                                channel_username = target_channel.replace('@', '')
                                listing['telegram_link'] = f"https://t.me/{channel_username}/{msg_id}"
                                listing['source_channel'] = channel_username
                                listing['photos'] = [f"https://t.me/{channel_username}/{msg_id}"]
                                listing['all_images'] = listing['photos']
                                listing['image_url'] = f"/tg_img/{channel_username}/{msg_id}"
                                listing['telegram_photo'] = True
                                print(f"MODERATION: Photo sent to {target_channel}, msg_id={msg_id}")
                    else:
                        caption = f"📋 {listing.get('title', 'Объявление')}\n\n{listing.get('description', '')[:500]}"
                        file_id = send_photo_to_channel(image_data, caption)
                        if file_id:
                            listing['telegram_file_id'] = file_id
                            listing['telegram_photo'] = True
                            fresh_url = get_telegram_photo_url(file_id)
                            if fresh_url:
                                listing['image_url'] = fresh_url
            except Exception as e:
                print(f"Error uploading photo to Telegram: {e}")
        
        all_images = listing.get('all_images') or []
        extra_images = []
        for key in ['image_url_2', 'image_url_3', 'image_url_4']:
            img = listing.get(key)
            if img and img.startswith('data:'):
                extra_images.append(img)
        
        if extra_images and target_channel:
            is_private = isinstance(target_channel, int) or (isinstance(target_channel, str) and not target_channel.startswith('@'))
            for extra_img in extra_images:
                try:
                    import base64
                    header, b64_data = extra_img.split(',', 1)
                    extra_data = base64.b64decode(b64_data)
                    extra_msg = send_photo_to_group(extra_data, None, target_channel)
                    if extra_msg:
                        if is_private:
                            extra_photos = extra_msg.get('photo', [])
                            if extra_photos:
                                extra_fid = extra_photos[-1].get('file_id', '')
                                extra_url = get_telegram_photo_url(extra_fid)
                                if extra_url:
                                    all_images.append(extra_url)
                        else:
                            channel_username = target_channel.replace('@', '')
                            extra_msg_id = extra_msg.get('message_id')
                            all_images.append(f"https://t.me/{channel_username}/{extra_msg_id}")
                except:
                    pass
            if all_images:
                listing['all_images'] = all_images
                listing['photos'] = all_images
        
        data = load_data(country)

        if category not in data:
            data[category] = []
        # Недвижимость — только с фото
        if category == 'real_estate' and not (listing.get('image_url') or listing.get('photos')):
            return jsonify({'success': False, 'message': 'Объявление о недвижимости должно содержать фото'})
        data[category].insert(0, listing)
        save_data(country, data)
        return jsonify({'success': True, 'message': f'Объявление одобрено и добавлено в {category}'})
    else:
        return jsonify({'success': True, 'message': 'Объявление отклонено'})

captcha_storage = {}

@app.route('/api/captcha')
def get_captcha():
    import random
    import uuid
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    token = str(uuid.uuid4())[:8]
    captcha_storage[token] = str(a + b)
    if len(captcha_storage) > 1000:
        keys = list(captcha_storage.keys())[:500]
        for k in keys:
            del captcha_storage[k]
    return jsonify({'question': f'{a} + {b} = ?', 'token': token})

@app.route('/api/parser-config', methods=['GET', 'POST'])
def parser_config():
    country = request.args.get('country', 'vietnam')
    config_file = f'parser_config_{country}.json'
    
    if request.method == 'POST':
        config = request.json
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return jsonify({'success': True})
    
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    
    return jsonify({
        'channels': [],
        'keywords': [],
        'auto_parse_interval': 300
    })

@app.route('/api/parse-thailand', methods=['POST'])
def parse_thailand():
    try:
        from bot_parser import run_bot_parser
        result = run_bot_parser()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/thailand-channels')
def get_thailand_channels():
    channels_file = 'thailand_channels.json'
    if os.path.exists(channels_file):
        with open(channels_file, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify({})

@app.route('/bot/webhook', methods=['POST'])
def bot_webhook():
    from telegram_bot import handle_start, handle_app, send_message

    data = request.json
    if not data:
        return jsonify({'ok': True})

    message = data.get('message', {}) or data.get('channel_post', {})
    chat_id = message.get('chat', {}).get('id')
    text = (message.get('text', '') or '').split('@')[0].strip()
    user = message.get('from', {})
    user_name = user.get('first_name', '') or 'друг'

    if not chat_id:
        return jsonify({'ok': True})

    print(f"WEBHOOK: chat_id={chat_id}, chat_type={message.get('chat',{}).get('type')}, chat_title={message.get('chat',{}).get('title')}, text={text[:50] if text else ''}")

    chat_username = message.get('chat', {}).get('username', '').lower()
    try:
        from vietnamparsing_parser import EXTRA_CHANNELS, process_extra_channel_update, atomic_add_listing
        if chat_username in EXTRA_CHANNELS:
            category, subcategory = EXTRA_CHANNELS[chat_username]
            update_wrapped = {'message': message} if data.get('message') else {'channel_post': message}
            item = process_extra_channel_update(update_wrapped, chat_username, category, subcategory)
            if item:
                added = atomic_add_listing(category, item)
                if added:
                    print(f"WEBHOOK: New [{category}] listing from @{chat_username}: {item.get('title','')[:60]}")
                else:
                    print(f"WEBHOOK: Duplicate [{category}] from @{chat_username}: {item.get('id')}")
    except Exception as e_extra:
        print(f"WEBHOOK extra channel error: {e_extra}")

    if text == '/chatid':
        chat_title = message.get('chat', {}).get('title', 'N/A')
        chat_type = message.get('chat', {}).get('type', 'N/A')
        send_message(chat_id, f'📋 <b>Chat ID:</b> <code>{chat_id}</code>\n<b>Title:</b> {chat_title}\n<b>Type:</b> {chat_type}')
    elif text == '/start':
        handle_start(chat_id, user_name)
    elif text == '/app':
        handle_app(chat_id)
    elif text == '/help':
        send_message(chat_id, '🦌 <b>Goldantelope ASIA</b>\n\n/start — Главное меню\n/app — Открыть приложение\n/thailand — Тайланд\n/vietnam — Вьетнам\n/help — Помощь')
    elif text == '/thailand':
        from telegram_bot import get_webapp_url
        webapp_url = get_webapp_url()
        keyboard = {"inline_keyboard": [[{"text": "🇹🇭 Открыть Тайланд", "url": f"{webapp_url}/?country=thailand"}]]}
        send_message(chat_id, '🇹🇭 <b>Тайланд</b>\n\n70+ каналов:\n• Пхукет\n• Паттайя\n• Бангкок\n• Самуи\n\nВыберите жильё, транспорт, рестораны и многое другое!', keyboard)
    elif text == '/vietnam':
        from telegram_bot import get_webapp_url
        webapp_url = get_webapp_url()
        keyboard = {"inline_keyboard": [[{"text": "🇻🇳 Открыть Вьетнам", "url": f"{webapp_url}/?country=vietnam"}]]}
        send_message(chat_id, '🇻🇳 <b>Вьетнам</b>\n\n5,800+ объявлений:\n• Нячанг\n• Дананг\n• Хошимин\n• Ханой\n• Фукуок\n\nАренда, рестораны, туры и многое другое!', keyboard)
    elif text == '/auth':
        send_message(chat_id, '🔐 <b>Авторизация Telethon</b>\n\nОтправьте 5-значный код подтверждения из приложения Telegram.')
    elif text and text.isdigit() and len(text) == 5:
        with open('pending_code.txt', 'w') as f:
            f.write(text)
        send_message(chat_id, f'✅ Код {text} получен! Пробую авторизацию...')

    return jsonify({'ok': True})

@app.route('/bot/setup', methods=['POST'])
def setup_bot_webhook():
    import requests
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    domains = os.environ.get('REPLIT_DOMAINS', '')
    
    if domains:
        webhook_url = f"https://{domains.split(',')[0]}/bot/webhook"
        url = f'https://api.telegram.org/bot{bot_token}/setWebhook'
        result = requests.post(url, data={'url': webhook_url}).json()
        return jsonify(result)
    
    return jsonify({'error': 'No domain found'})

# ============ УПРАВЛЕНИЕ КАНАЛАМИ ============

def load_channels(country):
    """Загрузить каналы для страны"""
    channels_file = f'{country}_channels.json'
    if os.path.exists(channels_file):
        with open(channels_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('channels', {})
    return {}

def save_channels(country, channels):
    """Сохранить каналы для страны"""
    channels_file = f'{country}_channels.json'
    with open(channels_file, 'w', encoding='utf-8') as f:
        json.dump({'channels': channels}, f, ensure_ascii=False, indent=2)

@app.route('/api/admin/channels', methods=['GET'])
def get_channels():
    """Получить список каналов по странам"""
    country = request.args.get('country', 'vietnam')
    channels = load_channels(country)
    return jsonify({'country': country, 'channels': channels})

@app.route('/api/admin/add-channel', methods=['POST'])
def add_channel():
    """Добавить канал"""
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.json.get('category', 'chat')
    channel = request.json.get('channel', '').strip().replace('@', '')
    
    if not channel:
        return jsonify({'error': 'Channel name required'}), 400
    
    channels = load_channels(country)
    
    if category not in channels:
        channels[category] = []
    
    if channel in channels[category]:
        return jsonify({'error': 'Channel already exists'}), 400
    
    channels[category].append(channel)
    save_channels(country, channels)
    
    return jsonify({'success': True, 'message': f'Канал @{channel} добавлен в {category}'})

@app.route('/api/admin/remove-channel', methods=['POST'])
def remove_channel():
    """Удалить канал"""
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.json.get('category')
    channel = request.json.get('channel')
    
    channels = load_channels(country)
    
    if category in channels and channel in channels[category]:
        channels[category].remove(channel)
        save_channels(country, channels)
        return jsonify({'success': True, 'message': f'Канал @{channel} удален'})
    
    return jsonify({'error': 'Channel not found'}), 404

# ============ BOT SOURCES CONFIG ============

BOT_SOURCES_FILE = 'bot_sources.json'

@app.route('/api/admin/bot-sources', methods=['GET'])
def get_bot_sources():
    """Получить конфиг источников бота по странам"""
    try:
        if os.path.exists(BOT_SOURCES_FILE):
            with open(BOT_SOURCES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {}
        return jsonify({'sources': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/bot-sources/update', methods=['POST'])
def update_bot_sources():
    """Обновить конфиг источников бота"""
    req = request.json or {}
    password = req.get('password', '')
    is_valid, _ = check_admin_password(password)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    country = req.get('country', '')
    channels = req.get('channels', [])
    if not country:
        return jsonify({'error': 'Country required'}), 400
    try:
        data = {}
        if os.path.exists(BOT_SOURCES_FILE):
            with open(BOT_SOURCES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        data[country] = channels
        with open(BOT_SOURCES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ TELEGRAM PHOTO PROXY ============

# Disk-persistent cache: channel_postid → {url, ts}
_TG_PHOTO_CACHE_FILE = 'tg_photo_cache.json'
_TG_PHOTO_CACHE_TTL = 20 * 3600  # 20 hours (CDN URLs expire in ~24h)
_tg_photo_cache_lock = threading.Lock()

def _load_tg_photo_cache():
    if os.path.exists(_TG_PHOTO_CACHE_FILE):
        try:
            with open(_TG_PHOTO_CACHE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_tg_photo_cache(cache):
    try:
        with open(_TG_PHOTO_CACHE_FILE, 'w') as f:
            json.dump(cache, f)
    except Exception:
        pass

_tg_photo_cache = _load_tg_photo_cache()

_FILE_PATH_CACHE_FILE = 'tg_file_paths_cache.json'
_file_path_cache_lock = threading.Lock()

def _load_file_path_cache():
    try:
        if os.path.exists(_FILE_PATH_CACHE_FILE):
            with open(_FILE_PATH_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_file_path_cache(cache):
    try:
        with open(_FILE_PATH_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f)
    except Exception:
        pass

_file_path_cache = _load_file_path_cache()  # file_id → file_path, persisted

def _prewarm_restaurant_file_paths():
    """Background: pre-fetch file_paths for all restaurant tg_file_ids via Bot API."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _time
    _time.sleep(10)
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        return
    try:
        vn_path = 'listings_vietnam.json'
        if not os.path.exists(vn_path):
            return
        with open(vn_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        restaurants = data.get('restaurants', [])
        all_fids = []
        for r in restaurants:
            for fid in (r.get('tg_file_ids') or []):
                with _file_path_cache_lock:
                    if fid not in _file_path_cache:
                        all_fids.append(fid)
        if not all_fids:
            logger.info('file_path cache already warm.')
            return
        logger.info(f'Pre-warming file_path cache for {len(all_fids)} file_ids...')

        def _fetch_one(fid):
            try:
                r = requests.get(
                    f'https://api.telegram.org/bot{bot_token}/getFile',
                    params={'file_id': fid}, timeout=10
                )
                if r.status_code == 200 and r.json().get('ok'):
                    return fid, r.json()['result']['file_path']
            except Exception:
                pass
            return fid, None

        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(_fetch_one, fid): fid for fid in all_fids}
            for future in as_completed(futures):
                fid, fp = future.result()
                if fp:
                    with _file_path_cache_lock:
                        _file_path_cache[fid] = fp

        with _file_path_cache_lock:
            _save_file_path_cache(dict(_file_path_cache))
        logger.info(f'Pre-warm complete: {len(_file_path_cache)} file_paths cached and saved.')
    except Exception as e:
        logger.warning(f'Pre-warm error: {e}')

threading.Thread(target=_prewarm_restaurant_file_paths, daemon=True).start()


def _prewarm_restaurant_disk_photos():
    """Фоновый прогрев: скачивает фото всех ресторанов через Bot API на диск один раз.
    После этого /tg_img/ отдаёт с диска — CDN не используется совсем."""
    import time as _t
    _t.sleep(25)  # подождать пока индекс file_id построится
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        logger.warning('[disk_prewarm] Нет TELEGRAM_BOT_TOKEN, прогрев пропущен')
        return
    try:
        vn_data = json.load(open('listings_vietnam.json', 'r', encoding='utf-8'))
        rests = vn_data.get('restaurants', [])
    except Exception:
        return
    cached_dir = _TG_DISK_CACHE_DIR
    downloaded = 0; skipped = 0; failed = 0
    for r in rests:
        msg_ids = r.get('photo_msg_ids') or []
        fids = r.get('tg_file_ids') or []
        if not msg_ids or not fids:
            failed += 1
            continue
        mid = msg_ids[0]
        fid = fids[0]
        disk_path = os.path.join(cached_dir, f'restoranvietnam_{mid}.jpg')
        if os.path.exists(disk_path) and os.path.getsize(disk_path) > 0:
            skipped += 1
            continue
        img_data = _bot_api_download(fid, bot_token)
        if img_data:
            try:
                tmp = disk_path + '.tmp'
                with open(tmp, 'wb') as f:
                    f.write(img_data)
                os.replace(tmp, disk_path)
                downloaded += 1
            except Exception as ex:
                logger.debug(f'disk prewarm save {mid}: {ex}')
                failed += 1
        else:
            failed += 1
        _t.sleep(0.15)
    logger.info(f'[disk_prewarm] Bot API: {downloaded} скачано, {skipped} уже есть, {failed} ошибок')


threading.Thread(target=_prewarm_restaurant_disk_photos, daemon=True, name='DiskPhotoPrewarm').start()

@app.route('/api/tgphoto/<path:file_id>')
def tg_photo_redirect(file_id):
    """Редирект на прямую ссылку Telegram CDN через Bot API getFile.
    Браузер скачивает фото напрямую с серверов Telegram — без серверного скачивания."""
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        return Response(status=503)
    try:
        with _file_path_cache_lock:
            file_path = _file_path_cache.get(file_id)
        if not file_path:
            r = requests.get(
                f'https://api.telegram.org/bot{bot_token}/getFile',
                params={'file_id': file_id}, timeout=10
            )
            if not (r.status_code == 200 and r.json().get('ok')):
                return Response(status=404)
            file_path = r.json()['result']['file_path']
            with _file_path_cache_lock:
                _file_path_cache[file_id] = file_path
                if len(_file_path_cache) % 20 == 0:
                    _save_file_path_cache(dict(_file_path_cache))
        direct_url = f'https://api.telegram.org/file/bot{bot_token}/{file_path}'
        return redirect(direct_url, code=302)
    except Exception as e:
        logger.warning(f'tg_photo_redirect error for {file_id}: {e}')
    return Response(status=404)


@app.route('/tg_file/<path:file_id>')
def tg_file_proxy(file_id):
    """Redirect browser to direct Telegram file URL via Bot API. No server-side download."""
    from flask import redirect as _redirect
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        return Response(status=503)
    try:
        with _file_path_cache_lock:
            file_path = _file_path_cache.get(file_id)

        if not file_path:
            r = requests.get(
                f'https://api.telegram.org/bot{bot_token}/getFile',
                params={'file_id': file_id},
                timeout=10
            )
            if not (r.status_code == 200 and r.json().get('ok')):
                return Response(status=404)
            file_path = r.json()['result']['file_path']
            with _file_path_cache_lock:
                _file_path_cache[file_id] = file_path
                if len(_file_path_cache) % 20 == 0:
                    _save_file_path_cache(dict(_file_path_cache))

        tg_url = f'https://api.telegram.org/file/bot{bot_token}/{file_path}'
        return _redirect(tg_url, code=302)
    except Exception as e:
        logger.warning(f'tg_file_proxy error for {file_id}: {e}')
    return Response(status=404)


_TG_DISK_CACHE_DIR = 'tg_photo_cache'
os.makedirs(_TG_DISK_CACHE_DIR, exist_ok=True)

# Маппинг: (channel, msg_id) → file_id, построенный из listings_vietnam.json при старте
_msg_to_file_id: dict = {}
_msg_to_file_id_lock = threading.Lock()


def _build_msg_to_file_id_index():
    """Индексируем все tg_file_ids из листингов: (channel, msg_id) → file_id."""
    try:
        vn = json.load(open('listings_vietnam.json', 'r', encoding='utf-8'))
        idx = {}
        for r in vn.get('restaurants', []):
            mids = r.get('photo_msg_ids') or []
            fids = r.get('tg_file_ids') or []
            ch = r.get('source', 'restoranvietnam')
            for i, mid in enumerate(mids):
                if i < len(fids) and fids[i]:
                    idx[(ch, int(mid))] = fids[i]
        if os.path.exists('file_id_index.json'):
            try:
                with open('file_id_index.json', 'r') as f:
                    extra_idx = json.load(f)
                for key, fid in extra_idx.items():
                    parts = key.rsplit('_', 1)
                    if len(parts) == 2:
                        idx[(parts[0], int(parts[1]))] = fid
            except Exception:
                pass
        with _msg_to_file_id_lock:
            _msg_to_file_id.update(idx)
        logger.info(f'[file_id_index] Проиндексировано {len(idx)} пар msg_id→file_id')
    except Exception as e:
        logger.warning(f'[file_id_index] Ошибка: {e}')


threading.Thread(target=_build_msg_to_file_id_index, daemon=True, name='FileIdIndexer').start()


def _save_file_id_index():
    """Сохраняет _msg_to_file_id в file_id_index.json (периодически, чтобы пережить рестарт)."""
    try:
        with _msg_to_file_id_lock:
            snapshot = dict(_msg_to_file_id)
        # Сериализуем: ключ (channel, msg_id) → строка "channel_msg_id"
        out = {f'{ch}_{mid}': fid for (ch, mid), fid in snapshot.items()}
        tmp = 'file_id_index.json.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False)
        os.replace(tmp, 'file_id_index.json')
        logger.debug(f'[file_id_index] Сохранено {len(out)} пар')
    except Exception as e:
        logger.warning(f'[file_id_index] Ошибка сохранения: {e}')


def _file_id_autosave_loop():
    """Раз в 5 минут сохраняем file_id индекс на диск."""
    import time as _t
    _t.sleep(60)  # первое сохранение через минуту
    while True:
        _save_file_id_index()
        _t.sleep(300)


threading.Thread(target=_file_id_autosave_loop, daemon=True, name='FileIdAutosave').start()


def _auto_delete_webhook():
    """Удаляет webhook бота при запуске, чтобы работал polling через getUpdates."""
    import time as _time
    _time.sleep(3)
    try:
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
        if not bot_token:
            return
        r = requests.post(
            f'https://api.telegram.org/bot{bot_token}/deleteWebhook',
            json={'drop_pending_updates': False},
            timeout=10
        )
        result = r.json()
        if result.get('ok'):
            logger.info('[bot] Webhook удалён — переключено на polling (getUpdates каждые 30с)')
        else:
            logger.warning(f'[bot] Ошибка удаления webhook: {result}')
    except Exception as e:
        logger.warning(f'[bot] Исключение при удалении webhook: {e}')


threading.Thread(target=_auto_delete_webhook, daemon=True, name='BotWebhookDelete').start()


# ============ ФОНОВЫЙ ПОЛЛЕР КАНАЛА @banner_vn ============

_gavibeshub_poll_offset = 0
_gavibeshub_poll_lock = threading.Lock()
GAVIBESHUB_POLL_INTERVAL = 30  # секунд

def _gavibeshub_poller():
    """Фоновый поллер: получает новые посты из @media_vn через Bot API getUpdates
    и добавляет их в категорию entertainment (Vietnam)."""
    import time as _time
    global _gavibeshub_poll_offset

    _time.sleep(10)  # дать приложению запуститься

    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        logger.warning('[gavibeshub_poller] TELEGRAM_BOT_TOKEN не задан — поллер отключён')
        return

    logger.info('[gavibeshub_poller] Запущен (интервал %ds)', GAVIBESHUB_POLL_INTERVAL)

    while True:
        try:
            with _gavibeshub_poll_lock:
                offset = _gavibeshub_poll_offset

            params = {
                'limit': 100,
                'timeout': 0,
                'allowed_updates': json.dumps(['channel_post', 'message']),
            }
            if offset:
                params['offset'] = offset

            resp = requests.get(
                f'https://api.telegram.org/bot{bot_token}/getUpdates',
                params=params,
                timeout=15
            )
            if resp.status_code != 200:
                logger.warning('[gavibeshub_poller] getUpdates HTTP %d', resp.status_code)
                _time.sleep(GAVIBESHUB_POLL_INTERVAL)
                continue

            updates = resp.json().get('result', [])
            logger.debug('[gavibeshub_poller] poll OK, updates=%d, offset=%d', len(updates), offset or 0)

            for upd in updates:
                upd_id = upd.get('update_id', 0)
                with _gavibeshub_poll_lock:
                    if upd_id >= _gavibeshub_poll_offset:
                        _gavibeshub_poll_offset = upd_id + 1

                # Передаём обновление в tg_feed (единственный getUpdates — этот поллер)
                try:
                    import tg_feed as _tf_mod
                    _tf_mod.handle_update(upd)
                except Exception:
                    pass

                cp = upd.get('channel_post', {})
                if not cp:
                    continue

                chat_username = cp.get('chat', {}).get('username', '').lower()
                chat_id_val = cp.get('chat', {}).get('id', 0)

                # @banner_vn → только баннеры Вьетнам, без Развлечений
                if chat_username == _BANNER_TG_GROUP:
                    if cp.get('photo'):
                        msg_id_b = cp.get('message_id', 0)
                        photo_list_b = cp.get('photo', [])
                        if photo_list_b and msg_id_b:
                            largest_b = max(photo_list_b, key=lambda p: p.get('file_size', 0))
                            fid_b = largest_b.get('file_id', '')
                            if fid_b:
                                handle_banner_channel_photo(msg_id_b, fid_b)
                    continue

                # Роутинг всех каналов → категория + страна
                _CH_ROUTE = {
                    'vietnamparsing':  ('real_estate',   'vietnam'),
                    'thailandparsing': ('real_estate',   'thailand'),
                    'visarun_vn':      ('visas',         'vietnam'),
                    'paymens_vn':      ('money_exchange','vietnam'),
                    'baykivietnam':    ('transport',     'vietnam'),
                    'gatours_vn':      ('tours',         'vietnam'),
                    'vibeshub_vn':     ('entertainment', 'vietnam'),
                    'restoranvietnam': ('restaurants',   'vietnam'),
                    'obmenvietnam':    ('chat',          'vietnam'),
                    # Агрегаторы-приёмники (HF Space пересылает сюда)
                    'parsing_vn':      ('real_estate',   'vietnam'),
                    'parsing_th':      ('real_estate',   'thailand'),
                    'parsing_in':      ('real_estate',   'india'),
                    'parsing_indo':    ('real_estate',   'indonesia'),
                    'bikeparsing_vn':  ('transport',     'vietnam'),
                    'bikeparsing_th':  ('transport',     'thailand'),
                    'bikeparsing_in':  ('transport',     'india'),
                    'chatparsing_vn':  ('chat',          'vietnam'),
                    'tusaparsing_vn':  ('entertainment', 'vietnam'),
                    'tusaparsing_th':  ('entertainment', 'thailand'),
                    'excursii_th':     ('entertainment', 'thailand'),
                    'tusaparsing_indo':('entertainment', 'indonesia'),
                }
                route = _CH_ROUTE.get(chat_username)
                if not route:
                    continue

                category_r, country_r = route
                _raw_text = cp.get('text', '') or cp.get('caption', '') or ''
                import re as _re_f
                text_r = _re_f.sub(
                    r'\n*Источник:\s*@?\S+\s*\n?Ссылка:\s*https?://t\.me/\S+',
                    '', _raw_text, flags=_re_f.IGNORECASE
                ).strip()
                msg_id = cp.get('message_id', 0)

                # Первоисточник: forward_from_chat → ссылки в entities → ссылки в тексте
                _AGGR = {'parsing_vn', 'parsing_th', 'chatparsing_vn', 'tusaparsing_vn',
                         'baikeparsing_vn', 'baikeparsing_th', 'dom_vn', 'doma_th',
                         'tusaparsing_th', 'excursii_th', 'tusaparsing_indo'}
                orig_username, orig_msg_id = '', 0

                # 1) Telegram forward header
                fwd_chat = cp.get('forward_from_chat', {})
                fwd_username = (fwd_chat.get('username', '') if fwd_chat else '').lower()
                if fwd_username and fwd_username not in _AGGR and not fwd_username.startswith('parsing_'):
                    orig_username = fwd_username
                    orig_msg_id = cp.get('forward_from_message_id', 0)

                # 2) URL entities в тексте или подписи
                if not orig_username:
                    import re as _re
                    _full_text = (cp.get('text', '') or cp.get('caption', '') or '')
                    for _ent in (cp.get('entities') or cp.get('caption_entities') or []):
                        _url = _ent.get('url', '')
                        if not _url:
                            # type=url — извлекаем из текста
                            _off, _len = _ent.get('offset', 0), _ent.get('length', 0)
                            _url = _full_text[_off:_off+_len]
                        _m = _re.search(r't\.me/([^/"?\s]+)/(\d+)', _url)
                        if _m and _m.group(1).lower() not in _AGGR and not _m.group(1).lower().startswith('parsing_'):
                            orig_username, orig_msg_id = _m.group(1), int(_m.group(2))
                            break

                # 3) Прямой поиск t.me-ссылок в тексте
                if not orig_username:
                    import re as _re2
                    _full_text2 = cp.get('text', '') or cp.get('caption', '') or ''
                    for _m2 in _re2.finditer(r'https://t\.me/([^/"?\s]+)/(\d+)', _full_text2):
                        _ch2 = _m2.group(1).lower()
                        if _ch2 not in _AGGR and not _ch2.startswith('parsing_'):
                            orig_username, orig_msg_id = _m2.group(1), int(_m2.group(2))
                            break

                # Fallback: сам агрегирующий канал
                orig_username = orig_username or chat_username
                orig_msg_id = orig_msg_id or msg_id

                # Получаем прямую ссылку на фото через getFile (браузер грузит напрямую с CDN)
                photos_r = []
                photo_list = cp.get('photo', [])
                if photo_list and msg_id:
                    _bot_tok = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
                    for _ph in sorted(photo_list, key=lambda p: p.get('file_size', 0), reverse=True):
                        fid = _ph.get('file_id', '')
                        if not fid:
                            continue
                        with _msg_to_file_id_lock:
                            _msg_to_file_id[(chat_username, msg_id)] = fid
                        # Сначала проверяем кэш file_path
                        with _file_path_cache_lock:
                            _fp = _file_path_cache.get(fid)
                        if not _fp and _bot_tok:
                            try:
                                _gf = requests.get(
                                    f'https://api.telegram.org/bot{_bot_tok}/getFile',
                                    params={'file_id': fid}, timeout=8
                                )
                                if _gf.status_code == 200 and _gf.json().get('ok'):
                                    _fp = _gf.json()['result']['file_path']
                                    with _file_path_cache_lock:
                                        _file_path_cache[fid] = _fp
                            except Exception:
                                pass
                        if _fp and _bot_tok:
                            # Прямая CDN ссылка — браузер загружает напрямую, без серверного скачивания
                            photos_r = [f'https://api.telegram.org/file/bot{_bot_tok}/{_fp}']
                        else:
                            # Fallback: редирект-прокси (работает пока сервер запущен)
                            photos_r = [f'/api/tgphoto/{fid}']
                        break

                if not text_r and not photos_r:
                    continue

                # Недвижимость — только с фото
                if category_r == 'real_estate' and not photos_r:
                    continue

                try:
                    from vietnamparsing_parser import atomic_add_listing
                    from datetime import datetime as _dt, timezone as _tz
                    title_r = text_r[:120].replace('\n', ' ').strip() if text_r else f'Пост {orig_msg_id}'
                    # Определяем город из текста
                    _txt_low = (text_r or '').lower()
                    _vn_cities = {
                        'nhatrang':  ['нячанг', 'nha trang', 'nhatrang', 'камрань', 'cam ranh',
                                      'bắc nha trang', 'khanh hoa', 'bai dai', 'hon tre', 'vinh nguyen'],
                        'hochiminh': ['хошимин', 'сайгон', 'saigon', 'ho chi minh', 'hcm',
                                      'binh thanh', 'thu duc', 'tan binh', 'go vap'],
                        'danang':    ['дананг', 'da nang', 'danang', 'da-nang', 'son tra', 'sơn trà',
                                      'lien chieu', 'my khe', 'bac my an', 'hoa khanh',
                                      'hai chau', 'thanh khe', 'ngu hanh son', 'nam o'],
                        'hanoi':     ['ханой', 'hanoi', 'ha noi', 'tay ho', 'hoan kiem', 'ba dinh'],
                        'phuquoc':   ['фукуок', 'phu quoc', 'phuquoc', 'duong dong', 'long beach'],
                        'dalat':     ['далат', 'da lat', 'dalat', 'lam dong'],
                        'muine':     ['муйне', 'mui ne', 'phan thiet', 'фантьет'],
                        'hoian':     ['хойан', 'hoi an', 'hội an'],
                    }
                    _th_cities = {
                        'pattaya':  ['паттайя', 'pattaya', 'wongamat', 'jomtien'],
                        'phuket':   ['пхукет', 'phuket', 'rawai', 'patong', 'karon', 'kata'],
                        'bangkok':  ['бангкок', 'bangkok'],
                        'samui':    ['самуи', 'samui', 'ko samui'],
                        'chiangmai':['чиангмай', 'chiang mai'],
                    }
                    _in_cities = {
                        'goa':       ['гоа', 'goa', 'arambol', 'арамболь', 'anjuna', 'анджуна', 'calangute', 'morjim', 'морджим', 'baga', 'panjim', 'siolim'],
                        'mumbai':    ['мумбай', 'mumbai', 'bombay'],
                        'delhi':     ['дели', 'delhi', 'new delhi'],
                        'bangalore': ['бангалор', 'bangalore', 'bengaluru'],
                    }
                    _indo_cities = {
                        'bali':      ['бали', 'bali', 'canggu', 'семиньяк', 'seminyak', 'ubud', 'убуд', 'kuta', 'кута', 'sanur', 'nusa dua', 'uluwatu'],
                        'jakarta':   ['джакарта', 'jakarta'],
                        'lombok':    ['ломбок', 'lombok'],
                    }
                    _country_city_default = {
                        'vietnam': 'Вьетнам', 'thailand': 'Таиланд',
                        'india': 'Индия', 'indonesia': 'Индонезия',
                    }
                    _city_map_r = {'vietnam': _vn_cities, 'thailand': _th_cities,
                                   'india': _in_cities, 'indonesia': _indo_cities}.get(country_r, {})
                    _re_city = ''
                    for _cs, _kws in _city_map_r.items():
                        if any(_kw in _txt_low for _kw in _kws):
                            _re_city = _cs
                            break
                    # Город: сначала из названия канала, затем из текста, затем страна
                    try:
                        from bot_channel_parser import city_from_channel as _city_from_ch
                        _city_from_name = _city_from_ch(orig_username) or _city_from_ch(chat_username)
                    except Exception:
                        _city_from_name = ''
                    if _city_from_name:
                        _city_display = _city_from_name
                    elif _re_city:
                        # Слаг → русское название
                        _slug_to_ru = {
                            'nhatrang':'Нячанг','danang':'Дананг','hochiminh':'Хошимин',
                            'hanoi':'Ханой','phuquoc':'Фукуок','dalat':'Далат',
                            'muine':'Муйне','hoian':'Хойан','camranh':'Камрань',
                            'pattaya':'Паттайя','phuket':'Пхукет','bangkok':'Бангкок',
                            'samui':'Самуи','chiangmai':'Чиангмай',
                            'goa':'Гоа','mumbai':'Мумбаи','delhi':'Дели','bangalore':'Бангалор',
                            'bali':'Бали','jakarta':'Джакарта','lombok':'Ломбок',
                        }
                        _city_display = _slug_to_ru.get(_re_city,
                                         _country_city_default.get(country_r, country_r.capitalize()))
                    else:
                        _city_display = _country_city_default.get(country_r, country_r.capitalize())
                    item_r = {
                        'id': f'{orig_username}_{orig_msg_id}',
                        'title': title_r,
                        'description': text_r,
                        'text': text_r,
                        'price': 0,
                        'price_display': '',
                        'city': _city_display,
                        'city_ru': _city_display,
                        'realestate_city': _re_city,
                        'date': _dt.now(_tz.utc).isoformat(),
                        'contact': f'@{orig_username}',
                        'contact_name': orig_username,
                        'source_group': orig_username,
                        'source_channel': chat_username,
                        'telegram': f'https://t.me/{orig_username}',
                        'telegram_link': f'https://t.me/{orig_username}/{orig_msg_id}',
                        'image_url': photos_r[0] if photos_r else '',
                        'all_images': photos_r,
                        'photos': photos_r,
                        'has_media': bool(photos_r),
                        'status': 'active',
                        'country': country_r,
                        'message_id': orig_msg_id,
                        'category': category_r,
                    }
                    added = atomic_add_listing(category_r, item_r)
                    if added:
                        data_cache.pop(country_r, None)
                    logger.info('[all_ch_poller] @%s #%d → %s (%s)', chat_username, msg_id, category_r, 'добавлен' if added else 'дубликат')
                except Exception as e:
                    logger.error('[all_ch_poller] Ошибка @%s #%d: %s', chat_username, msg_id, e)

        except Exception as e:
            logger.warning('[gavibeshub_poller] Ошибка поллинга: %s', e)

        _time.sleep(GAVIBESHUB_POLL_INTERVAL)


def _sync_vibeshub_vn_entertainment():
    """Периодически скрейпит t.me/s/vibeshub_vn и добавляет посты с фото в Entertainment Vietnam.
    Задержка 90 сек при старте — чтобы repair/initial_fetch парсера успели завершиться."""
    import time as _t
    _t.sleep(90)  # Ждём пока repair+initial_fetch парсера не перезапишут файл
    while True:
        try:
            from bs4 import BeautifulSoup as _BS
            channel = 'vibeshub_vn'
            base_url = f'https://t.me/s/{channel}'
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

            # Загружаем существующие ID из entertainment
            try:
                with open('listings_vietnam.json', 'r', encoding='utf-8') as _f:
                    _vn = json.load(_f)
                existing_ids = set()
                for it in _vn.get('entertainment', []):
                    if isinstance(it, dict):
                        if it.get('listing_id'): existing_ids.add(it['listing_id'])
                        if it.get('id'): existing_ids.add(it['id'])
            except Exception:
                existing_ids = set()

            added = 0
            before = None
            max_pages = 20
            for _page in range(max_pages):
                params = {}
                if before:
                    params['before'] = before
                try:
                    resp = requests.get(base_url, params=params, headers=headers, timeout=20)
                    if resp.status_code != 200:
                        break
                    soup = _BS(resp.text, 'html.parser')
                    ids_on_page = []
                    for msg_div in soup.select('.tgme_widget_message'):
                        data_post = msg_div.get('data-post', '')
                        if '/' not in data_post:
                            continue
                        try:
                            mid = int(data_post.split('/')[-1])
                        except ValueError:
                            continue
                        ids_on_page.append(mid)
                        has_photo = bool(
                            msg_div.select('.tgme_widget_message_photo_wrap') or
                            msg_div.select('a.tgme_widget_message_photo_wrap')
                        )
                        if not has_photo:
                            continue
                        item_id = f'{channel}_{mid}'
                        if item_id in existing_ids:
                            continue
                        # Строим листинг через og:image
                        try:
                            og_resp = requests.get(
                                f'https://t.me/{channel}/{mid}',
                                headers={'User-Agent': 'TelegramBot (like TwitterBot)'},
                                timeout=10
                            )
                            import re as _re
                            from datetime import datetime, timezone
                            from html import unescape as _ue
                            desc_m = _re.search(r'<meta property="og:description" content="([^"]*)"', og_resp.text)
                            img_m = _re.search(r'<meta property="og:image" content="([^"]+)"', og_resp.text)
                            text = _ue(desc_m.group(1)) if desc_m else ''
                            img_url = img_m.group(1) if img_m else ''
                            if not text or 'You can view and join' in text:
                                _t.sleep(0.3)
                                continue
                            lines = [l.strip() for l in text.splitlines() if l.strip()]
                            title = lines[0][:120] if lines else 'Развлечение'
                            description = '\n'.join(lines[1:]) if len(lines) > 1 else text
                            city = 'Вьетнам'
                            tl = text.lower()
                            if any(k in tl for k in ['нячанг', 'nha trang', 'nhatrang']): city = 'Нячанг'
                            elif any(k in tl for k in ['дананг', 'da nang', 'danang']): city = 'Дананг'
                            elif any(k in tl for k in ['хошимин', 'ho chi minh', 'saigon', 'сайгон']): city = 'Хошимин'
                            elif any(k in tl for k in ['ханой', 'ha noi', 'hanoi']): city = 'Ханой'
                            elif any(k in tl for k in ['фукуок', 'phu quoc']): city = 'Фукуок'
                            elif any(k in tl for k in ['хойан', 'hoi an']): city = 'Хойан'
                            elif any(k in tl for k in ['далат', 'da lat', 'dalat']): city = 'Далат'
                            now = datetime.now(timezone.utc).isoformat()
                            item = {
                                'id': item_id,
                                'listing_id': item_id,
                                'title': title,
                                'description': description,
                                'text': text,
                                'price': 0,
                                'price_display': '',
                                'city': city,
                                'city_ru': city,
                                'date': now,
                                'contact': f'@{channel}',
                                'contact_name': channel,
                                'source_group': channel,
                                'source_channel': channel,
                                'telegram': f'https://t.me/{channel}',
                                'telegram_link': f'https://t.me/{channel}/{mid}',
                                'image_url': f'/tg_img/{channel}/{mid}' if img_url else '',
                                'all_images': [f'/tg_img/{channel}/{mid}'] if img_url else [],
                                'photos': [f'/tg_img/{channel}/{mid}'] if img_url else [],
                                'status': 'active',
                                'country': 'vietnam',
                                'message_id': mid,
                                'has_media': bool(img_url),
                                'category': 'entertainment',
                                'listing_type': 'entertainment',
                            }
                            # Используем atomic_add_listing из парсера (тот же _listings_lock что и parser)
                            try:
                                from vietnamparsing_parser import atomic_add_listing as _aal
                                _added_ok = _aal('entertainment', item)
                            except Exception as _ae:
                                logger.warning(f'[vibeshub_vn_sync] atomic_add_listing ошибка: {_ae}')
                                _added_ok = False
                            if _added_ok:
                                # Сбрасываем кеш app.py чтобы API сразу отдавал новые данные
                                data_cache.pop('vietnam', None)
                                data_cache.pop('all', None)
                                existing_ids.add(item_id)
                                added += 1
                                logger.info(f'[vibeshub_vn_sync] + пост #{mid}: {title[:50]}')
                        except Exception as _e:
                            logger.warning(f'[vibeshub_vn_sync] Ошибка поста #{mid}: {_e}')
                        _t.sleep(0.5)
                    if not ids_on_page:
                        break
                    before = min(ids_on_page)
                    if before <= 1:
                        break
                    _t.sleep(1)
                except Exception as e:
                    logger.warning(f'[vibeshub_vn_sync] Ошибка страницы: {e}')
                    break
            logger.info(f'[vibeshub_vn_sync] Завершено: добавлено {added} постов из @{channel} в entertainment')
        except Exception as e:
            logger.error(f'[vibeshub_vn_sync] Критическая ошибка: {e}')
        _t.sleep(600)  # Повторяем каждые 10 минут

threading.Thread(target=_gavibeshub_poller, daemon=True, name='GAvibeshubPoller').start()
threading.Thread(target=_sync_vibeshub_vn_entertainment, daemon=True, name='VibeshubVnSync').start()
logger.info('GAvibeshub background poller started (every %ds)', GAVIBESHUB_POLL_INTERVAL)

# ─── Периодический скрейпер всех каналов (t.me/s/) ────────────────────────
# Опрашиваются каждые ALL_CHANNELS_SCRAPE_INTERVAL секунд (5 мин)
_PERIODIC_SCRAPE_CHANNELS = [
    # Недвижимость
    ('parsing_vn',     'real_estate',   'listings_vietnam.json',   'vietnam'),
    ('parsing_th',     'real_estate',   'listings_thailand.json',  'thailand'),
    ('parsing_in',     'real_estate',   'listings_india.json',     'india'),
    ('parsing_indo',   'real_estate',   'listings_indonesia.json', 'indonesia'),
    # Транспорт / байки
    ('bikeparsing_vn', 'transport',     'listings_vietnam.json',   'vietnam'),
    ('bikeparsing_th', 'transport',     'listings_thailand.json',  'thailand'),
    ('bikeparsing_in', 'transport',     'listings_india.json',     'india'),
    # Развлечения
    ('tusaparsing_vn', 'entertainment', 'listings_vietnam.json',   'vietnam'),
    # banner_vn — пропускаем, баннеры обновляются отдельно
]
_CHAT_SCRAPE_CHANNELS = [
    ('obmenvietnam', 'chat', 'listings_vietnam.json', 'vietnam'),
]
ALL_CHANNELS_SCRAPE_INTERVAL = 300  # 5 минут
CHAT_SCRAPE_INTERVAL = 30           # 30 секунд


def _scrape_channel_latest(channel, category, target_file, country):
    """Скрейпит последнюю страницу канала через t.me/s/, добавляет новые посты."""
    import time as _t2
    try:
        from bot_channel_parser import scrape_channel_page, make_listing, detect_logo_fingerprints
        scraped = scrape_channel_page(channel)
        if not scraped:
            return 0
        try:
            with open(target_file, 'r', encoding='utf-8') as _ff:
                file_data = json.load(_ff)
        except Exception:
            file_data = {}
        existing = file_data.get(category, [])
        existing_ids = {item['id'] for item in existing}
        logo_fps = detect_logo_fingerprints(scraped)
        _SKIP = {'channel created', 'канал создан', 'channel photo updated', 'telegram'}
        added = 0
        for msg_id in sorted(scraped.keys(), reverse=True):
            item_id = f'{channel}_{msg_id}'
            if item_id in existing_ids:
                continue
            post = scraped[msg_id]
            raw_title = (post.get('text', '') or '')[:40].lower().strip()
            if not raw_title or raw_title in _SKIP:
                continue
            new_item = make_listing(channel, msg_id, post, category, country, logo_fps=logo_fps)
            # Для туров Вьетнама — добавляем source_group
            if category == 'tours' and country == 'vietnam':
                new_item['source_group'] = 'GAtours_vn'
            existing.insert(0, new_item)
            existing_ids.add(item_id)
            added += 1
        if added > 0:
            file_data[category] = existing
            _tmp = target_file + '.tmp'
            with open(_tmp, 'w', encoding='utf-8') as _ff:
                json.dump(file_data, _ff, ensure_ascii=False, separators=(',', ':'))
            os.replace(_tmp, target_file)
            data_cache.pop(country, None)
            logger.info('[periodic_scraper] @%s +%d → %s', channel, added, category)
            # Немедленно обновляем баннер если добавлены entertainment-посты с фото
            if category == 'entertainment' and country in ('thailand', 'indonesia'):
                try:
                    _cfg = load_banner_config()
                    _imgs = [
                        it.get('image_url', '') or it.get('image', '')
                        for it in file_data.get('entertainment', [])
                        if (it.get('image_url', '') or it.get('image', '')).startswith('http')
                    ]
                    _imgs = list(dict.fromkeys(_imgs))  # уникальные, порядок сохранён
                    if _imgs:
                        _cfg.setdefault(country, {})['web'] = _imgs
                        _cfg[country]['mobile'] = _imgs
                        save_banner_config(_cfg)
                        logger.info('[periodic_scraper] Баннер %s обновлён: %d фото', country, len(_imgs))
                except Exception as _be:
                    logger.warning('[periodic_scraper] Banner update error: %s', _be)
        return added
    except Exception as _se:
        logger.warning('[periodic_scraper] @%s error: %s', channel, _se)
        return 0


def _all_channels_periodic_scraper():
    """Каждые 5 минут скрейпит последнюю страницу всех каналов."""
    import time as _t2
    _t2.sleep(90)  # дать приложению запуститься
    logger.info('[periodic_scraper] Запущен (все каналы каждые %ds)', ALL_CHANNELS_SCRAPE_INTERVAL)
    while True:
        for _ch, _cat, _tf, _co in _PERIODIC_SCRAPE_CHANNELS:
            _scrape_channel_latest(_ch, _cat, _tf, _co)
            _t2.sleep(3)
        _t2.sleep(ALL_CHANNELS_SCRAPE_INTERVAL)


def _chat_periodic_scraper():
    """Каждые 30 секунд обновляет чат-каналы."""
    import time as _t2
    _t2.sleep(45)
    logger.info('[chat_scraper] Запущен (чаты каждые %ds)', CHAT_SCRAPE_INTERVAL)
    while True:
        for _ch, _cat, _tf, _co in _CHAT_SCRAPE_CHANNELS:
            _scrape_channel_latest(_ch, _cat, _tf, _co)
        _t2.sleep(CHAT_SCRAPE_INTERVAL)


threading.Thread(target=_all_channels_periodic_scraper, daemon=True, name='AllChannelsScraper').start()
threading.Thread(target=_chat_periodic_scraper, daemon=True, name='ChatScraper').start()
logger.info('[periodic_scraper] Все каналы — каждые %ds, чаты — каждые %ds',
            ALL_CHANNELS_SCRAPE_INTERVAL, CHAT_SCRAPE_INTERVAL)


# ─── Авто-синхронизация данных с HF Space ───────────────────────────────────
HF_SYNC_REPO = 'poweramanita/goldantelopeasia.com'
HF_SYNC_INTERVAL = 600  # каждые 10 минут

_hf_sync_files = [
    'listings_vietnam.json',
    'listings_thailand.json',
    'listings_india.json',
    'listings_indonesia.json',
    'listings_data.json',
    'tg_feed_posts.json',
    'banner_config.json',
    'banner_data.json',
    'analytics.json',
    'file_id_index.json',
    'tg_file_paths_cache.json',
    'groups_stats_vietnam.json',
    'groups_stats_thailand.json',
    'bot_sources.json',
]

_hf_sync_last_mtime = {}


def _hf_auto_sync():
    """Фоновый поток: каждые 10 минут пушит изменённые JSON-файлы в HF Space."""
    import time as _t3
    _t3.sleep(60)  # дать приложению запуститься
    hf_token = os.environ.get('HF_TOKEN', '').strip()
    if not hf_token:
        logger.warning('[hf_sync] HF_TOKEN не задан — синхронизация отключена')
        return
    try:
        from huggingface_hub import HfApi as _HfApi
        _hf_api = _HfApi(token=hf_token)
        logger.info('[hf_sync] Запущен (репо: %s, интервал: %ds)', HF_SYNC_REPO, HF_SYNC_INTERVAL)
    except Exception as _e:
        logger.warning('[hf_sync] Ошибка инициализации HfApi: %s', _e)
        return

    while True:
        changed = []
        for fname in _hf_sync_files:
            if not os.path.exists(fname):
                continue
            try:
                mtime = os.path.getmtime(fname)
                if _hf_sync_last_mtime.get(fname) != mtime:
                    changed.append(fname)
            except Exception:
                pass

        if changed:
            logger.info('[hf_sync] Изменено %d файлов, пушим в HF...', len(changed))
            pushed = 0
            for fname in changed:
                try:
                    _hf_api.upload_file(
                        path_or_fileobj=fname,
                        path_in_repo=fname,
                        repo_id=HF_SYNC_REPO,
                        repo_type='space',
                        commit_message=f'auto-sync: {fname}',
                    )
                    _hf_sync_last_mtime[fname] = os.path.getmtime(fname)
                    pushed += 1
                    logger.info('[hf_sync] ✓ %s', fname)
                except Exception as _ue:
                    logger.warning('[hf_sync] ✗ %s: %s', fname, _ue)
            logger.info('[hf_sync] Синхронизация завершена: %d/%d файлов', pushed, len(changed))
        else:
            logger.debug('[hf_sync] Нет изменений')

        _t3.sleep(HF_SYNC_INTERVAL)


threading.Thread(target=_hf_auto_sync, daemon=True, name='HfAutoSync').start()
logger.info('[hf_sync] Авто-синхронизация с HF Space запущена (каждые %ds)', HF_SYNC_INTERVAL)


PARTYHUNT_API_BASE = 'https://api.anbocas.com'
PARTYHUNT_EVENTS_EP = '/webapp/v1/events'
PARTYHUNT_SITE = 'https://tickets.partyhunt.com/events'
PARTYHUNT_POLL_INTERVAL = 600
_partyhunt_sent_ids = set()

def _partyhunt_poller():
    """Background poller: fetches events from PartyHunt API and adds to India entertainment."""
    import time as _time
    _time.sleep(15)
    logger.info('[partyhunt_poller] Started (interval %ds)', PARTYHUNT_POLL_INTERVAL)

    india_file = 'listings_india.json'
    _ph_lock = threading.Lock()

    while True:
        try:
            all_events = []
            page = 1
            while True:
                try:
                    r = requests.get(f'{PARTYHUNT_API_BASE}{PARTYHUNT_EVENTS_EP}', params={
                        'page': page,
                        'sortField': 'start_date',
                        'sortDirection': 'asc',
                    }, headers={'Accept': 'application/json', 'User-Agent': 'GoldAntelope/1.0'}, timeout=15)
                    if r.status_code != 200:
                        break
                    data = r.json().get('data', {})
                    events = data.get('data', [])
                    if not events:
                        break
                    all_events.extend(events)
                    if page >= data.get('last_page', 1):
                        break
                    page += 1
                    _time.sleep(0.5)
                except Exception as e:
                    logger.warning('[partyhunt_poller] Fetch error page %d: %s', page, e)
                    break

            new_count = 0
            for ev in all_events:
                ev_id = ev.get('id', '')
                if not ev_id or ev_id in _partyhunt_sent_ids:
                    continue

                name = ev.get('name', 'Event')
                venue = ev.get('venue', '')
                location = ev.get('location', '')
                city = ev.get('city', 'Goa')
                start = ev.get('start_date', '')
                end = ev.get('end_date', '')
                slug = ev.get('slug', '')
                price = ev.get('start_price', '')
                image_url = ev.get('image_url', '')
                category_name = ''
                cat_data = ev.get('category')
                if isinstance(cat_data, dict):
                    category_name = cat_data.get('name', '')

                date_str = ''
                if start:
                    try:
                        from datetime import datetime as _dt
                        dt = _dt.strptime(start, '%Y-%m-%d %H:%M:%S')
                        date_str = dt.strftime('%d %b %Y, %H:%M')
                    except:
                        date_str = start

                desc_parts = []
                if date_str:
                    desc_parts.append(f'📅 {date_str}')
                if venue:
                    desc_parts.append(f'📍 {venue}')
                if location:
                    desc_parts.append(f'🗺 {location}')
                if category_name:
                    desc_parts.append(f'🏷 {category_name}')
                if slug:
                    desc_parts.append(f'🎟 {PARTYHUNT_SITE}/{slug}')
                description = '\n'.join(desc_parts)

                price_display = ''
                price_val = ''
                if price and str(price) != '0' and str(price) != '0.00':
                    price_val = str(price)
                    price_display = f'from ₹{price}'

                photos = []
                if image_url:
                    photos.append(image_url)

                city_lower = (city or '').strip().lower()
                location_lower = (location or '').lower()
                _INDIA_CITY_MAP = {
                    'goa': 'goa', 'anjuna': 'goa', 'arambol': 'goa', 'vagator': 'goa',
                    'palolem': 'goa', 'calangute': 'goa', 'baga': 'goa', 'panjim': 'goa',
                    'mapusa': 'goa', 'chapora': 'goa', 'morjim': 'goa', 'mandrem': 'goa',
                    'mumbai': 'mumbai', 'delhi': 'delhi', 'bangalore': 'bangalore',
                    'bengaluru': 'bangalore', 'chennai': 'chennai', 'kolkata': 'kolkata',
                    'hyderabad': 'hyderabad', 'pune': 'pune', 'jaipur': 'jaipur',
                    'kasol': 'kasol', 'manali': 'manali', 'rishikesh': 'rishikesh',
                    'kerala': 'kerala', 'gurugram': 'delhi', 'noida': 'delhi',
                    'vashist': 'manali', 'bir': 'bir',
                }
                resolved_city = _INDIA_CITY_MAP.get(city_lower, '')
                if not resolved_city:
                    for key, val in _INDIA_CITY_MAP.items():
                        if key in location_lower:
                            resolved_city = val
                            break
                if not resolved_city:
                    resolved_city = city_lower or 'india'

                ticket_url = f'{PARTYHUNT_SITE}/{slug}' if slug else ''

                item = {
                    'id': f'partyhunt_{ev_id}',
                    'title': name,
                    'description': description,
                    'photos': photos,
                    'contact': '@partyhuntgoa',
                    'source_group': 'partyhuntgoa',
                    'date': start or datetime.now(timezone.utc).isoformat(),
                    'city': city or resolved_city.title(),
                    'realestate_city': resolved_city,
                    'country': 'india',
                    'telegram_link': ticket_url,
                }
                if price_val:
                    item['price'] = price_val
                    item['price_display'] = price_display

                # Фильтр 14 дней: не добавляем события за пределами окна
                _item_date_str = start  # используем сырую дату из API
                try:
                    import re as _re
                    _ds = str(_item_date_str).strip()
                    # Убираем timezone offset вида +05:30 или -07:00 → заменяем на UTC
                    _ds_clean = _re.sub(r'[+-]\d{2}:\d{2}$', '', _ds).replace(' ', 'T')
                    _item_dt = datetime.fromisoformat(_ds_clean).replace(tzinfo=timezone.utc)
                    _now_ph = datetime.now(timezone.utc)
                    if _item_dt < _now_ph or _item_dt > _now_ph + timedelta(days=14):
                        _partyhunt_sent_ids.add(ev_id)
                        logger.info('[partyhunt_poller] Skipped (outside 14d): %s (%s)', name, _item_date_str)
                        continue
                except Exception as _fe:
                    logger.debug('[partyhunt_poller] Date parse error for %s: %s', name, _fe)

                with _ph_lock:
                    try:
                        with open(india_file, 'r', encoding='utf-8') as f:
                            india_data = json.load(f)
                    except Exception:
                        india_data = {}
                    if 'entertainment' not in india_data:
                        india_data['entertainment'] = []

                    existing_ids = set()
                    for cat_items in india_data.values():
                        if isinstance(cat_items, list):
                            for it in cat_items:
                                if isinstance(it, dict) and 'id' in it:
                                    existing_ids.add(it['id'])

                    if item['id'] in existing_ids:
                        _partyhunt_sent_ids.add(ev_id)
                        continue

                    india_data['entertainment'].insert(0, item)
                    try:
                        tmp = india_file + '.tmp'
                        with open(tmp, 'w', encoding='utf-8') as f:
                            json.dump(india_data, f, ensure_ascii=False, indent=2)
                        os.replace(tmp, india_file)
                        _partyhunt_sent_ids.add(ev_id)
                        new_count += 1
                        logger.info('[partyhunt_poller] Added: %s (₹%s)', name, price_val or '?')
                    except Exception as e:
                        logger.error('[partyhunt_poller] Save error: %s', e)

            if new_count:
                logger.info('[partyhunt_poller] Batch done: %d new events added to India entertainment', new_count)
                if 'india' in data_cache:
                    del data_cache['india']
            else:
                logger.debug('[partyhunt_poller] No new events (total checked: %d)', len(all_events))

            try:
                from datetime import datetime as _dt, timezone as _tz, timedelta as _td
                now = _dt.now(_tz.utc)
                _EVENT_DURATION = _td(days=4)
                with _ph_lock:
                    try:
                        with open(india_file, 'r', encoding='utf-8') as f:
                            india_data = json.load(f)
                    except Exception:
                        india_data = {}
                    ent_list = india_data.get('entertainment', [])
                    before_count = len(ent_list)
                    cleaned = []
                    for item in ent_list:
                        date_str = item.get('date', '')
                        if date_str and item.get('id', '').startswith('partyhunt_'):
                            try:
                                if 'T' in str(date_str):
                                    ev_dt = _dt.fromisoformat(str(date_str).replace('Z', '+00:00'))
                                else:
                                    ev_dt = _dt.strptime(str(date_str), '%Y-%m-%d %H:%M:%S').replace(tzinfo=_tz.utc)
                                ev_end = ev_dt + _EVENT_DURATION
                                if ev_end < now:
                                    continue
                            except Exception:
                                pass
                        cleaned.append(item)
                    removed = before_count - len(cleaned)
                    if removed > 0:
                        india_data['entertainment'] = cleaned
                        tmp = india_file + '.tmp'
                        with open(tmp, 'w', encoding='utf-8') as f:
                            json.dump(india_data, f, ensure_ascii=False, indent=2)
                        os.replace(tmp, india_file)
                        if 'india' in data_cache:
                            del data_cache['india']
                        logger.info('[partyhunt_poller] Auto-cleanup: removed %d expired events (%d remaining)', removed, len(cleaned))
            except Exception as e:
                logger.warning('[partyhunt_poller] Cleanup error: %s', e)

            try:
                from datetime import datetime as _dt, timezone as _tz, timedelta as _td
                now = _dt.now(_tz.utc)
                _EVENT_DURATION = _td(days=4)
                sorted_events = []
                for ev in all_events:
                    start = ev.get('start_date', '')
                    end = ev.get('end_date', '')
                    img = ev.get('image_url', '')
                    if not start or not img:
                        continue
                    try:
                        ev_start = _dt.strptime(start, '%Y-%m-%d %H:%M:%S').replace(tzinfo=_tz.utc)
                        if end:
                            ev_end = _dt.strptime(end, '%Y-%m-%d %H:%M:%S').replace(tzinfo=_tz.utc)
                        else:
                            ev_end = ev_start + _EVENT_DURATION
                        if ev_end >= now:
                            sorted_events.append((ev_start, img))
                    except Exception:
                        pass
                sorted_events.sort(key=lambda x: x[0])
                upcoming_images = [img for _, img in sorted_events]

                try:
                    banner_cfg = load_banner_config()
                    if 'india' not in banner_cfg:
                        banner_cfg['india'] = {'web': [], 'mobile': []}
                    elif isinstance(banner_cfg['india'], list):
                        banner_cfg['india'] = {'web': banner_cfg['india'], 'mobile': []}
                    banner_cfg['india']['mobile'] = upcoming_images
                    banner_cfg['india']['web'] = upcoming_images
                    save_banner_config(banner_cfg)
                    logger.info('[partyhunt_poller] India banners updated: %d images (only future/active events from PartyHunt)', len(upcoming_images))
                except Exception as e:
                    logger.warning('[partyhunt_poller] Banner update error: %s', e)
            except Exception as e:
                logger.warning('[partyhunt_poller] Banner build error: %s', e)

        except Exception as e:
            logger.warning('[partyhunt_poller] Error: %s', e)

        _time.sleep(PARTYHUNT_POLL_INTERVAL)


threading.Thread(target=_partyhunt_poller, daemon=True, name='PartyHuntPoller').start()
logger.info('PartyHunt Goa poller started (every %ds)', PARTYHUNT_POLL_INTERVAL)


def _sync_entertainment_banners_th_indo():
    """Синхронизирует баннеры Тайланда и Индонезии из раздела Развлечения (как Индия)."""
    import time as _time
    _INTERVAL = 900  # каждые 15 минут
    while True:
        try:
            cfg = load_banner_config()
            changed = False
            for country, fname in [('thailand', 'listings_thailand.json'), ('indonesia', 'listings_indonesia.json')]:
                try:
                    with open(fname, 'r', encoding='utf-8') as _f:
                        _data = json.load(_f)
                except Exception:
                    continue
                ent_items = _data.get('entertainment', [])
                images = []
                for item in ent_items:
                    img = item.get('image_url', '') or item.get('image', '') or ''
                    if img and img.startswith('http') and img not in images:
                        images.append(img)
                if country not in cfg:
                    cfg[country] = {'web': [], 'mobile': []}
                if cfg[country].get('web') != images or cfg[country].get('mobile') != images:
                    cfg[country]['web'] = images
                    cfg[country]['mobile'] = images
                    changed = True
                    logger.info('[ent_banner_sync] %s: %d баннеров из entertainment', country, len(images))
            if changed:
                save_banner_config(cfg)
        except Exception as e:
            logger.warning('[ent_banner_sync] Ошибка: %s', e)
        _time.sleep(_INTERVAL)


threading.Thread(target=_sync_entertainment_banners_th_indo, daemon=True, name='EntBannerSyncThIndo').start()
logger.info('Entertainment banner sync for Thailand/Indonesia started')


def _bot_api_download(file_id: str, bot_token: str) -> bytes | None:
    """Скачивает файл через Bot API напрямую с api.telegram.org (без CDN)."""
    try:
        r = requests.get(
            f'https://api.telegram.org/bot{bot_token}/getFile',
            params={'file_id': file_id},
            timeout=10
        )
        j = r.json()
        if not j.get('ok'):
            logger.warning(f'getFile failed for {file_id[:40]}: {j.get("description")}')
            return None
        file_path = j['result']['file_path']
        img = requests.get(
            f'https://api.telegram.org/file/bot{bot_token}/{file_path}',
            timeout=20
        )
        if img.status_code == 200 and img.content:
            return img.content
    except Exception as e:
        logger.warning(f'_bot_api_download error: {e}')
    return None


# Нейтральные короткие коды каналов — скрывают имя канала в URL фото
_CHANNEL_ALIAS = {
    'parsing_vn':       'v1',
    'parsing_th':       't1',
    'parsing_in':       'i1',
    'parsing_indo':     'd1',
    'bikeparsing_vn':   'v2',
    'bikeparsing_th':   't2',
    'bikeparsing_in':   'i2',
    'bikeparsing_indo': 'd2',
    'tusaparsing_vn':   'v3',
    'tusaparsing_th':   't3',
    'banner_vn':        'v4',
    'chatparsing_vn':   'v5',
    'restoranvietnam':  'v6',
    'dom_vn':           'v7',
    'doma_th':          't4',
    'media_vn':         'v8',
    'excursii_vn':      'v9',
    'excursii_th':      't5',
    'restoranparsing_all': 'a1',
}
_ALIAS_CHANNEL = {v: k for k, v in _CHANNEL_ALIAS.items()}


@app.route('/g/<code>/<int:post_id>')
def g_photo_proxy(code, post_id):
    """Нейтральный прокси фото: /g/<alias>/<msg_id> → скрывает имя канала."""
    channel = _ALIAS_CHANNEL.get(code, code)
    return tg_photo_proxy(channel, post_id)


@app.route('/gg/<code>/<int:post_id>/<int:idx>')
def g_photo_group_proxy(code, post_id, idx):
    """Нейтральный прокси группового фото: /gg/<alias>/<msg_id>/<idx>."""
    channel = _ALIAS_CHANNEL.get(code, code)
    return tg_photo_group_proxy(channel, post_id, idx)


@app.route('/tg_img/<channel>/<int:post_id>')
def tg_photo_proxy(channel, post_id):
    """302-редирект на фото через Bot API getFile.
    Сервер только резолвит URL — браузер скачивает напрямую с api.telegram.org.
    Никакого серверного скачивания байт."""
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()

    # 1. Bot API: file_id из индекса → getFile → прямая ссылка api.telegram.org
    if bot_token:
        with _msg_to_file_id_lock:
            file_id = _msg_to_file_id.get((channel, post_id))
        if file_id:
            # Проверяем кэш file_path
            with _file_path_cache_lock:
                fp = _file_path_cache.get(file_id)
            if not fp:
                try:
                    r = requests.get(
                        f'https://api.telegram.org/bot{bot_token}/getFile',
                        params={'file_id': file_id}, timeout=8
                    )
                    if r.status_code == 200 and r.json().get('ok'):
                        fp = r.json()['result']['file_path']
                        with _file_path_cache_lock:
                            _file_path_cache[file_id] = fp
                except Exception as e:
                    logger.debug(f'tg_photo_proxy getFile error {channel}/{post_id}: {e}')
            if fp:
                direct_url = f'https://api.telegram.org/file/bot{bot_token}/{fp}'
                logger.debug(f'tg_photo_proxy: Bot API redirect {channel}/{post_id}')
                return redirect(direct_url, code=302)

    # 2. Fallback: og:image — браузер получает 302 на свежий CDN URL
    try:
        og_resp = requests.get(
            f'https://t.me/{channel}/{post_id}',
            headers={'User-Agent': 'TelegramBot (like TwitterBot)'},
            timeout=8
        )
        if og_resp.status_code == 200:
            img_m = re.search(r'<meta property="og:image" content="([^"]+)"', og_resp.text)
            if img_m:
                logger.debug(f'tg_photo_proxy: og:image redirect {channel}/{post_id}')
                return redirect(img_m.group(1), code=302)
    except Exception as e:
        logger.debug(f'tg_photo_proxy og:image error {channel}/{post_id}: {e}')

    return Response(status=404)

@app.route('/tg_img_grp/<channel>/<int:post_id>/<int:idx>')
def tg_photo_group_proxy(channel, post_id, idx):
    """302-редирект на idx-й снимок из медиагруппы.
    Для idx=0: используем Bot API (file_id из индекса).
    Для idx>0: скрапим t.me/s для получения актуального CDN URL → 302 redirect.
    Сервер не скачивает байты — браузер грузит напрямую."""
    # idx=0 → главное фото через Bot API
    if idx == 0:
        return tg_photo_proxy(channel, post_id)

    # idx>0 → получаем список URL из t.me/s и редиректим
    try:
        from vietnamparsing_parser import _scrape_cdn_photos_for_post
        cdn_urls = _scrape_cdn_photos_for_post(channel, post_id)
        if cdn_urls and idx < len(cdn_urls):
            logger.debug(f'tg_photo_group_proxy: cdn redirect {channel}/{post_id}/{idx}')
            return redirect(cdn_urls[idx], code=302)
    except Exception as e:
        logger.debug(f'tg_photo_group_proxy scrape error {channel}/{post_id}/{idx}: {e}')

    return Response(status=404)


# ============ УПРАВЛЕНИЕ ГОРОДАМИ ============

def load_cities_config(country, category):
    """Загрузить конфигурацию городов для категории"""
    cities_file = f'cities_{country}_{category}.json'
    if os.path.exists(cities_file):
        with open(cities_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_cities_config(country, category, cities):
    """Сохранить конфигурацию городов"""
    cities_file = f'cities_{country}_{category}.json'
    with open(cities_file, 'w', encoding='utf-8') as f:
        json.dump(cities, f, ensure_ascii=False, indent=2)

@app.route('/api/admin/cities', methods=['GET', 'POST'])
def get_cities():
    """Получить города для категории (требует авторизации)"""
    # Для GET запросов проверяем пароль в параметрах
    if request.method == 'GET':
        password = request.args.get('password', '')
        country = request.args.get('country', 'vietnam')
    else:
        password = request.json.get('password', '')
        country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    
    category = request.args.get('category', 'restaurants') if request.method == 'GET' else request.json.get('category', 'restaurants')
    cities = load_cities_config(country, category)
    return jsonify({'country': country, 'category': category, 'cities': cities})

@app.route('/api/admin/add-city', methods=['POST'])
def add_city():
    """Добавить город"""
    password = request.form.get('password', '')
    country = request.form.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.form.get('category', 'restaurants')
    name = request.form.get('name', '').strip()
    
    if not name:
        return jsonify({'error': 'City name required'}), 400
    
    cities = load_cities_config(country, category)
    
    # Генерируем ID
    city_id = f"{country}_{category}_{len(cities)}_{int(time.time())}"
    
    # Обработка фото
    image_path = '/static/icons/placeholder.png'
    photo = request.files.get('photo')
    if photo and photo.filename:
        import base64
        file_data = photo.read()
        ext = photo.filename.rsplit('.', 1)[-1].lower() if '.' in photo.filename else 'jpg'
        
        # Сохраняем в static/icons/cities/
        os.makedirs('static/icons/cities', exist_ok=True)
        filename = f"{city_id}.{ext}"
        filepath = f"static/icons/cities/{filename}"
        with open(filepath, 'wb') as f:
            f.write(file_data)
        image_path = f"/static/icons/cities/{filename}"
    
    new_city = {
        'id': city_id,
        'name': name,
        'image': image_path
    }
    
    cities.append(new_city)
    save_cities_config(country, category, cities)
    
    return jsonify({'success': True, 'message': f'Город "{name}" добавлен'})

@app.route('/api/admin/update-city', methods=['POST'])
def update_city():
    """Обновить название города"""
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.json.get('category', 'restaurants')
    city_id = request.json.get('city_id')
    name = request.json.get('name', '').strip()
    
    cities = load_cities_config(country, category)
    
    for city in cities:
        if city.get('id') == city_id:
            city['name'] = name
            save_cities_config(country, category, cities)
            return jsonify({'success': True, 'message': 'Город обновлён'})
    
    return jsonify({'error': 'City not found'}), 404

@app.route('/api/admin/update-city-photo', methods=['POST'])
def update_city_photo():
    """Обновить фото города"""
    password = request.form.get('password', '')
    country = request.form.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.form.get('category', 'restaurants')
    city_id = request.form.get('city_id')
    photo = request.files.get('photo')
    
    if not photo or not photo.filename:
        return jsonify({'error': 'Photo required'}), 400
    
    cities = load_cities_config(country, category)
    
    for city in cities:
        if city.get('id') == city_id:
            file_data = photo.read()
            ext = photo.filename.rsplit('.', 1)[-1].lower() if '.' in photo.filename else 'jpg'
            
            os.makedirs('static/icons/cities', exist_ok=True)
            filename = f"{city_id}.{ext}"
            filepath = f"static/icons/cities/{filename}"
            with open(filepath, 'wb') as f:
                f.write(file_data)
            
            city['image'] = f"/static/icons/cities/{filename}"
            save_cities_config(country, category, cities)
            return jsonify({'success': True, 'message': 'Фото обновлено'})
    
    return jsonify({'error': 'City not found'}), 404

@app.route('/api/admin/delete-city', methods=['POST'])
def delete_city():
    """Удалить город"""
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    category = request.json.get('category', 'restaurants')
    city_id = request.json.get('city_id')
    
    cities = load_cities_config(country, category)
    
    for i, city in enumerate(cities):
        if city.get('id') == city_id:
            cities.pop(i)
            save_cities_config(country, category, cities)
            return jsonify({'success': True, 'message': 'Город удалён'})
    
    return jsonify({'error': 'City not found'}), 404

@app.route('/api/admin/edit-city-inline', methods=['POST'])
def edit_city_inline():
    """Редактировать город из основного меню (название и фото)"""
    password = request.form.get('password', '')
    country = request.form.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    section = request.form.get('section', 'restaurants')
    old_name = request.form.get('old_name', '')
    new_name = request.form.get('new_name', '')
    photo = request.files.get('photo')
    
    if not old_name or not new_name:
        return jsonify({'error': 'City names required'}), 400
    
    # Обновляем citiesByCountry в dashboard.html через JSON config
    config_file = f'city_config_{country}.json'
    config = {}
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    
    # Обновляем название города в списке
    if section not in config:
        config[section] = {}
    
    section_data = config.get(section, {})
    cities_list = section_data.get('cities', [])
    
    # Ищем город и меняем название
    for i, city in enumerate(cities_list):
        if city == old_name:
            cities_list[i] = new_name
            break
    
    section_data['cities'] = cities_list
    
    # Обрабатываем фото
    if photo and photo.filename:
        file_data = photo.read()
        ext = photo.filename.rsplit('.', 1)[-1].lower() if '.' in photo.filename else 'jpg'
        
        os.makedirs(f'static/icons/cities/{country}/{section}', exist_ok=True)
        safe_name = new_name.replace(' ', '_').lower()
        filename = f"{safe_name}.{ext}"
        filepath = f"static/icons/cities/{country}/{section}/{filename}"
        with open(filepath, 'wb') as f:
            f.write(file_data)
        
        # Сохраняем URL фото
        if 'images' not in section_data:
            section_data['images'] = {}
        section_data['images'][new_name] = f"/{filepath}"
    
    config[section] = section_data
    
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    return jsonify({'success': True, 'message': 'Город обновлён'})

@app.route('/api/admin/move-city-position', methods=['POST'])
def move_city_position():
    """Переместить город вверх/вниз в списке"""
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    section = request.json.get('section', 'restaurants')
    city_name = request.json.get('city_name', '')
    direction = request.json.get('direction', 0)  # -1 вверх, +1 вниз
    
    if not city_name:
        return jsonify({'error': 'City name required'}), 400
    
    config_file = f'city_config_{country}.json'
    config = {}
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    
    if section not in config:
        return jsonify({'error': 'Section not found'}), 404
    
    cities_list = config[section].get('cities', [])
    
    # Находим индекс города
    try:
        idx = cities_list.index(city_name)
    except ValueError:
        return jsonify({'error': 'City not found'}), 404
    
    new_idx = idx + direction
    
    if new_idx < 0 or new_idx >= len(cities_list):
        return jsonify({'error': 'Cannot move beyond list boundaries'}), 400
    
    # Меняем местами
    cities_list[idx], cities_list[new_idx] = cities_list[new_idx], cities_list[idx]
    config[section]['cities'] = cities_list
    
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    return jsonify({'success': True, 'message': 'Город перемещён'})

@app.route('/api/admin/delete-city-inline', methods=['POST'])
def delete_city_inline():
    """Удалить город из основного меню"""
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    section = request.json.get('section', 'restaurants')
    city_name = request.json.get('city_name', '')
    
    if not city_name:
        return jsonify({'error': 'City name required'}), 400
    
    config_file = f'city_config_{country}.json'
    config = {}
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    
    if section not in config:
        return jsonify({'error': 'Section not found'}), 404
    
    cities_list = config[section].get('cities', [])
    
    if city_name in cities_list:
        cities_list.remove(city_name)
        config[section]['cities'] = cities_list
        
        # Удаляем фото если есть
        if 'images' in config[section] and city_name in config[section]['images']:
            del config[section]['images'][city_name]
        
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        
        return jsonify({'success': True, 'message': 'Город удалён'})
    
    return jsonify({'error': 'City not found'}), 404

# ============ РУЧНОЙ ПАРСЕР ============

@app.route('/api/admin/manual-parse', methods=['POST'])
def manual_parse():
    """Ручной парсинг канала - 100% всех сообщений"""
    password = request.json.get('password', '')
    country = request.json.get('country', 'vietnam')
    
    is_valid, admin_country = check_admin_password(password, country)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if admin_country != 'all' and admin_country != country:
        return jsonify({'error': 'No access to this country'}), 403
    channel = request.json.get('channel', '').strip().replace('@', '')
    category = request.json.get('category', 'chat')
    limit = request.json.get('limit', 0)  # 0 = все сообщения
    
    if not channel:
        return jsonify({'error': 'Channel name required'}), 400
    
    try:
        # Пытаемся использовать Telethon парсер
        from telethon.sync import TelegramClient
        
        api_id = os.environ.get('TELEGRAM_API_ID')
        api_hash = os.environ.get('TELEGRAM_API_HASH')
        
        if not api_id or not api_hash:
            return jsonify({'error': 'Telegram API credentials not configured'}), 400
        
        session_name = 'goldantelope_manual'
        client = TelegramClient(session_name, int(api_id), api_hash)
        
        count = 0
        log_messages = []
        
        with client:
            entity = client.get_entity(channel)
            
            # Если limit=0, загружаем ВСЕ сообщения (iter_messages без limit)
            if limit == 0 or limit >= 10000:
                messages = client.iter_messages(entity)
            else:
                messages = client.iter_messages(entity, limit=limit)
            
            data = load_data(country)

            if category not in data:
                data[category] = []
            
            existing_ids = set(item.get('telegram_link', '') for item in data[category])
            
            for msg in messages:
                if msg.text:
                    telegram_link = f"https://t.me/{channel}/{msg.id}"
                    
                    # Пропускаем дубликаты
                    if telegram_link in existing_ids:
                        continue
                    
                    # Создаём объявление
                    listing_id = f"{country}_{category}_{int(time.time())}_{count}"
                    
                    new_listing = {
                        'id': listing_id,
                        'title': msg.text[:100] if msg.text else 'Без названия',
                        'description': msg.text,
                        'date': msg.date.isoformat() if msg.date else datetime.now().isoformat(),
                        'telegram_link': telegram_link,
                        'category': category
                    }
                    
                    # Обработка фото - пересылаем в наш Telegram канал
                    if msg.photo:
                        try:
                            # Скачиваем фото во временный буфер
                            import io
                            photo_buffer = io.BytesIO()
                            client.download_media(msg.photo, file=photo_buffer)
                            photo_buffer.seek(0)
                            image_data = photo_buffer.read()
                            
                            if image_data:
                                # Отправляем в Telegram канал с полным текстом
                                caption = f"📋 {new_listing['title']}\n\n{msg.text[:900] if msg.text else ''}"
                                file_id = send_photo_to_channel(image_data, caption)
                                
                                if file_id:
                                    new_listing['telegram_file_id'] = file_id
                                    new_listing['telegram_photo'] = True
                                    # Получаем актуальный URL
                                    fresh_url = get_telegram_photo_url(file_id)
                                    if fresh_url:
                                        new_listing['image_url'] = fresh_url
                                    log_messages.append(f"[✓] Фото #{count+1} загружено в Telegram канал")
                        except Exception as photo_err:
                            log_messages.append(f"[!] Ошибка фото: {photo_err}")
                    
                    data[category].insert(0, new_listing)
                    existing_ids.add(telegram_link)
                    count += 1
                    
                    if count % 50 == 0:
                        log_messages.append(f"[{count}] Обработано {count} сообщений...")
            
            save_data(country, data)
        
        return jsonify({
            'success': True, 
            'message': f'Парсинг завершён. Добавлено {count} объявлений из канала @{channel}.',
            'count': count,
            'log': '\n'.join(log_messages[-30:])
        })
        
    except ImportError:
        return jsonify({'error': 'Telethon не установлен. Используйте Bot API.'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ TELEGRAM КАНАЛ ДЛЯ ФОТО ============

TELEGRAM_PHOTO_CHANNEL = '-1003577636318'

def send_photo_to_group(image_data, listing, chat_id):
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        return None
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        caption_parts = []
        if listing:
            if listing.get('title'):
                caption_parts.append(f"📋 {listing['title']}")
            if listing.get('description'):
                desc = listing['description'][:600]
                caption_parts.append(desc)
            if listing.get('city'):
                caption_parts.append(f"📍 {listing['city']}")
            if listing.get('contact_name'):
                caption_parts.append(f"👤 {listing['contact_name']}")
            if listing.get('telegram'):
                tg = listing['telegram']
                if not tg.startswith('@'):
                    tg = '@' + tg
                caption_parts.append(f"✈️ {tg}")
            if listing.get('whatsapp'):
                caption_parts.append(f"💬 {listing['whatsapp']}")
        caption = '\n'.join(caption_parts)[:1024]
        files = {'photo': ('photo.jpg', image_data, 'image/jpeg')}
        data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}
        print(f"TELEGRAM: Sending photo to group {chat_id}, size: {len(image_data)} bytes")
        response = requests.post(url, files=files, data=data, timeout=30)
        result = response.json()
        if result.get('ok'):
            print(f"TELEGRAM: Photo sent to {chat_id}, message_id={result['result']['message_id']}")
            return result['result']
        else:
            print(f"TELEGRAM: Failed to send to {chat_id}: {result.get('description')}")
            return None
    except Exception as e:
        print(f"TELEGRAM: Error sending to group {chat_id}: {e}")
        return None

def send_photo_to_channel(image_data, caption=''):
    """Отправить фото в Telegram канал и получить file_id для постоянного хранения"""
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        print("TELEGRAM: Bot token not found!")
        return None
    
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        
        files = {'photo': ('photo.jpg', image_data, 'image/jpeg')}
        data = {
            'chat_id': TELEGRAM_PHOTO_CHANNEL,
            'caption': caption[:1024] if caption else ''
        }
        
        print(f"TELEGRAM: Sending photo to channel {TELEGRAM_PHOTO_CHANNEL}, size: {len(image_data)} bytes")
        response = requests.post(url, files=files, data=data, timeout=30)
        result = response.json()
        print(f"TELEGRAM: Response: {result}")
        
        if result.get('ok'):
            photo = result['result'].get('photo', [])
            if photo:
                largest = max(photo, key=lambda x: x.get('file_size', 0))
                file_id = largest.get('file_id')
                print(f"TELEGRAM: Photo uploaded! file_id: {file_id[:50]}...")
                return file_id
        else:
            print(f"TELEGRAM: Failed to send photo: {result.get('description', 'Unknown error')}")
        
        return None
    except Exception as e:
        print(f"TELEGRAM: Error sending photo to channel: {e}")
        return None

_tg_url_cache = {}  # {file_id: (url, expires_at)}
_TG_URL_TTL = 3000  # seconds (~50 min, Telegram links valid ~1 hour)

def get_telegram_photo_url(file_id):
    """Получить актуальный URL фото по file_id (с кешированием на 50 мин)"""
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token or not file_id:
        return None
    # Check cache
    cached = _tg_url_cache.get(file_id)
    if cached and time.time() < cached[1]:
        return cached[0]
    try:
        file_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
        file_response = requests.get(file_url, timeout=6).json()
        if file_response.get('ok'):
            file_path = file_response['result'].get('file_path')
            url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            _tg_url_cache[file_id] = (url, time.time() + _TG_URL_TTL)
            return url
    except Exception:
        pass
    return None


_OLD_BOT_TOKEN_RE = re.compile(r'api\.telegram\.org/file/bot([^/]+)/(.+)')

def _retoken_url(url, new_token):
    """Replace the bot token in a Telegram file URL with the current token."""
    if not url or not new_token:
        return url
    m = _OLD_BOT_TOKEN_RE.search(url)
    if m:
        file_path = m.group(2)
        return f"https://api.telegram.org/file/bot{new_token}/{file_path}"
    return url


def _refresh_photo_urls_parallel(items):
    """Refresh image_url for all items:
    1. If image_url has a Telegram file path, replace the (possibly old) bot token.
    2. Otherwise, call getFile API with telegram_file_id to get a fresh path.
    """
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        return

    # Separate items: those with existing Telegram paths vs those needing getFile
    need_getfile = []
    for item in items:
        url = item.get('image_url', '') or ''
        if _OLD_BOT_TOKEN_RE.search(url):
            # Just swap in the current token — fast, no network call
            item['image_url'] = _retoken_url(url, bot_token)
        elif item.get('telegram_file_id'):
            need_getfile.append(item)

    if not need_getfile:
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=min(10, len(need_getfile))) as ex:
        future_to_item = {ex.submit(get_telegram_photo_url, item['telegram_file_id']): item
                         for item in need_getfile}
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                fresh_url = future.result()
                if fresh_url:
                    item['image_url'] = fresh_url
            except Exception:
                pass

# ============ ВНУТРЕННИЙ ЧАТ С TELEGRAM АВТОРИЗАЦИЕЙ ============

CHAT_DATA_FILE = 'internal_chat.json'
CHAT_BLACKLIST_FILE = 'chat_blacklist.json'
verification_codes = {}
import random
import string

CHAT_FILES = {
    'vietnam': 'internal_chat.json',
    'thailand': 'internal_chat_thailand.json',
    'india': 'internal_chat_india.json',
    'indonesia': 'internal_chat_indonesia.json'
}

def get_chat_file(country='vietnam'):
    return CHAT_FILES.get(country, CHAT_FILES['vietnam'])

def load_chat_data(country='vietnam'):
    chat_file = get_chat_file(country)
    if os.path.exists(chat_file):
        with open(chat_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            messages = data.get('messages', [])
            three_days_ago = datetime.now() - timedelta(days=3)
            messages = [m for m in messages if datetime.fromisoformat(m.get('timestamp', '2000-01-01')) > three_days_ago]
            return {'messages': messages[-1000:], 'users': data.get('users', {})}
    return {'messages': [], 'users': {}}

def save_chat_data(data, country='vietnam'):
    chat_file = get_chat_file(country)
    three_days_ago = datetime.now() - timedelta(days=3)
    data['messages'] = [m for m in data.get('messages', []) if datetime.fromisoformat(m.get('timestamp', '2000-01-01')) > three_days_ago][-1000:]
    with open(chat_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_blacklist():
    if os.path.exists(CHAT_BLACKLIST_FILE):
        with open(CHAT_BLACKLIST_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'users': []}

def save_blacklist(data):
    with open(CHAT_BLACKLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

CHAT_USERS_FILE = 'chat_users.json'

def load_chat_users():
    if os.path.exists(CHAT_USERS_FILE):
        with open(CHAT_USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_chat_users(data):
    with open(CHAT_USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def find_chat_id_by_username(username):
    users = load_chat_users()
    username_lower = username.lower().replace('@', '')
    if username_lower in users:
        return users[username_lower]
    
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        return None
    
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates?limit=100"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            updates = resp.json().get('result', [])
            for upd in updates:
                msg = upd.get('message', {})
                user = msg.get('from', {})
                uname = user.get('username', '').lower()
                chat_id = msg.get('chat', {}).get('id')
                if uname and chat_id:
                    users[uname] = str(chat_id)
            save_chat_users(users)
            if username_lower in users:
                return users[username_lower]
    except Exception as e:
        print(f"Error finding chat_id: {e}")
    return None

@app.route('/api/chat/request-code', methods=['POST'])
def request_chat_code():
    data = request.json
    username = data.get('telegram_id', '').strip().replace('@', '')
    if not username:
        return jsonify({'success': False, 'error': 'Укажите ваш @username'})
    
    blacklist = load_blacklist()
    if username.lower() in [u.lower() for u in blacklist.get('users', [])]:
        return jsonify({'success': False, 'error': 'Ваш аккаунт заблокирован'})
    
    chat_id = find_chat_id_by_username(username)
    if not chat_id:
        return jsonify({'success': False, 'error': 'Сначала напишите боту @goldantelope_bot команду /start'})
    
    code = ''.join(random.choices(string.digits, k=6))
    verification_codes[username.lower()] = {'code': code, 'expires': datetime.now() + timedelta(minutes=10), 'chat_id': chat_id}
    
    message = f"🔐 Ваш код для чата GoldAntelope:\n\n<b>{code}</b>\n\nКод действителен 10 минут."
    
    try:
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
        if bot_token:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            resp = requests.post(url, json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'}, timeout=10)
            if resp.status_code == 200 and resp.json().get('ok'):
                return jsonify({'success': True, 'message': 'Код отправлен в Telegram'})
            else:
                error_desc = resp.json().get('description', 'Ошибка отправки')
                return jsonify({'success': False, 'error': f'Ошибка Telegram: {error_desc}'})
    except Exception as e:
        print(f"Chat code error: {e}")
    
    return jsonify({'success': False, 'error': 'Не удалось отправить код'})

@app.route('/api/chat/verify-code', methods=['POST'])
def verify_chat_code():
    data = request.json
    telegram_id = data.get('telegram_id', '').strip().replace('@', '').lower()
    code = data.get('code', '').strip()
    
    if not telegram_id or not code:
        return jsonify({'success': False, 'error': 'Укажите ID и код'})
    
    stored = verification_codes.get(telegram_id)
    if not stored:
        return jsonify({'success': False, 'error': 'Сначала запросите код'})
    
    if datetime.now() > stored['expires']:
        del verification_codes[telegram_id]
        return jsonify({'success': False, 'error': 'Код истёк, запросите новый'})
    
    if stored['code'] != code:
        return jsonify({'success': False, 'error': 'Неверный код'})
    
    del verification_codes[telegram_id]
    
    session_token = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    
    for country in CHAT_FILES.keys():
        chat_data = load_chat_data(country)
        chat_data['users'][session_token] = {'telegram_id': telegram_id, 'created': datetime.now().isoformat()}
        save_chat_data(chat_data, country)
    
    return jsonify({'success': True, 'token': session_token, 'username': telegram_id})

@app.route('/api/chat/messages', methods=['GET'])
def get_chat_messages():
    country = request.args.get('country', 'vietnam')
    chat_data = load_chat_data(country)
    return jsonify({'messages': chat_data.get('messages', [])[-1000:]})

@app.route('/api/chat/send', methods=['POST'])
def send_chat_message():
    data = request.json
    username = data.get('username', 'Гость').strip()
    message = data.get('message', '').strip()
    country = data.get('country', 'vietnam')
    
    if not message:
        return jsonify({'success': False, 'error': 'Введите сообщение'})
    
    if not username:
        username = 'Гость'
    
    if len(message) > 2000:
        return jsonify({'success': False, 'error': 'Сообщение слишком длинное (макс 2000 символов)'})
    
    if len(username) > 50:
        return jsonify({'success': False, 'error': 'Ник слишком длинный'})
    
    blacklist = load_blacklist()
    if username.lower() in [u.lower() for u in blacklist.get('users', [])]:
        return jsonify({'success': False, 'error': 'Ваш аккаунт заблокирован'})
    
    chat_data = load_chat_data(country)
    
    new_message = {
        'id': f"msg_{int(time.time())}_{random.randint(1000,9999)}",
        'username': username,
        'message': message,
        'timestamp': datetime.now().isoformat()
    }
    
    chat_data['messages'].append(new_message)
    save_chat_data(chat_data, country)
    
    # Дублируем сообщение в Telegram канал
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            tg_text = f"💬 <b>{username}</b>\n{message}"
            send_telegram_notification(tg_text)
        except Exception as e:
            print(f"Error sending chat to Telegram: {e}")
    
    return jsonify({'success': True})

@app.route('/api/admin/chat-blacklist', methods=['GET', 'POST'])
def admin_chat_blacklist():
    admin_key = request.headers.get('X-Admin-Key') or request.json.get('admin_key') if request.json else None
    expected_key = os.environ.get('ADMIN_KEY', 'goldantelope2025')
    if admin_key != expected_key:
        return jsonify({'success': False, 'error': 'Неверный пароль'}), 401
    
    if request.method == 'GET':
        return jsonify(load_blacklist())
    
    data = request.json
    action = data.get('action')
    username = data.get('username', '').strip().replace('@', '').lower()
    
    if not username:
        return jsonify({'success': False, 'error': 'Укажите username'})
    
    blacklist = load_blacklist()
    
    if action == 'add':
        if username not in blacklist['users']:
            blacklist['users'].append(username)
            save_blacklist(blacklist)
        return jsonify({'success': True, 'message': f'{username} добавлен в чёрный список'})
    elif action == 'remove':
        blacklist['users'] = [u for u in blacklist['users'] if u.lower() != username]
        save_blacklist(blacklist)
        return jsonify({'success': True, 'message': f'{username} удалён из чёрного списка'})
    
    return jsonify({'success': False, 'error': 'Неизвестное действие'})

@app.route('/api/admin/chat-delete', methods=['POST'])
def admin_delete_chat_message():
    data = request.json
    admin_key = data.get('admin_key')
    expected_key = os.environ.get('ADMIN_KEY', 'goldantelope2025')
    if admin_key != expected_key:
        return jsonify({'success': False, 'error': 'Неверный пароль'}), 401
    
    msg_id = data.get('message_id')
    if not msg_id:
        return jsonify({'success': False, 'error': 'Укажите ID сообщения'})
    
    chat_data = load_chat_data()
    chat_data['messages'] = [m for m in chat_data['messages'] if m.get('id') != msg_id]
    save_chat_data(chat_data)
    
    return jsonify({'success': True, 'message': 'Сообщение удалено'})















def run_bot():
    try:
        import asyncio
        import json
        import os
        from telethon import TelegramClient
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        api_id = os.environ.get('TELEGRAM_API_ID')
        api_hash = os.environ.get('TELEGRAM_API_HASH')
        bot_token = os.environ.get('telegram_bot_token')
        channel_id = os.environ.get('telegram_channel_id')
        
        if not api_id or not api_hash or not bot_token:
            print("[run_bot] TELEGRAM_API_ID / TELEGRAM_API_HASH / telegram_bot_token не заданы — бот отключён")
            return
        
        client = TelegramClient('bot_session', int(api_id), api_hash)
        
        async def monitor():
            await client.start(bot_token=bot_token)
            print("--- БОТ ЗАПУЩЕН: ПОИСК ФОТО + ПОЛНЫЙ ПОСТ ---")
            while True:
                try:
                    fname = 'ads_channels_vietnam.json'
                    if os.path.exists(fname):
                        with open(fname, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        
                        changed = False
                        for ch in data.get('channels', []):
                            # Если одобрено и флаг отправки не стоит
                            if ch.get('approved') == True and ch.get('sent_to_tg') != True:
                                ad_id = ch.get('id', '').replace('ad_', '')
                                
                                caption = (
                                    f"🔥 **НОВОЕ ОБЪЯВЛЕНИЕ**\n\n"
                                    f"📝 **Название:** {ch.get('name', 'N/A')}\n"
                                    f"📁 **Категория:** #{ch.get('category', 'vietnam').replace(' ', '_')}\n"
                                    f"📍 **Город:** {ch.get('city', 'Вьетнам')}\n"
                                    f"💰 **Цена:** {ch.get('price', '—')} USD\n"
                                    f"📞 **Контакт:** {ch.get('contact', 'N/A')}"
                                )

                                # Ищем все фото в папке static по ID
                                photo_paths = []
                                for root, dirs, files in os.walk("static"):
                                    for file in files:
                                        if ad_id in file and file.lower().endswith(('.png', '.jpg', '.jpeg')):
                                            photo_paths.append(os.path.join(root, file))
                                
                                photo_paths = list(dict.fromkeys(photo_paths))[:4]

                                if photo_paths:
                                    print(f"--- ОТПРАВКА АЛЬБОМА ДЛЯ {ad_id} ---")
                                    await client.send_file(int(channel_id), photo_paths, caption=caption, parse_mode='md')
                                else:
                                    print(f"--- ФОТО НЕ НАЙДЕНЫ ДЛЯ {ad_id}, ШЛЮ ТЕКСТ ---")
                                    await client.send_message(int(channel_id), caption, parse_mode='md')
                                
                                ch['sent_to_tg'] = True
                                changed = True

                        if changed:
                            with open(fname, 'w', encoding='utf-8') as f:
                                json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"Ошибка цикла: {e}")
                await asyncio.sleep(15)
        
        loop.run_until_complete(monitor())
    except Exception as e:
        print(f"Ошибка авторизации: {e}")

# ============ GLOBALPARSING HUGGINGFACE SPACE INTEGRATION ============

HF_SPACE_URL = 'https://poweramanita-baza.hf.space'
HF_API_URL = 'https://huggingface.co/api/spaces/poweramanita/Baza'

@app.route('/api/admin/globalparsing-status', methods=['GET'])
def globalparsing_status():
    """Статус парсера всех групп на HuggingFace Space."""
    try:
        hf_token = os.environ.get('HF_TOKEN', '')
        headers = {}
        if hf_token:
            headers['Authorization'] = f'Bearer {hf_token}'

        ping_ok = False
        try:
            ping_r = requests.get(f'{HF_SPACE_URL}/', timeout=8)
            ping_ok = ping_r.status_code == 200 and ping_r.json().get('status') in ('ok', 'running')
        except Exception:
            pass

        space_info = {}
        try:
            meta_r = requests.get(HF_API_URL, headers=headers, timeout=8)
            if meta_r.status_code == 200:
                meta = meta_r.json()
                runtime = meta.get('runtime', {})
                space_info = {
                    'stage': runtime.get('stage', 'UNKNOWN'),
                    'hardware': runtime.get('hardware', {}).get('current', 'unknown'),
                    'replicas': runtime.get('replicas', {}).get('current', 0),
                    'last_modified': meta.get('lastModified', ''),
                }
        except Exception:
            pass

        return jsonify({
            'success': True,
            'ping_ok': ping_ok,
            'space_url': HF_SPACE_URL,
            'space_info': space_info,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/monitoring-stats', methods=['GET'])
def monitoring_stats():
    """Полная статистика мониторинга: статусы парсеров + кол-во объявлений по группам."""
    import re as _re

    # ── 1. HuggingFace Space ──
    hf_token = os.environ.get('HF_TOKEN', '')
    hf_headers = {'Authorization': f'Bearer {hf_token}'} if hf_token else {}
    hf_ping = False
    hf_info = {}
    try:
        r = requests.get(f'{HF_SPACE_URL}/', timeout=6)
        hf_ping = r.status_code == 200 and r.json().get('status') in ('ok', 'running')
    except Exception:
        pass
    try:
        r2 = requests.get(HF_API_URL, headers=hf_headers, timeout=6)
        if r2.status_code == 200:
            meta = r2.json()
            rt = meta.get('runtime', {})
            hf_info = {
                'stage': rt.get('stage', 'UNKNOWN'),
                'hardware': rt.get('hardware', {}).get('current', 'unknown'),
                'replicas': rt.get('replicas', {}).get('current', 0),
                'last_modified': meta.get('lastModified', ''),
            }
    except Exception:
        pass

    # ── 2. Vietnamparsing parser ──
    vp_state = {}
    try:
        from vietnamparsing_parser import get_parser_state
        vp_state = get_parser_state()
    except Exception:
        pass

    # ── 3. Telethon forwarder ──
    tf_stats = {}
    try:
        from telethon_forwarder import STATS as TF_STATS, SOURCES as TF_SOURCES, DEST as TF_DEST
        tf_stats = {
            'running': TF_STATS.get('running', False),
            'user': TF_STATS.get('user'),
            'started_at': TF_STATS.get('started_at'),
            'total_messages': TF_STATS.get('total_messages', 0),
            'total_photos': TF_STATS.get('total_photos', 0),
            'total_albums': TF_STATS.get('total_albums', 0),
            'groups': {},
        }
        for grp, names in TF_SOURCES.items():
            ok = TF_STATS.get('connected', {}).get(grp, [])
            fail = TF_STATS.get('failed', {}).get(grp, [])
            fwd = TF_STATS.get('forwarded', {}).get(grp, {})
            tf_stats['groups'][grp] = {
                'dest': TF_DEST.get(grp, ''),
                'total': len(names),
                'connected': len(ok),
                'failed': len(fail),
                'channels_ok': ok,
                'channels_fail': fail,
                'forwarded_messages': fwd.get('messages', 0),
                'forwarded_photos': fwd.get('photos', 0),
                'forwarded_albums': fwd.get('albums', 0),
            }
    except Exception as e:
        tf_stats = {'error': str(e)}

    # ── 4. HF Space source channels (from globalparsing) ──
    HF_SOURCES = {
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
        ],
    }

    # ── 5. Listings stats by source channel ──
    files = {
        'vietnam': 'listings_vietnam.json',
        'thailand': 'listings_thailand.json',
        'india': 'listings_india.json',
        'indonesia': 'listings_indonesia.json',
    }
    channel_stats = {}   # {channel: {country, category, count}}
    country_totals = {}  # {country: count}
    category_totals = {} # {category: count}

    for country, fname in files.items():
        try:
            data = load_data(country)
            country_totals[country] = 0
            for cat, items in data.items():
                if not isinstance(items, list):
                    continue
                category_totals[cat] = category_totals.get(cat, 0) + len(items)
                country_totals[country] += len(items)
                for item in items:
                    _AGGREGATORS = {'vietnamparsing','thailandparsing','baykivietnam'}
                    _IGNORE_SRC = _AGGREGATORS | {'https','http','s','joinchat','c'}
                    src = ''
                    tl = item.get('telegram_link', '')
                    m = _re.search(r't\.me/([A-Za-z][A-Za-z0-9_]{2,})/\d+', tl)
                    if m and m.group(1).lower() not in _IGNORE_SRC:
                        src = m.group(1)
                    if not src:
                        desc = item.get('description', '') or item.get('text', '') or ''
                        m_ist = _re.search(r'Источник:\s*(?:https?://t\.me/)?@?([A-Za-z][A-Za-z0-9_]{2,})', desc)
                        if m_ist and m_ist.group(1).lower() not in _IGNORE_SRC:
                            src = m_ist.group(1)
                        if not src:
                            for tm in _re.finditer(r't\.me/([A-Za-z][A-Za-z0-9_]{2,})/\d+', desc):
                                if tm.group(1).lower() not in _IGNORE_SRC:
                                    src = tm.group(1)
                                    break
                    if not src:
                        src = item.get('channel', '') or item.get('source_channel', '')
                        if src and src.lstrip('@').lower() in _IGNORE_SRC:
                            src = ''
                    src = src.lstrip('@').lower() if src else 'unknown'
                    key = src
                    if key not in channel_stats:
                        channel_stats[key] = {}
                    if country not in channel_stats[key]:
                        channel_stats[key][country] = {}
                    channel_stats[key][country][cat] = channel_stats[key][country].get(cat, 0) + 1
        except Exception:
            pass

    # Flatten to list sorted by total count
    channel_list = []
    for ch, countries in channel_stats.items():
        total = sum(sum(cats.values()) for cats in countries.values())
        country_str = ', '.join(sorted(countries.keys()))
        cats_all = {}
        for cats in countries.values():
            for c, n in cats.items():
                cats_all[c] = cats_all.get(c, 0) + n
        top_cat = max(cats_all, key=cats_all.get) if cats_all else ''
        channel_list.append({
            'channel': ch,
            'total': total,
            'countries': country_str,
            'top_category': top_cat,
            'by_country': countries,
        })
    channel_list.sort(key=lambda x: -x['total'])

    return jsonify({
        'success': True,
        'hf_space': {
            'ping_ok': hf_ping,
            'url': HF_SPACE_URL,
            'info': hf_info,
            'source_channels': HF_SOURCES,
        },
        'vietnamparsing': vp_state,
        'telethon_forwarder': tf_stats,
        'listings': {
            'by_channel': channel_list,
            'country_totals': country_totals,
            'category_totals': category_totals,
        },
    })


# ============ HF CHANNELS HEALTH CHECK ============

@app.route('/api/admin/hf-channels-check', methods=['GET'])
def hf_channels_check():
    """Check accessibility of all HF Space source Telegram channels in parallel."""
    import requests as _req
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _time
    import re as _re2

    HF_SOURCES = {
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
        ],
    }

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (compatible; TelegramBot/1.0)',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8',
    }

    def check_channel(grp, ch):
        url = f'https://t.me/s/{ch}'
        t0 = _time.time()
        try:
            r = _req.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
            elapsed = round((_time.time() - t0) * 1000)
            ok = r.status_code == 200
            msg_count = None
            last_date = None
            if ok:
                html = r.text
                # Try to extract message count
                m = _re2.search(r'(\d[\d\s,]+)\s*(?:subscriber|member|подписч)', html, _re2.I)
                # Try to extract last post date
                dm = _re2.findall(r'"datePublished"\s*:\s*"([^"]+)"', html)
                last_date = dm[-1][:10] if dm else None
            return {
                'group': grp,
                'channel': ch,
                'ok': ok,
                'status': r.status_code,
                'ms': elapsed,
                'last_post': last_date,
            }
        except Exception as e:
            elapsed = round((_time.time() - t0) * 1000)
            return {
                'group': grp,
                'channel': ch,
                'ok': False,
                'status': 0,
                'ms': elapsed,
                'last_post': None,
                'error': str(e)[:60],
            }

    tasks = []
    for grp, channels in HF_SOURCES.items():
        for ch in channels:
            tasks.append((grp, ch))

    results = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(check_channel, grp, ch): (grp, ch) for grp, ch in tasks}
        for f in as_completed(futures):
            results.append(f.result())

    results.sort(key=lambda x: (x['group'], x['channel'].lower()))

    ok_count = sum(1 for r in results if r['ok'])
    return jsonify({
        'success': True,
        'total': len(results),
        'ok': ok_count,
        'failed': len(results) - ok_count,
        'channels': results,
    })


# ============ FETCH EMPTY CHANNELS (one-shot history scrape) ============

_FETCH_STATE = {'running': False, 'done': False, 'total': 0, 'current': '', 'results': {}, 'error': None}

def _run_fetch_empty():
    import time as _time
    _FETCH_STATE.update({'running': True, 'done': False, 'total': 0, 'current': '', 'results': {}, 'error': None})

    # Все HF-каналы (68): THAI×23 / VIET×38 / BIKE×7
    EMPTY_BIKE = [
        'bike_nhatrang','motohub_nhatrang','NhaTrang_moto_market','RentBikeUniq',
        'BK_rental','nha_trang_rent','RentTwentyTwo22NhaTrang',
    ]
    EMPTY_VIET = [
        'phuquoc_rent_wt','phyquocnedvigimost','Viet_Life_Phu_Quoc_rent','nhatrangapartment',
        'tanrealtorgh','viet_life_niachang','nychang_arenda','rent_nha_trang','nyachang_nedvizhimost',
        'nedvizimost_nhatrang','nhatrangforrent79','NhatrangRentl','arenda_v_nyachang','rent_appart_nha',
        'Arenda_Nyachang_Zhilye','NhaTrang_rental','realestatebythesea_1','NhaTrang_Luxury',
        'luckyhome_nhatrang','rentnhatrang','megasforrentnhatrang','viethome',
        'Vietnam_arenda','huynhtruonq','DaNangRentAFlat','danag_viet_life_rent','Danang_House',
        'DaNangApartmentRent','danang_arenda','arenda_v_danang','HoChiMinhRentI','hcmc_arenda',
        'Hanoirentapartment','HanoiRentl','Hanoi_Rent','PhuquocRentl',
    ]
    EMPTY_THAI = [
        'arenda_phukets','THAILAND_REAL_ESTATE_PHUKET','housephuket','arenda_phuket_thailand',
        'phuket_nedvizhimost_rent','phuketsk_arenda','phuket_nedvizhimost_thailand','phuketsk_for_rent',
        'phuket_rentas','rentalsphuketonli','rentbuyphuket','Phuket_thailand05','nedvizhimost_pattaya',
        'arenda_pattaya','pattaya_realty_estate','HappyHomePattaya','sea_bangkok','Samui_for_you',
        'sea_phuket','realty_in_thailand','nedvig_thailand','thailand_nedvizhimost','globe_nedvizhka_Thailand',
    ]

    try:
        from vietnamparsing_parser import (
            scrape_extra_channel_page, build_generic_listing,
            load_listings as viet_load, save_listings as viet_save,
            get_existing_ids as viet_ids_fn,
            detect_city as viet_detect_city,
            get_content_fingerprints as viet_fps_fn,
            _content_fingerprint as viet_fp,
        )
        from thailandparsing_parser import (
            load_listings as thai_load, save_listings as thai_save,
            get_existing_ids as thai_ids_fn,
            is_spam as thai_spam, extract_price as thai_price,
            detect_city as thai_city, detect_listing_type as thai_lt,
            extract_title_th,
        )
    except Exception as e:
        _FETCH_STATE['running'] = False
        _FETCH_STATE['error'] = str(e)
        return

    import threading as _threading
    from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _ac

    _state_lock = _threading.Lock()

    # Маппинг группы → канал-получатель
    _DST_CHANNELS = {
        'VIET':      'parsing_vn',
        'THAI':      'parsing_th',
        'BIKE':      'baikeparsing_vn',
        'BIKE_TH':   'baikeparsing_th',
        'CHAT':      'chatparsing_vn',
        'ENTERTAIN': 'tusaparsing_vn',
    }

    def _post_to_channel(grp, item):
        """Отправляет одно объявление в нужный Telegram-канал через Bot API."""
        import requests as _req
        bot_token = os.environ.get('VIETNAMPARSING_BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
        if not bot_token:
            return
        dst = _DST_CHANNELS.get(grp)
        if not dst:
            return
        chat_id = f'@{dst}'

        text = item.get('text') or item.get('description') or item.get('title') or ''
        tg_link = item.get('telegram_link', '')
        caption = (text[:900] + f'\n\n🔗 {tg_link}') if tg_link else text[:1024]
        caption = caption.strip()

        photos = item.get('photos') or item.get('all_images') or []
        photo_url = photos[0] if photos else (item.get('image_url') or '')

        try:
            # Отправляем только текст + ссылку на оригинал (фото t.me/s/ недоступны Bot API)
            r = _req.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json={'chat_id': chat_id, 'text': caption,
                      'parse_mode': 'HTML', 'disable_web_page_preview': True},
                timeout=10)
            if not r.ok:
                rj = r.json()
                desc = rj.get('description', '')
                if r.status_code == 429:
                    wait = rj.get('parameters', {}).get('retry_after', 30)
                    app.logger.warning(f'[forward] rate limit @{dst}: retry after {wait}s')
                    _time.sleep(wait + 1)
                    # повтор после паузы
                    r2 = _req.post(
                        f'https://api.telegram.org/bot{bot_token}/sendMessage',
                        json={'chat_id': chat_id, 'text': caption,
                              'parse_mode': 'HTML', 'disable_web_page_preview': True},
                        timeout=10)
                    if not r2.ok:
                        app.logger.warning(f'[forward] retry failed @{dst}: {r2.json().get("description")}')
                else:
                    app.logger.warning(f'[forward] sendMessage @{dst}: {desc}')
            else:
                app.logger.debug(f'[forward] ✅ @{dst}: {caption[:60]}')
        except Exception as _e:
            app.logger.warning(f'[forward] {grp}→{dst}: {_e}')

    def scrape_100(grp, channel):
        """Скачивает до 100 постов пагинацией, обновляет current в _FETCH_STATE."""
        _FETCH_STATE['current'] = f'{grp} @{channel}'
        all_msgs = []
        before_id = None
        for _ in range(10):
            try:
                page = scrape_extra_channel_page(channel, before_id)
            except Exception:
                break
            if not page:
                break
            all_msgs.extend(page)
            if len(all_msgs) >= 100:
                break
            ids = [m['post_id'] for m in page if m['post_id']]
            if not ids:
                break
            before_id = min(ids)
            _time.sleep(0.2)
        return all_msgs[:100]

    try:
        results = _FETCH_STATE['results']
        to_forward = []  # [(grp, item), ...]  — очередь для пересылки в каналы

        # ── Параллельный скрапинг всех каналов (5 воркеров) ──
        tasks = (
            [(grp, ch) for grp in ('BIKE',) for ch in EMPTY_BIKE] +
            [(grp, ch) for grp in ('VIET',) for ch in EMPTY_VIET] +
            [(grp, ch) for grp in ('THAI',) for ch in EMPTY_THAI]
        )
        scraped = {}  # {(grp, ch): [msgs]}
        with _TPE(max_workers=5) as ex:
            futs = {ex.submit(scrape_100, grp, ch): (grp, ch) for grp, ch in tasks}
            for fut in _ac(futs):
                grp, ch = futs[fut]
                try:
                    scraped[(grp, ch)] = fut.result()
                except Exception as e:
                    scraped[(grp, ch)] = []
                    app.logger.warning(f"[fetch_empty] {grp} {ch}: {e}")

        # ── Vietnam data (BIKE + VIET) ──
        viet_data = viet_load()
        viet_ids = viet_ids_fn(viet_data)
        viet_fps = viet_fps_fn(viet_data)
        viet_data.setdefault('transport', [])
        viet_data.setdefault('real_estate', [])

        for ch in EMPTY_BIKE:
            msgs = scraped.get(('BIKE', ch), [])
            count = 0
            for msg in msgs:
                item_id = f"{ch}_{msg['post_id']}"
                if item_id in viet_ids:
                    continue
                item = build_generic_listing(msg, item_id, ch, 'transport', 'bikes')
                if item is None:
                    continue
                fp = viet_fp(item)
                if fp != '||' and fp in viet_fps:
                    continue
                viet_data['transport'].insert(0, item)
                viet_ids.add(item_id)
                viet_fps.add(fp)
                to_forward.append(('BIKE', item))
                count += 1
            results[ch] = count
            _FETCH_STATE['total'] += count

        for ch in EMPTY_VIET:
            msgs = scraped.get(('VIET', ch), [])
            count = 0
            for msg in msgs:
                item_id = f"{ch}_{msg['post_id']}"
                if item_id in viet_ids:
                    continue
                item = build_generic_listing(msg, item_id, ch, 'real_estate')
                if item is None:
                    continue
                # Недвижимость — только с фото
                if not item.get('image_url') and not item.get('photos'):
                    continue
                fp = viet_fp(item)
                if fp != '||' and fp in viet_fps:
                    continue
                city = viet_detect_city(item.get('text', ''))
                item['city'] = city or 'Вьетнам'
                item['city_ru'] = city or 'Вьетнам'
                item['country'] = 'vietnam'
                viet_data['real_estate'].insert(0, item)
                viet_ids.add(item_id)
                viet_fps.add(fp)
                to_forward.append(('VIET', item))
                count += 1
            results[ch] = count
            _FETCH_STATE['total'] += count

        viet_save(viet_data)

        # ── Thailand data ──
        thai_data = thai_load()
        thai_ids = thai_ids_fn(thai_data)
        thai_fps = viet_fps_fn(thai_data)
        thai_data.setdefault('real_estate', [])

        for ch in EMPTY_THAI:
            msgs = scraped.get(('THAI', ch), [])
            count = 0
            for msg in msgs:
                text = msg.get('text', '')
                if not text or len(text) < 20:
                    continue
                if thai_spam(text):
                    continue
                photos = msg.get('images', [])
                if not photos:
                    continue
                item_id = f"{ch}_{msg['post_id']}"
                if item_id in thai_ids:
                    continue
                price_val, price_display = thai_price(text)
                city = thai_city(text) or 'Таиланд'
                listing_type = thai_lt(text)
                title = extract_title_th(text)
                thai_item = {
                    'id': item_id, 'title': title,
                    'description': text[:500], 'text': text,
                    'price': price_val, 'price_display': price_display,
                    'city': city, 'listing_type': listing_type,
                    'contact': f'@{ch}',
                    'telegram_link': f'https://t.me/{ch}/{msg["post_id"]}',
                    'photos': photos, 'image_url': photos[0] if photos else '',
                    'all_images': photos, 'date': msg.get('date', ''),
                    'source': 'telegram', 'channel': ch, 'country': 'thailand',
                }
                fp = viet_fp(thai_item)
                if fp != '||' and fp in thai_fps:
                    continue
                thai_data['real_estate'].insert(0, thai_item)
                thai_ids.add(item_id)
                thai_fps.add(fp)
                to_forward.append(('THAI', thai_item))
                count += 1
            results[ch] = count
            _FETCH_STATE['total'] += count

        thai_save(thai_data)

        # ── Пересылка новых объявлений в Telegram-каналы ──
        if to_forward:
            _FETCH_STATE['current'] = f'Пересылка {len(to_forward)} объявлений...'
            app.logger.info(f'[forward] Отправляю {len(to_forward)} объявлений в каналы')
            for grp, item in to_forward:
                try:
                    _post_to_channel(grp, item)
                except Exception as _fe:
                    app.logger.warning(f'[forward] ошибка: {_fe}')
                _time.sleep(3)  # ~20 msg/мин — лимит Telegram для каналов

        try:
            _file_path_cache.clear()
        except Exception:
            pass

    except Exception as e:
        _FETCH_STATE['error'] = str(e)
        app.logger.error(f"[fetch_empty] fatal: {e}")
    finally:
        _FETCH_STATE['running'] = False
        _FETCH_STATE['done'] = True
        _FETCH_STATE['current'] = ''


# ── Forward-100 state & runner ────────────────────────────────────────────────

_FWD100_STATE = {
    'running': False, 'done': False,
    'sent': 0, 'failed': 0, 'current': '',
    'results': {},
    'error': None,
}


def _run_forward_100(only_groups=None):
    """Скачивает последние 100 постов с каждого источника, отправляет фото+текст в каналы-назначения."""
    import time as _time, requests as _req

    init_results = {}
    for g in (only_groups or ['BIKE', 'VIET', 'THAI']):
        init_results[g] = {'sent': 0, 'failed': 0}
    _FWD100_STATE.update({
        'running': True, 'done': False,
        'sent': 0, 'failed': 0, 'current': '',
        'results': init_results,
        'error': None,
    })

    bot_token = os.environ.get('VIETNAMPARSING_BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        _FWD100_STATE.update({'running': False, 'done': True, 'error': 'no bot token'})
        return

    DST = {'BIKE': 'baykivietnam', 'VIET': 'vietnamparsing', 'THAI': 'thailandparsing'}

    M = {
        'THAI': [
            'arenda_phukets','THAILAND_REAL_ESTATE_PHUKET','housephuket','arenda_phuket_thailand',
            'phuket_nedvizhimost_rent','phuketsk_arenda','phuket_nedvizhimost_thailand','phuketsk_for_rent',
            'phuket_rentas','rentalsphuketonli','rentbuyphuket','Phuket_thailand05','nedvizhimost_pattaya',
            'arenda_pattaya','pattaya_realty_estate','HappyHomePattaya','sea_bangkok','Samui_for_you',
            'sea_phuket','realty_in_thailand','nedvig_thailand','thailand_nedvizhimost',
            'globe_nedvizhka_Thailand',
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
        ],
    }

    CDN_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Referer': 'https://t.me/',
    }

    try:
        from vietnamparsing_parser import scrape_extra_channel_page
    except Exception as e:
        _FWD100_STATE.update({'running': False, 'done': True, 'error': str(e)})
        return

    def scrape_last_100(channel):
        """Скрапит до 100 последних постов из t.me/s/channel."""
        all_msgs = []
        before_id = None
        for _ in range(10):
            try:
                page = scrape_extra_channel_page(channel, before_id)
            except Exception:
                break
            if not page:
                break
            all_msgs.extend(page)
            if len(all_msgs) >= 100:
                break
            ids = [m['post_id'] for m in page if m.get('post_id')]
            if not ids:
                break
            before_id = min(ids)
            _time.sleep(0.2)
        return all_msgs[:100]

    def download_photo(url):
        """Скачивает CDN-фото, возвращает bytes или None."""
        try:
            r = _req.get(url, headers=CDN_HEADERS, timeout=12)
            if r.ok and len(r.content) > 500:
                return r.content
        except Exception:
            pass
        return None

    def send_with_photo(dst_ch, photo_bytes, caption):
        """Отправляет фото + подпись. Возвращает True/False."""
        try:
            r = _req.post(
                f'https://api.telegram.org/bot{bot_token}/sendPhoto',
                data={'chat_id': f'@{dst_ch}', 'caption': caption[:1024]},
                files={'photo': ('photo.jpg', photo_bytes, 'image/jpeg')},
                timeout=30)
            if r.ok:
                return True
            if r.status_code == 429:
                wait = r.json().get('parameters', {}).get('retry_after', 30)
                app.logger.warning(f'[fwd100] rate limit @{dst_ch}: wait {wait}s')
                _time.sleep(wait + 1)
                r2 = _req.post(
                    f'https://api.telegram.org/bot{bot_token}/sendPhoto',
                    data={'chat_id': f'@{dst_ch}', 'caption': caption[:1024]},
                    files={'photo': ('photo.jpg', photo_bytes, 'image/jpeg')},
                    timeout=30)
                return r2.ok
            app.logger.warning(f'[fwd100] sendPhoto @{dst_ch}: {r.json().get("description","")}')
        except Exception as e:
            app.logger.warning(f'[fwd100] exception: {e}')
        return False

    def send_text_only(dst_ch, caption):
        """Отправляет текстовое сообщение. Возвращает True/False."""
        try:
            r = _req.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json={'chat_id': f'@{dst_ch}', 'text': caption[:4096],
                      'disable_web_page_preview': True},
                timeout=10)
            if r.ok:
                return True
            if r.status_code == 429:
                wait = r.json().get('parameters', {}).get('retry_after', 30)
                _time.sleep(wait + 1)
                r2 = _req.post(
                    f'https://api.telegram.org/bot{bot_token}/sendMessage',
                    json={'chat_id': f'@{dst_ch}', 'text': caption[:4096],
                          'disable_web_page_preview': True},
                    timeout=10)
                return r2.ok
        except Exception as e:
            app.logger.warning(f'[fwd100] text exception: {e}')
        return False

    def send_album(dst_ch, photo_bytes_list, caption):
        """Отправляет альбом (до 10 фото) + подпись на первом. Возвращает True/False."""
        import json as _json
        media = []
        files = {}
        for idx, pb in enumerate(photo_bytes_list[:10]):
            key = f'photo{idx}'
            item = {'type': 'photo', 'media': f'attach://{key}'}
            if idx == 0:
                item['caption'] = caption[:1024]
            media.append(item)
            files[key] = (f'{key}.jpg', pb, 'image/jpeg')
        try:
            r = _req.post(
                f'https://api.telegram.org/bot{bot_token}/sendMediaGroup',
                data={'chat_id': f'@{dst_ch}', 'media': _json.dumps(media)},
                files=files, timeout=60)
            if r.ok:
                return True
            if r.status_code == 429:
                wait = r.json().get('parameters', {}).get('retry_after', 30)
                _time.sleep(wait + 1)
                r2 = _req.post(
                    f'https://api.telegram.org/bot{bot_token}/sendMediaGroup',
                    data={'chat_id': f'@{dst_ch}', 'media': _json.dumps(media)},
                    files=files, timeout=60)
                return r2.ok
            app.logger.warning(f'[fwd100] album @{dst_ch}: {r.json().get("description","")}')
        except Exception as e:
            app.logger.warning(f'[fwd100] album exception: {e}')
        return False

    import re as _re_clean
    EMOJI_RE = _re_clean.compile(
        '['
        '\U0001F000-\U0001FFFF'
        '\U00002600-\U000027BF'
        '\U0000FE00-\U0000FE0F'
        '\U0000200B-\U0000200D'
        '\U00002060\U0000FEFF'
        '\U000000A9\U000000AE\U00002122\U00002139'
        '\U00002190-\U000021FF'
        '\U00002300-\U000023FF'
        '\U00002460-\U000024FF'
        '\U00002500-\U000025FF'
        '\U00002600-\U000026FF'
        '\U00002700-\U000027BF'
        '\U00002900-\U0000297F'
        '\U00002B00-\U00002BFF'
        '\U00003000-\U0000303F'
        '\U00003200-\U000032FF'
        '\U0000E000-\U0000F8FF'
        '\U000E0000-\U000E007F'
        ']+')
    HTML_RE = _re_clean.compile(r'<[^>]+>')

    def process_group(grp):
        dst_ch = DST[grp]
        channels = M[grp]
        sent = 0
        failed = 0

        _FWD100_STATE['results'][grp] = {'sent': 0, 'failed': 0, 'status': 'scraping'}
        app.logger.info(f'[fwd100] {grp}: скрапинг {len(channels)} каналов → @{dst_ch}')

        from concurrent.futures import ThreadPoolExecutor, as_completed
        all_msgs = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            futs = {pool.submit(scrape_last_100, ch): ch for ch in channels}
            for fut in as_completed(futs):
                ch = futs[fut]
                try:
                    msgs = fut.result()
                    for m in msgs:
                        m['_src_channel'] = ch
                    all_msgs.extend(msgs)
                except Exception as e:
                    app.logger.warning(f'[fwd100] scrape {ch}: {e}')

        to_send = all_msgs
        app.logger.info(f'[fwd100] {grp}: scraped {len(to_send)}, sending ALL → @{dst_ch}')
        _FWD100_STATE['results'][grp] = {'sent': 0, 'failed': 0, 'total': len(to_send), 'status': 'sending'}

        for i, msg in enumerate(to_send, 1):
            ch = msg.get('_src_channel', '?')
            raw_text = msg.get('text', '')
            clean = HTML_RE.sub('', raw_text)
            clean = EMOJI_RE.sub('', clean)
            clean = _re_clean.sub(r'[ ]{2,}', ' ', clean)
            clean = _re_clean.sub(r'\n{3,}', '\n\n', clean).strip()
            post_id = msg.get('post_id', '')
            tg_link = f'https://t.me/{ch}/{post_id}' if post_id else ''
            caption = (clean[:900] + f'\n\n{tg_link}').strip() if tg_link else clean[:1024]

            images = msg.get('images', [])
            cdn_images = [u for u in images if 'cdn' in u.lower() or 'telesco' in u.lower()]

            if not cdn_images:
                failed += 1
                _FWD100_STATE['failed'] += 1
                _FWD100_STATE['results'][grp] = {'sent': sent, 'failed': failed, 'total': len(to_send), 'pos': i, 'status': 'sending'}
                continue

            ok = False
            if len(cdn_images) == 1:
                pb = download_photo(cdn_images[0])
                if pb:
                    ok = send_with_photo(dst_ch, pb, caption)
            else:
                pbs = []
                for u in cdn_images[:10]:
                    pb = download_photo(u)
                    if pb:
                        pbs.append(pb)
                if pbs:
                    ok = send_album(dst_ch, pbs, caption)

            if ok:
                sent += 1
                _FWD100_STATE['sent'] += 1
            else:
                failed += 1
                _FWD100_STATE['failed'] += 1

            _FWD100_STATE['results'][grp] = {'sent': sent, 'failed': failed, 'total': len(to_send), 'pos': i, 'status': 'sending'}
            _time.sleep(3)

        _FWD100_STATE['results'][grp] = {'sent': sent, 'failed': failed, 'total': len(to_send), 'status': 'done'}
        app.logger.info(f'[fwd100] {grp} ИТОГО: sent={sent} failed={failed}')

    try:
        groups_to_run = only_groups or ['BIKE', 'VIET', 'THAI']
        import threading
        threads = []
        for grp in groups_to_run:
            t = threading.Thread(target=process_group, args=(grp,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    except Exception as e:
        _FWD100_STATE['error'] = str(e)
        app.logger.error(f'[fwd100] fatal: {e}')
    finally:
        _FWD100_STATE['running'] = False
        _FWD100_STATE['done'] = True
        _FWD100_STATE['current'] = 'ЗАВЕРШЕНО'


@app.route('/api/admin/forward-100', methods=['POST'])
def forward_100():
    if _FWD100_STATE['running']:
        return jsonify({'success': False, 'error': 'Уже запущено'})
    groups = request.json.get('groups') if request.is_json else None
    import threading
    threading.Thread(target=_run_forward_100, args=(groups,), daemon=True).start()
    label = ','.join(groups) if groups else 'ALL'
    return jsonify({'success': True, 'message': f'Запущен форвард [{label}] — /api/admin/forward-100-status'})


_FWDCUSTOM_STATE = {
    'running': False, 'done': False, 'sent': 0, 'failed': 0,
    'current': '', 'results': {}, 'error': None,
}

def _run_forward_custom(channels, dst_channel, limit_per_channel):
    import time as _time, requests as _req
    _FWDCUSTOM_STATE.update({
        'running': True, 'done': False, 'sent': 0, 'failed': 0,
        'current': 'init', 'results': {}, 'error': None,
    })

    bot_token = os.environ.get('VIETNAMPARSING_BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        _FWDCUSTOM_STATE.update({'running': False, 'done': True, 'error': 'no bot token'})
        return

    CDN_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Referer': 'https://t.me/',
    }

    try:
        from vietnamparsing_parser import scrape_extra_channel_page
    except Exception as e:
        _FWDCUSTOM_STATE.update({'running': False, 'done': True, 'error': str(e)})
        return

    def scrape_n(channel, n):
        all_msgs = []
        before_id = None
        for _ in range(n // 10 + 5):
            try:
                page = scrape_extra_channel_page(channel, before_id)
            except Exception:
                break
            if not page:
                break
            all_msgs.extend(page)
            if len(all_msgs) >= n:
                break
            ids = [m['post_id'] for m in page if m.get('post_id')]
            if not ids:
                break
            before_id = min(ids)
            _time.sleep(0.3)
        return all_msgs[:n]

    def download_photo(url):
        try:
            r = _req.get(url, headers=CDN_HEADERS, timeout=12)
            if r.ok and len(r.content) > 500:
                return r.content
        except Exception:
            pass
        return None

    def send_with_photo(dst_ch, photo_bytes, caption):
        try:
            r = _req.post(
                f'https://api.telegram.org/bot{bot_token}/sendPhoto',
                data={'chat_id': f'@{dst_ch}', 'caption': caption[:1024]},
                files={'photo': ('photo.jpg', photo_bytes, 'image/jpeg')},
                timeout=30)
            if r.ok:
                return True
            if r.status_code == 429:
                wait = r.json().get('parameters', {}).get('retry_after', 30)
                _time.sleep(wait + 1)
                r2 = _req.post(
                    f'https://api.telegram.org/bot{bot_token}/sendPhoto',
                    data={'chat_id': f'@{dst_ch}', 'caption': caption[:1024]},
                    files={'photo': ('photo.jpg', photo_bytes, 'image/jpeg')},
                    timeout=30)
                return r2.ok
        except Exception:
            pass
        return False

    def send_album(dst_ch, photo_bytes_list, caption):
        import json as _json
        media = []
        files = {}
        for idx, pb in enumerate(photo_bytes_list[:10]):
            key = f'photo{idx}'
            item = {'type': 'photo', 'media': f'attach://{key}'}
            if idx == 0:
                item['caption'] = caption[:1024]
            media.append(item)
            files[key] = (f'{key}.jpg', pb, 'image/jpeg')
        try:
            r = _req.post(
                f'https://api.telegram.org/bot{bot_token}/sendMediaGroup',
                data={'chat_id': f'@{dst_ch}', 'media': _json.dumps(media)},
                files=files, timeout=60)
            if r.ok:
                return True
            if r.status_code == 429:
                wait = r.json().get('parameters', {}).get('retry_after', 30)
                _time.sleep(wait + 1)
                r2 = _req.post(
                    f'https://api.telegram.org/bot{bot_token}/sendMediaGroup',
                    data={'chat_id': f'@{dst_ch}', 'media': _json.dumps(media)},
                    files=files, timeout=60)
                return r2.ok
        except Exception:
            pass
        return False

    import re as _re_clean
    EMOJI_RE = _re_clean.compile(
        '['
        '\U0001F000-\U0001FFFF'
        '\U00002600-\U000027BF'
        '\U0000FE00-\U0000FE0F'
        '\U0000200B-\U0000200D'
        '\U00002060\U0000FEFF'
        ']+')
    HTML_RE = _re_clean.compile(r'<[^>]+>')

    try:
        total_sent = 0
        total_failed = 0
        for ch in channels:
            _FWDCUSTOM_STATE['current'] = f'scraping @{ch}'
            _FWDCUSTOM_STATE['results'][ch] = {'sent': 0, 'failed': 0, 'status': 'scraping'}
            app.logger.info(f'[fwd-custom] scraping @{ch} (limit={limit_per_channel})')

            msgs = scrape_n(ch, limit_per_channel)
            app.logger.info(f'[fwd-custom] @{ch}: scraped {len(msgs)} posts')
            _FWDCUSTOM_STATE['results'][ch] = {'sent': 0, 'failed': 0, 'total': len(msgs), 'status': 'sending'}
            _FWDCUSTOM_STATE['current'] = f'sending @{ch}'

            ch_sent = 0
            ch_failed = 0
            for i, msg in enumerate(msgs, 1):
                raw_text = msg.get('text', '')
                clean = HTML_RE.sub('', raw_text)
                clean = EMOJI_RE.sub('', clean)
                clean = _re_clean.sub(r'[ ]{2,}', ' ', clean)
                clean = _re_clean.sub(r'\n{3,}', '\n\n', clean).strip()
                post_id = msg.get('post_id', '')
                tg_link = f'https://t.me/{ch}/{post_id}' if post_id else ''
                caption = (clean[:900] + f'\n\n{tg_link}').strip() if tg_link else clean[:1024]

                images = msg.get('images', [])
                cdn_images = [u for u in images if 'cdn' in u.lower() or 'telesco' in u.lower()]

                if not cdn_images:
                    ch_failed += 1
                    total_failed += 1
                    _FWDCUSTOM_STATE['failed'] = total_failed
                    _FWDCUSTOM_STATE['results'][ch] = {'sent': ch_sent, 'failed': ch_failed, 'total': len(msgs), 'pos': i, 'status': 'sending'}
                    continue

                ok = False
                if len(cdn_images) == 1:
                    pb = download_photo(cdn_images[0])
                    if pb:
                        ok = send_with_photo(dst_channel, pb, caption)
                else:
                    pbs = []
                    for u in cdn_images[:10]:
                        pb = download_photo(u)
                        if pb:
                            pbs.append(pb)
                    if pbs:
                        ok = send_album(dst_channel, pbs, caption)

                if ok:
                    ch_sent += 1
                    total_sent += 1
                    _FWDCUSTOM_STATE['sent'] = total_sent
                else:
                    ch_failed += 1
                    total_failed += 1
                    _FWDCUSTOM_STATE['failed'] = total_failed

                _FWDCUSTOM_STATE['results'][ch] = {'sent': ch_sent, 'failed': ch_failed, 'total': len(msgs), 'pos': i, 'status': 'sending'}
                _time.sleep(3)

            _FWDCUSTOM_STATE['results'][ch] = {'sent': ch_sent, 'failed': ch_failed, 'total': len(msgs), 'status': 'done'}
            app.logger.info(f'[fwd-custom] @{ch} DONE: sent={ch_sent} failed={ch_failed}')

    except Exception as e:
        _FWDCUSTOM_STATE['error'] = str(e)
        app.logger.error(f'[fwd-custom] fatal: {e}')
    finally:
        _FWDCUSTOM_STATE['running'] = False
        _FWDCUSTOM_STATE['done'] = True
        _FWDCUSTOM_STATE['current'] = 'ЗАВЕРШЕНО'


@app.route('/api/admin/forward-custom', methods=['POST'])
def forward_custom():
    if _FWDCUSTOM_STATE['running']:
        return jsonify({'success': False, 'error': 'Уже запущено'})
    data = request.json or {}
    channels = data.get('channels', [])
    dst = data.get('destination', 'vietnamparsing')
    limit = data.get('limit', 200)
    if not channels:
        return jsonify({'success': False, 'error': 'channels required'})
    import threading
    threading.Thread(target=_run_forward_custom, args=(channels, dst, limit), daemon=True).start()
    return jsonify({'success': True, 'message': f'Запущена пересылка {len(channels)} каналов (по {limit}) → @{dst}'})


@app.route('/api/admin/forward-custom-status', methods=['GET'])
def forward_custom_status():
    return jsonify({
        'success': True,
        'running': _FWDCUSTOM_STATE['running'],
        'done': _FWDCUSTOM_STATE['done'],
        'sent': _FWDCUSTOM_STATE['sent'],
        'failed': _FWDCUSTOM_STATE['failed'],
        'current': _FWDCUSTOM_STATE['current'],
        'results': _FWDCUSTOM_STATE['results'],
        'error': _FWDCUSTOM_STATE['error'],
    })


_TELETHON_FWD_STATE = {
    'running': False, 'done': False, 'sent': 0, 'failed': 0,
    'total': 0, 'current': '', 'error': None,
}

def _run_telethon_forward(source_channels, dst_channel, limit, min_photos=0, add_offset=0):
    import asyncio
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import MessageMediaPhoto
    import re as _re

    if isinstance(source_channels, str):
        source_channels = [source_channels]

    _TELETHON_FWD_STATE.update({
        'running': True, 'done': False, 'sent': 0, 'failed': 0,
        'total': 0, 'current': f'connecting', 'error': None,
        'channels': {ch: {'sent': 0, 'failed': 0, 'total': 0, 'status': 'pending'} for ch in source_channels},
    })

    sess_str = os.environ.get('TELETHON_SESSION', '')
    api_id = int(os.environ.get('TELETHON_API_ID', '32881984'))
    api_hash = os.environ.get('TELETHON_API_HASH', 'd2588f09dfbc5103ef77ef21c07dbf8b')

    if not sess_str:
        _TELETHON_FWD_STATE.update({'running': False, 'done': True, 'error': 'TELETHON_SESSION not set'})
        return

    EMOJI_RE = _re.compile('[\U0001F000-\U0001FFFF\U00002600-\U000027BF\U0000FE00-\U0000FE0F\U0000200B-\U0000200D]+')
    HTML_RE = _re.compile(r'<[^>]+>')

    async def do_forward():
        from telethon.errors import AuthKeyDuplicatedError
        client = TelegramClient(StringSession(sess_str), api_id, api_hash,
                                connection_retries=5, retry_delay=5,
                                device_model='Goldantelope Server',
                                system_version='1.0',
                                flood_sleep_threshold=60)
        try:
            await client.connect()
        except AuthKeyDuplicatedError:
            _TELETHON_FWD_STATE.update({'running': False, 'done': True,
                'error': 'AuthKeyDuplicated — сессия заблокирована. Нужно пересоздать TELETHON_SESSION'})
            return
        except Exception as e:
            _TELETHON_FWD_STATE.update({'running': False, 'done': True, 'error': f'Connect error: {e}'})
            return

        if not await client.is_user_authorized():
            _TELETHON_FWD_STATE.update({'running': False, 'done': True, 'error': 'Session invalid'})
            await client.disconnect()
            return

        me = await client.get_me()
        app.logger.info(f'[telethon-fwd] Authorized as {me.first_name} (id={me.id})')

        total_sent = 0
        total_failed = 0

        for source_channel in source_channels:
            _TELETHON_FWD_STATE['current'] = f'resolving @{source_channel}'
            _TELETHON_FWD_STATE['channels'][source_channel]['status'] = 'resolving'

            try:
                entity = await client.get_input_entity(source_channel)
            except Exception as e:
                app.logger.warning(f'[telethon-fwd] Cannot resolve @{source_channel}: {e}')
                _TELETHON_FWD_STATE['channels'][source_channel]['status'] = f'error: {e}'
                continue

            _TELETHON_FWD_STATE['current'] = f'fetching @{source_channel}'
            _TELETHON_FWD_STATE['channels'][source_channel]['status'] = 'fetching'
            app.logger.info(f'[telethon-fwd] Fetching {limit} messages from @{source_channel}')

            messages = []
            async for msg in client.iter_messages(entity, limit=limit, add_offset=add_offset):
                messages.append(msg)

            _TELETHON_FWD_STATE['channels'][source_channel]['total'] = len(messages)
            _TELETHON_FWD_STATE['total'] += len(messages)
            _TELETHON_FWD_STATE['channels'][source_channel]['status'] = 'sending'
            app.logger.info(f'[telethon-fwd] @{source_channel}: {len(messages)} msgs (offset={add_offset}), sending...')

            ch_sent = 0
            ch_failed = 0
            ch_dedup = 0
            grouped_done = set()
            sent_album_sizes = set()
            sent_single_sizes = set()
            target_photos = min_photos if min_photos > 0 else 999999

            def _photo_total_size(media):
                try:
                    if hasattr(media, 'photo') and hasattr(media.photo, 'sizes'):
                        return sum(getattr(s, 'size', 0) for s in media.photo.sizes if hasattr(s, 'size'))
                except Exception:
                    pass
                return 0

            for i, msg in enumerate(messages, 1):
                if ch_sent >= target_photos:
                    app.logger.info(f'[telethon-fwd] @{source_channel}: reached {target_photos} photos, stopping')
                    break

                _TELETHON_FWD_STATE['current'] = f'@{source_channel} {i}/{len(messages)} (sent {ch_sent}/{target_photos})'

                if msg.grouped_id and msg.grouped_id in grouped_done:
                    continue

                if msg.grouped_id:
                    album_msgs = [m for m in messages if m.grouped_id == msg.grouped_id]
                    photos = [m for m in album_msgs if isinstance(getattr(m, 'media', None), MessageMediaPhoto)]
                    if not photos:
                        ch_failed += 1
                        total_failed += 1
                        _TELETHON_FWD_STATE['failed'] = total_failed
                        _TELETHON_FWD_STATE['channels'][source_channel]['failed'] = ch_failed
                        continue
                    grouped_done.add(msg.grouped_id)

                    if len(photos) >= 2:
                        album_total = sum(_photo_total_size(m.media) for m in photos)
                        if album_total > 0:
                            size_key = (len(photos), album_total)
                            if size_key in sent_album_sizes:
                                ch_dedup += 1
                                app.logger.debug(f'[telethon-fwd] DEDUP album @{source_channel}/{msg.id}: {len(photos)} photos, size={album_total}')
                                continue
                            sent_album_sizes.add(size_key)
                    raw_text = ''
                    for am in album_msgs:
                        t = am.raw_text or am.text or ''
                        if t and len(t) > len(raw_text):
                            raw_text = t
                    clean = HTML_RE.sub('', raw_text)
                    clean = EMOJI_RE.sub('', clean)
                    clean = _re.sub(r'[ ]{2,}', ' ', clean)
                    clean = _re.sub(r'\n{3,}', '\n\n', clean).strip()
                    caption = f'{clean[:900]}\n\nhttps://t.me/{source_channel}/{msg.id}'.strip()
                    try:
                        await client.send_message(
                            dst_channel, caption[:1020],
                            file=[m.media for m in photos],
                            parse_mode=None
                        )
                        ch_sent += 1
                        total_sent += 1
                        _TELETHON_FWD_STATE['sent'] = total_sent
                        _TELETHON_FWD_STATE['channels'][source_channel]['sent'] = ch_sent
                    except Exception as e:
                        ch_failed += 1
                        total_failed += 1
                        _TELETHON_FWD_STATE['failed'] = total_failed
                        _TELETHON_FWD_STATE['channels'][source_channel]['failed'] = ch_failed
                        app.logger.warning(f'[telethon-fwd] Album error @{source_channel}: {e}')
                    await asyncio.sleep(3)
                    continue

                if not msg.media or not isinstance(msg.media, MessageMediaPhoto):
                    continue

                single_size = _photo_total_size(msg.media)
                if single_size > 0 and single_size in sent_single_sizes:
                    ch_dedup += 1
                    app.logger.debug(f'[telethon-fwd] DEDUP single @{source_channel}/{msg.id}: size={single_size}')
                    continue
                if single_size > 0:
                    sent_single_sizes.add(single_size)

                raw_text = msg.raw_text or msg.text or ''
                clean = HTML_RE.sub('', raw_text)
                clean = EMOJI_RE.sub('', clean)
                clean = _re.sub(r'[ ]{2,}', ' ', clean)
                clean = _re.sub(r'\n{3,}', '\n\n', clean).strip()
                caption = f'{clean[:900]}\n\nhttps://t.me/{source_channel}/{msg.id}'.strip()

                try:
                    await client.send_message(
                        dst_channel, caption[:1020],
                        file=msg.media, parse_mode=None
                    )
                    ch_sent += 1
                    total_sent += 1
                    _TELETHON_FWD_STATE['sent'] = total_sent
                    _TELETHON_FWD_STATE['channels'][source_channel]['sent'] = ch_sent
                except Exception as e:
                    ch_failed += 1
                    total_failed += 1
                    _TELETHON_FWD_STATE['failed'] = total_failed
                    _TELETHON_FWD_STATE['channels'][source_channel]['failed'] = ch_failed
                    app.logger.warning(f'[telethon-fwd] Send error @{source_channel}: {e}')

                await asyncio.sleep(3)

            _TELETHON_FWD_STATE['channels'][source_channel]['status'] = 'done'
            _TELETHON_FWD_STATE['channels'][source_channel]['dedup'] = ch_dedup
            app.logger.info(f'[telethon-fwd] @{source_channel} DONE: sent={ch_sent} failed={ch_failed} dedup={ch_dedup}')

        await client.disconnect()
        app.logger.info(f'[telethon-fwd] ALL DONE: sent={total_sent} failed={total_failed}')

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(do_forward())
    except Exception as e:
        _TELETHON_FWD_STATE['error'] = str(e)
        app.logger.error(f'[telethon-fwd] Fatal: {e}')
    finally:
        _TELETHON_FWD_STATE['running'] = False
        _TELETHON_FWD_STATE['done'] = True
        _TELETHON_FWD_STATE['current'] = 'ЗАВЕРШЕНО'
        loop.close()


_TELETHON_QUEUE = {'batches': [], 'current_batch': 0, 'running': False}

def _run_telethon_queue(batches):
    import time as _t
    _TELETHON_QUEUE['batches'] = batches
    _TELETHON_QUEUE['running'] = True
    for idx, batch in enumerate(batches):
        _TELETHON_QUEUE['current_batch'] = idx
        while _TELETHON_FWD_STATE.get('running'):
            _t.sleep(2)
        _run_telethon_forward(batch['sources'], batch['destination'], batch['limit'], batch.get('min_photos', 0))
        while _TELETHON_FWD_STATE.get('running'):
            _t.sleep(5)
        app.logger.info(f'[queue] Batch {idx+1}/{len(batches)} done: {batch["destination"]}')
    _TELETHON_QUEUE['running'] = False
    app.logger.info(f'[queue] ALL {len(batches)} batches complete')


@app.route('/api/admin/telethon-forward-queue', methods=['POST'])
def telethon_forward_queue():
    if _TELETHON_QUEUE.get('running') or _TELETHON_FWD_STATE.get('running'):
        return jsonify({'success': False, 'error': 'Already running'})
    data = request.json or {}
    batches = data.get('batches', [])
    if not batches:
        return jsonify({'success': False, 'error': 'batches required'})
    import threading
    threading.Thread(target=_run_telethon_queue, args=(batches,), daemon=True).start()
    return jsonify({'success': True, 'message': f'Queued {len(batches)} batches'})


@app.route('/api/admin/telethon-forward-queue-status', methods=['GET'])
def telethon_forward_queue_status():
    return jsonify({
        'queue_running': _TELETHON_QUEUE.get('running', False),
        'current_batch': _TELETHON_QUEUE.get('current_batch', 0),
        'total_batches': len(_TELETHON_QUEUE.get('batches', [])),
        'forward': {
            'running': _TELETHON_FWD_STATE.get('running'),
            'current': _TELETHON_FWD_STATE.get('current'),
            'sent': _TELETHON_FWD_STATE.get('sent'),
            'channels': _TELETHON_FWD_STATE.get('channels', {}),
        }
    })


@app.route('/api/admin/telethon-forward', methods=['POST'])
def telethon_forward():
    if _TELETHON_FWD_STATE['running']:
        return jsonify({'success': False, 'error': 'Already running'})
    data = request.json or {}
    source = data.get('source', '')
    sources = data.get('sources', [])
    if source and not sources:
        sources = [source]
    dst = data.get('destination', 'vietnamparsing')
    limit = data.get('limit', 200)
    min_photos = data.get('min_photos', 0)
    add_offset = data.get('add_offset', 0)
    if not sources:
        return jsonify({'success': False, 'error': 'source/sources required'})
    import threading
    threading.Thread(target=_run_telethon_forward, args=(sources, dst, limit, min_photos, add_offset), daemon=True).start()
    label = ', '.join(f'@{s}' for s in sources)
    return jsonify({'success': True, 'message': f'Telethon forwarding {limit} msgs each from {label} -> @{dst}'})


@app.route('/api/admin/telethon-forward-status', methods=['GET'])
def telethon_forward_status():
    return jsonify({
        'success': True,
        'running': _TELETHON_FWD_STATE['running'],
        'done': _TELETHON_FWD_STATE['done'],
        'sent': _TELETHON_FWD_STATE['sent'],
        'failed': _TELETHON_FWD_STATE['failed'],
        'total': _TELETHON_FWD_STATE['total'],
        'current': _TELETHON_FWD_STATE['current'],
        'channels': _TELETHON_FWD_STATE.get('channels', {}),
        'error': _TELETHON_FWD_STATE['error'],
    })


@app.route('/api/admin/forward-100-status', methods=['GET'])
def forward_100_status():
    return jsonify({
        'success': True,
        'running': _FWD100_STATE['running'],
        'done': _FWD100_STATE['done'],
        'sent': _FWD100_STATE['sent'],
        'failed': _FWD100_STATE['failed'],
        'current': _FWD100_STATE['current'],
        'results': _FWD100_STATE['results'],
        'error': _FWD100_STATE['error'],
    })


@app.route('/api/admin/fetch-empty-channels', methods=['POST'])
def fetch_empty_channels():
    if _FETCH_STATE['running']:
        return jsonify({'success': False, 'error': 'Уже запущено', 'state': _FETCH_STATE})
    import threading
    t = threading.Thread(target=_run_fetch_empty, daemon=True)
    t.start()
    return jsonify({'success': True, 'message': 'Запущено в фоне — используйте /api/admin/fetch-empty-status для отслеживания'})


@app.route('/api/admin/fetch-empty-status', methods=['GET'])
def fetch_empty_status():
    return jsonify({
        'success': True,
        'running': _FETCH_STATE['running'],
        'done': _FETCH_STATE['done'],
        'total': _FETCH_STATE['total'],
        'current': _FETCH_STATE['current'],
        'results': _FETCH_STATE['results'],
        'error': _FETCH_STATE['error'],
    })


# ============ VIETNAMPARSING PARSER INTEGRATION ============

@app.route('/api/admin/vietnamparsing-status', methods=['GET'])
def vietnamparsing_status():
    try:
        from vietnamparsing_parser import get_parser_state
        state = get_parser_state()
        return jsonify({'success': True, 'state': state})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/vietnamparsing-refresh', methods=['POST'])
def vietnamparsing_refresh():
    password = request.json.get('password', '') if request.is_json else request.form.get('password', '')
    is_valid, _ = check_admin_password(password, 'vietnam')
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        from vietnamparsing_parser import fetch_initial_200
        t = threading.Thread(target=fetch_initial_200, daemon=True)
        t.start()
        return jsonify({'success': True, 'message': 'Refresh started in background'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============ THAILAND HISTORY FETCH (Telethon) ============

_th_auth_state = {}  # phone, phone_code_hash, loop, client
TELETHON_SESSION = 'telegram_user_session'


def _get_telethon_creds():
    api_id = int(os.environ.get('TELETHON_API_ID', 0))
    api_hash = os.environ.get('TELETHON_API_HASH', '')
    return api_id, api_hash


def _run_async_in_thread(coro):
    """Run async coroutine in a dedicated thread with its own event loop."""
    import asyncio
    result_holder = [None]
    error_holder = [None]

    def _thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result_holder[0] = loop.run_until_complete(coro)
        except Exception as e:
            error_holder[0] = e
        finally:
            loop.close()

    t = threading.Thread(target=_thread)
    t.start()
    t.join(timeout=30)
    if error_holder[0]:
        raise error_holder[0]
    return result_holder[0]


@app.route('/api/admin/thailand-auth-start', methods=['POST'])
def thailand_auth_start():
    global _th_auth_state
    data_req = request.json or {}
    password = data_req.get('password', '')
    phone = data_req.get('phone', '').strip()
    is_valid, _ = check_admin_password(password)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if not phone:
        return jsonify({'error': 'Phone number required'}), 400

    api_id, api_hash = _get_telethon_creds()
    if not api_id or not api_hash:
        return jsonify({'error': 'TELETHON_API_ID / TELETHON_API_HASH not set'}), 500

    # Delete invalid session if exists (unauthenticated)
    session_path = TELETHON_SESSION + '.session'
    if os.path.exists(session_path):
        os.remove(session_path)

    from telethon import TelegramClient

    async def _send_code():
        client = TelegramClient(TELETHON_SESSION, api_id, api_hash)
        await client.connect()
        result = await client.send_code_request(phone)
        await client.disconnect()
        return result.phone_code_hash

    try:
        phone_code_hash = _run_async_in_thread(_send_code())
        _th_auth_state['phone'] = phone
        _th_auth_state['phone_code_hash'] = phone_code_hash
        return jsonify({'success': True, 'message': f'Код отправлен на {phone}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/thailand-auth-verify', methods=['POST'])
def thailand_auth_verify():
    global _th_auth_state
    data_req = request.json or {}
    password = data_req.get('password', '')
    code = data_req.get('code', '').strip()
    is_valid, _ = check_admin_password(password)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if not code:
        return jsonify({'error': 'Code required'}), 400
    if not _th_auth_state.get('phone_code_hash'):
        return jsonify({'error': 'Сначала запросите код (Шаг 1)'}), 400

    api_id, api_hash = _get_telethon_creds()
    phone = _th_auth_state['phone']
    phone_code_hash = _th_auth_state['phone_code_hash']

    from telethon import TelegramClient

    async def _sign_in():
        client = TelegramClient(TELETHON_SESSION, api_id, api_hash)
        await client.connect()
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        me = await client.get_me()
        await client.disconnect()
        return me.first_name, me.username

    try:
        first_name, username = _run_async_in_thread(_sign_in())
        _th_auth_state.clear()
        return jsonify({'success': True, 'message': f'Авторизован как {first_name} (@{username})'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- StringSession Generator for Globalparsing HF Space ---
_gp_auth_state = {}
_GP_API_ID = 32881984
_GP_API_HASH = 'd2588f09dfbc5103ef77ef21c07dbf8b'


@app.route('/api/admin/gen-session-start', methods=['POST'])
def gen_session_start():
    global _gp_auth_state
    data_req = request.json or {}
    password = data_req.get('password', '')
    phone = data_req.get('phone', '').strip()
    is_valid, _ = check_admin_password(password)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if not phone:
        return jsonify({'error': 'Введите номер телефона'}), 400

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    async def _send():
        client = TelegramClient(StringSession(), _GP_API_ID, _GP_API_HASH)
        await client.connect()
        result = await client.send_code_request(phone)
        session_str = client.session.save()
        await client.disconnect()
        return result.phone_code_hash, session_str

    try:
        phone_code_hash, session_str = _run_async_in_thread(_send())
        _gp_auth_state['phone'] = phone
        _gp_auth_state['phone_code_hash'] = phone_code_hash
        _gp_auth_state['session_str'] = session_str
        return jsonify({'success': True, 'message': f'Код отправлен на {phone}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/gen-session-verify', methods=['POST'])
def gen_session_verify():
    global _gp_auth_state
    data_req = request.json or {}
    password = data_req.get('password', '')
    code = data_req.get('code', '').strip()
    is_valid, _ = check_admin_password(password)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401
    if not code:
        return jsonify({'error': 'Введите код'}), 400
    if not _gp_auth_state.get('phone_code_hash'):
        return jsonify({'error': 'Сначала запросите код (шаг 1)'}), 400

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    phone = _gp_auth_state['phone']
    phone_code_hash = _gp_auth_state['phone_code_hash']
    session_str = _gp_auth_state['session_str']

    async def _verify():
        client = TelegramClient(StringSession(session_str), _GP_API_ID, _GP_API_HASH)
        await client.connect()
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        me = await client.get_me()
        final_session = client.session.save()
        await client.disconnect()
        return me.first_name, me.username, final_session

    try:
        first_name, username, final_session = _run_async_in_thread(_verify())
        _gp_auth_state.clear()
        return jsonify({
            'success': True,
            'message': f'Авторизован как {first_name} (@{username})',
            'session': final_session
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/gen-session')
def gen_session_page():
    return '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Генератор сессии Globalparsing</title>
<style>body{font-family:sans-serif;max-width:500px;margin:40px auto;padding:20px}
input{width:100%;padding:10px;margin:8px 0;box-sizing:border-box;border:1px solid #ccc;border-radius:4px}
button{background:#2563eb;color:#fff;border:none;padding:12px 24px;border-radius:4px;cursor:pointer;width:100%}
.result{background:#f0fdf4;border:1px solid #86efac;padding:16px;border-radius:4px;margin-top:16px;word-break:break-all}
.error{background:#fef2f2;border:1px solid #fca5a5;padding:16px;border-radius:4px;margin-top:16px}
</style></head><body>
<h2>🔑 Генератор Telethon сессии</h2>
<p>Для деплоя <b>Globalparsing</b> на HuggingFace Space</p>
<input id="pwd" type="password" placeholder="Пароль администратора">
<hr>
<h3>Шаг 1: Запросить код</h3>
<input id="phone" type="text" placeholder="Номер телефона (+79...)" value="+">
<button onclick="step1()">Отправить код</button>
<div id="msg1"></div>
<h3>Шаг 2: Подтвердить код</h3>
<input id="code" type="text" placeholder="Код из Telegram">
<button onclick="step2()">Получить сессию</button>
<div id="msg2"></div>
<script>
async function step1(){
    const r=await fetch('/api/admin/gen-session-start',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({password:document.getElementById('pwd').value,phone:document.getElementById('phone').value})});
    const d=await r.json();
    document.getElementById('msg1').innerHTML=d.success?'<div class="result">✅ '+d.message+'</div>':'<div class="error">❌ '+d.error+'</div>';
}
async function step2(){
    const r=await fetch('/api/admin/gen-session-verify',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({password:document.getElementById('pwd').value,code:document.getElementById('code').value})});
    const d=await r.json();
    if(d.success){
        document.getElementById('msg2').innerHTML='<div class="result"><b>✅ '+d.message+'</b><br><br><b>TELETHON_SESSION:</b><br><code>'+d.session+'</code><br><br>Скопируйте это значение и добавьте в секреты HuggingFace Space!</div>';
    } else {
        document.getElementById('msg2').innerHTML='<div class="error">❌ '+d.error+'</div>';
    }
}
</script></body></html>'''


@app.route('/api/admin/thailand-fetch-history', methods=['POST'])
def thailand_fetch_history():
    data_req = request.json or {}
    password = data_req.get('password', '')
    is_valid, _ = check_admin_password(password)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401

    session_path = TELETHON_SESSION + '.session'
    if not os.path.exists(session_path):
        return jsonify({'error': 'Сессия не найдена. Сначала авторизуйтесь через Шаг 1 и 2.'}), 400

    # Verify it's an authenticated user session
    api_id, api_hash = _get_telethon_creds()

    async def _check_auth():
        from telethon import TelegramClient
        client = TelegramClient(TELETHON_SESSION, api_id, api_hash)
        await client.connect()
        authorized = await client.is_user_authorized()
        await client.disconnect()
        return authorized

    try:
        authorized = _run_async_in_thread(_check_auth())
    except Exception:
        authorized = False

    if not authorized:
        # Remove invalid session
        if os.path.exists(session_path):
            os.remove(session_path)
        return jsonify({'error': 'Сессия не авторизована. Авторизуйтесь заново.'}), 400

    def _run():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_fetch_history_telethon())
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True, name='TelethonHistoryFetch')
    t.start()
    return jsonify({'success': True, 'message': 'Загрузка истории запущена в фоне. Следите за логами сервера.'})


@app.route('/api/admin/thailand-fetch-photos', methods=['POST'])
def thailand_fetch_photos():
    data_req = request.json or {}
    password = data_req.get('password', '')
    is_valid, _ = check_admin_password(password)
    if not is_valid:
        return jsonify({'error': 'Unauthorized'}), 401

    session_path = TELETHON_SESSION + '.session'
    if not os.path.exists(session_path):
        return jsonify({'error': 'Нет сессии. Авторизуйтесь сначала.'}), 400

    def _run():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_download_photos_telethon())
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True, name='TelethonPhotoFetch')
    t.start()
    return jsonify({'success': True, 'message': 'Загрузка фото запущена в фоне.'})


async def _download_photos_telethon():
    """
    Fetch Telegram CDN photo URLs for Thailand listings by scraping og:image
    from source channel posts referenced in listing texts.
    No files are downloaded — only Telegram CDN URLs are stored.
    """
    import asyncio
    import time
    import requests as req
    import re as re_mod
    from thailandparsing_parser import load_listings, save_listings

    TG_URL_RE = re_mod.compile(r'https?://t\.me/([^/\s]+)/(\d+)')
    OG_IMG_RE = re_mod.compile(
        r'<meta\s+property=["\']og:image["\']\s+content=["\'](.*?)["\']', re_mod.IGNORECASE
    )
    HEADS = {'User-Agent': 'Mozilla/5.0 (compatible; TelegramBot/1.0)'}

    def scrape_og_image(channel: str, post_id: str) -> str | None:
        try:
            r = req.get(f'https://t.me/{channel}/{post_id}', headers=HEADS, timeout=10)
            if r.status_code != 200:
                return None
            m = OG_IMG_RE.search(r.text)
            if m:
                url = m.group(1).strip()
                if url and ('cdn' in url or 'telesco.pe' in url):
                    return url
        except Exception:
            pass
        return None

    def cleanup_local_files():
        photos_dir = 'static/images/thailand'
        if not os.path.isdir(photos_dir):
            return
        for fname in os.listdir(photos_dir):
            if fname.endswith('.jpg'):
                try:
                    os.remove(os.path.join(photos_dir, fname))
                except Exception:
                    pass

    try:
        data = load_listings()
        items = data.get('real_estate', [])

        # Clear stale local /static/ URLs and remove local files
        for item in items:
            url = item.get('image_url', '')
            if url and url.startswith('/static/images/thailand/'):
                item['image_url'] = ''
                item['photos'] = []
                item['all_images'] = []
        cleanup_local_files()

        # Collect items needing photo URL from source channel link
        need_photos = [
            item for item in items
            if not item.get('image_url')
            and TG_URL_RE.search(item.get('text', ''))
        ]
        logger.info(f'[TH Photos] Scraping og:image for {len(need_photos)} listings')

        photo_count = 0
        save_batch = 0

        for item in need_photos:
            text = item.get('text', '')
            m = TG_URL_RE.search(text)
            if not m:
                continue
            channel, post_id = m.group(1), m.group(2)

            img_url = await asyncio.get_event_loop().run_in_executor(
                None, scrape_og_image, channel, post_id
            )
            if img_url:
                item['image_url'] = img_url
                item['photos'] = [img_url]
                item['all_images'] = [img_url]
                photo_count += 1
                save_batch += 1

            if save_batch >= 50:
                save_listings(data)
                save_batch = 0
                logger.info(f'[TH Photos] Saved. Photo URLs so far: {photo_count}')

            await asyncio.sleep(0.3)

        save_listings(data)
        logger.info(f'[TH Photos] Done. Got {photo_count} Telegram CDN photo URLs.')
        return photo_count

    except Exception as e:
        logger.error(f'[TH Photos] Error: {e}', exc_info=True)
        return 0


async def _fetch_history_telethon():
    import asyncio
    from telethon import TelegramClient
    from telethon.tl.types import Message as TLMessage
    from thailandparsing_parser import (
        load_listings, get_existing_ids, save_listings,
        is_spam, extract_price, detect_city, detect_listing_type,
        extract_title_th, extract_source, SOURCE_CHANNEL
    )
    from datetime import timezone

    api_id, api_hash = _get_telethon_creds()
    client = TelegramClient(TELETHON_SESSION, api_id, api_hash)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.error('[TH Telethon] Session not authorized!')
            return

        me = await client.get_me()
        logger.info(f'[TH Telethon] Connected as {me.first_name} (@{me.username})')

        data = load_listings()
        existing_ids = get_existing_ids(data)
        if 'real_estate' not in data:
            data['real_estate'] = []

        existing_nums = set()
        for eid in existing_ids:
            if eid.startswith('thailand_'):
                try:
                    existing_nums.add(int(eid.split('_')[1]))
                except ValueError:
                    pass

        new_count = 0
        offset_id = 0
        batch_size = 200

        while True:
            batch = await client.get_messages(SOURCE_CHANNEL, limit=batch_size, offset_id=offset_id)
            if not batch:
                break
            real_msgs = [m for m in batch if isinstance(m, TLMessage)]
            if not real_msgs:
                break

            for msg in real_msgs:
                if msg.id in existing_nums:
                    continue
                text = (msg.text or msg.message or '') if hasattr(msg, 'text') else ''
                if not text or len(text) < 20 or is_spam(text):
                    continue

                # Запрет: недвижимость без фото не добавляем
                has_photo = bool(msg.photo or (hasattr(msg, 'media') and msg.media and hasattr(msg.media, 'photo') and msg.media.photo))
                if not has_photo:
                    continue

                item_id = f'thailand_{msg.id}'
                price_val, price_display = extract_price(text)
                city = detect_city(text)
                listing_type = detect_listing_type(text)
                title = extract_title_th(text)
                source = extract_source(text)
                telegram_link = f'https://t.me/{SOURCE_CHANNEL}/{msg.id}'
                tg_m = re.search(r'https?://t\.me/\S+', text)
                if tg_m:
                    telegram_link = tg_m.group(0)
                date_str = msg.date.astimezone(timezone.utc).isoformat() if msg.date else datetime.now(timezone.utc).isoformat()

                item = {
                    'id': item_id,
                    'title': title,
                    'description': text[:500],
                    'text': text,
                    'price': price_val,
                    'price_display': price_display,
                    'city': city,
                    'listing_type': listing_type,
                    'contact': source,
                    'telegram_link': telegram_link,
                    'photos': [],
                    'image_url': '',
                    'all_images': [],
                    'date': date_str,
                    'source': 'telegram',
                    'channel': SOURCE_CHANNEL,
                }
                data['real_estate'].append(item)
                existing_nums.add(msg.id)
                existing_ids.add(item_id)
                new_count += 1

            oldest = min(m.id for m in real_msgs)
            logger.info(f'[TH Telethon] Batch: {len(real_msgs)} msgs, oldest_id={oldest}, new={new_count}')
            offset_id = oldest
            if len(batch) < batch_size:
                break
            await asyncio.sleep(0.5)

        data['real_estate'].sort(key=lambda x: x.get('date', ''), reverse=True)
        save_listings(data)
        logger.info(f'[TH Telethon] Done. Added {new_count} new listings. Total: {len(data["real_estate"])}')

    except Exception as e:
        logger.error(f'[TH Telethon] Error: {e}', exc_info=True)
    finally:
        await client.disconnect()


def _start_vietnamparsing_parser():
    try:
        import vietnamparsing_parser as _vp
        # Отключаем собственный getUpdates — polling делает _gavibeshub_poller (устраняет 409)
        _vp._external_poll_mode = True
        _vp.start_parser_in_background()
        print("[vietnamparsing] Parser background thread started (external poll mode).")
    except Exception as e:
        print(f"[vietnamparsing] Could not start parser: {e}")


_vp_started = False

def _ensure_parser_started():
    global _vp_started
    if not _vp_started:
        _vp_started = True
        def _delayed_start():
            time.sleep(8)
            _start_vietnamparsing_parser()
        threading.Thread(target=_delayed_start, daemon=True, name='VPParserLauncher').start()


_ensure_parser_started()


def _start_chat_parser_background():
    """Запускает chat_parser в фоновом потоке с периодическим повтором каждые 30 минут."""
    import asyncio as _asyncio

    def _run_loop():
        while True:
            try:
                loop = _asyncio.new_event_loop()
                _asyncio.set_event_loop(loop)
                from chat_parser import parse_chats
                loop.run_until_complete(parse_chats())
                loop.close()
                print("[chat_parser] Цикл завершён, следующий запуск через 30 мин.")
            except Exception as _e:
                print(f"[chat_parser] Ошибка: {_e}")
            time.sleep(1800)  # 30 минут

    t = threading.Thread(target=_run_loop, daemon=True, name='ChatParserLoop')
    t.start()
    print("[chat_parser] Фоновый парсер чатов запущен.")


def _ensure_chat_parser_started():
    # Не дублировать — проверяем, не запущен ли уже
    for t in threading.enumerate():
        if t.name in ('ChatParserLoop', 'ChatParserLauncher'):
            return
    if os.path.exists('goldantelope_user.session') and os.environ.get('TELETHON_API_ID'):
        def _delayed():
            time.sleep(15)
            _start_chat_parser_background()
        threading.Thread(target=_delayed, daemon=True, name='ChatParserLauncher').start()
    else:
        print("[chat_parser] Сессия или API ключи не найдены — парсер чатов не запущен.")


_ensure_chat_parser_started()




# ──── Telethon Forwarder (отключён — конфликтует с /api/admin/telethon-forward) ────
# def _start_telethon_forwarder():
#     time.sleep(12)
#     sess = os.environ.get('TELETHON_SESSION', '')
#     if not sess:
#         logger.info('TELETHON_SESSION не задана — Telethon forwarder не запущен')
#         return
#     try:
#         from telethon_forwarder import start_forwarder
#         start_forwarder(sess)
#         logger.info('Telethon forwarder запущен')
#     except Exception as e:
#         logger.error(f'Ошибка запуска Telethon forwarder: {e}')
# threading.Thread(target=_start_telethon_forwarder, daemon=True, name='TelethonForwarder').start()
logger.info('Telethon background forwarder отключён (используйте /api/admin/telethon-forward)')


_chatiparsing_cache = {'data': [], 'ts': 0}

CHAT_HISTORY_FILE = 'chat_history.json'
_CHAT_HISTORY_MAX = 100

def _load_chat_history():
    try:
        with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def _save_chat_history(messages):
    try:
        seen = set()
        unique = []
        for m in messages:
            key = (m.get('description', '') or '')[:80] + '|' + (m.get('date', '') or '')
            if key not in seen:
                seen.add(key)
                unique.append(m)
        unique.sort(key=lambda x: x.get('date', '') or '', reverse=True)
        unique = unique[:_CHAT_HISTORY_MAX]
        with open(CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(unique, f, ensure_ascii=False)
    except Exception as e:
        print(f'[chat_history] save error: {e}')

try:
    _chatiparsing_cache['data'] = _load_chat_history()
    if _chatiparsing_cache['data']:
        print(f'[chat_history] Загружено {len(_chatiparsing_cache["data"])} сообщений из истории')
except:
    pass

_SPAM_WORDS = [
    'казино', 'casino', 'покер', 'poker', 'ставки', 'betting', 'bet365',
    'слот', 'slot', 'джекпот', 'jackpot', 'рулетк', 'roulette', 'букмекер',
    'порно', 'porn', 'xxx', 'секс видео', 'sex video', 'onlyfans', 'эскорт', 'escort',
    'интим', 'intimate', 'проститу', 'prostitu', 'массаж 18', 'happy ending',
    'anal', 'blowjob', 'минет', 'шлюх', 'досуг для взрослых',
    'купить usdt', 'buy usdt', 'продам usdt', 'sell usdt',
    'купить крипт', 'buy crypto', 'обмен крипт', 'crypto exchange',
    'обмен usdt', 'p2p обмен', 'p2p exchange', 'купить btc', 'buy btc',
    'продам btc', 'sell btc', 'купить биткоин', 'buy bitcoin',
    'обнал', 'отмыв', 'дроп', 'drop card', 'кардинг', 'carding',
    'закладк', 'кладмен', 'наркот', 'drug', 'weed', 'cocaine', 'heroin',
    'мефедрон', 'mephedrone', 'амфетамин', 'amphetamine', 'марихуан', 'marijuana',
    'купить гашиш', 'mdma', 'экстази', 'ecstasy',
    'фейк паспорт', 'fake passport', 'fake id', 'поддельн',
    'схема заработк', 'лёгкий заработок', 'easy money', 'quick money',
    'пирамид', 'pyramid', 'понци', 'ponzi', 'mlm схем',
    'взлом', 'hack', 'ddos', 'брут', 'brute',
    'пробив', 'деанон', 'doxxing',
    'оружи', 'weapon', 'gun', 'пистолет', 'автомат',
    'telegram бот заработ', 'заработок в телеграм',
    'i want anal', 'looking for sex', 'ищу секс',
    'реклама', 'pеклама', 'peклама', 'рeклама', 'peкламa',
    'р е к л а м а', 'реклам',
    'крипта', 'криптовалют', 'crypto', 'cryptocurrency',
    'usd', 'usdt', 'usdc', 'tether', 'busd',
    'мошенник', 'мошенниц', 'scam', 'scammer',
    'рассылк', 'рассылка', 'paccылк',
    'документ', 'document',
    'ищу работ', 'ищем работ', 'ищу робот', 'ищем робот',
    'блокировк', 'блокировка',
    'массаж', 'massage',
    'бьюти-мастер', 'бьюти мастер', 'косметолог', 'дерматокосметолог',
    'лифтинг нефертити', 'ботокс', 'botox', 'филлер', 'filler',
    'перманентный макияж', 'татуаж', 'наращивание ресниц', 'маникюр педикюр',
    'для получения клиентов', 'разместить сообщение', 'размещение рекламы',
    'продвижение в чатах', 'рекламные услуги', 'привлечение клиентов',
    'запусти свою рекламу', 'запустить рекламу', 'свяжись с нами',
    'реклама в чате', 'реклама в нашем чате', 'рекламируй', 'рекламируйте',
    'разместить объявление', 'размести объявление', 'размещение объявлений',
    # Офтопик — еда, трансфер, валютный обмен не по теме
    'клубника', 'strawberry', 'свежая ягода', 'фрукт свеж',
    'трансфер из/в аэропорт', 'трансфер аэропорт', 'transfer airport',
    'аэропорт камрань', 'camranh airport', 'cam ranh airport',
    'русского информационного', 'russian information center',
    'быстрым обменом валют', 'обмен валют в нячанге',
    'помогаем с обменом', 'обменяем валюту',
    'поможем с обменом', 'помощь с обменом',
]

import re as _re
_LATIN_RE = _re.compile(r'[a-zA-Z]')
_CYRILLIC_RE = _re.compile(r'[а-яА-ЯёЁ]')

def _is_mostly_english(text):
    if not text:
        return False
    latin_count = len(_LATIN_RE.findall(text))
    cyrillic_count = len(_CYRILLIC_RE.findall(text))
    total_letters = latin_count + cyrillic_count
    if total_letters < 10:
        return False
    return latin_count > cyrillic_count * 3

def _is_link_only(text):
    if not text:
        return True
    cleaned = re.sub(r'https?://\S+', '', text)
    cleaned = re.sub(r'@[\w]+', '', cleaned)
    cleaned = re.sub(r'[^\w]', '', cleaned)
    return len(cleaned) < 20

def _is_spam(text):
    if not text:
        return False
    if _is_link_only(text):
        return True
    t = text.lower()
    for word in _SPAM_WORDS:
        if word in t:
            return True
    if _is_mostly_english(text):
        return True
    return False

def _bg_chatiparsing_poller():
    """Фоновый поллер chatiparsing — обновляет кэш каждые 5 сек"""
    import time as _t
    from bs4 import BeautifulSoup
    while True:
        try:
            resp = requests.get('https://t.me/s/chatiparsing', timeout=8,
                                headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(resp.text, 'html.parser')
            result = []
            for wrap in soup.find_all('div', class_='tgme_widget_message_wrap'):
                text_el = wrap.find('div', class_='tgme_widget_message_text')
                date_el = wrap.find('time')
                link_el = wrap.find('a', class_='tgme_widget_message_date')
                if not text_el:
                    continue
                raw_text = text_el.get_text('\n', strip=True)
                tg_links = re.findall(r'https://t\.me/([\w_]+)/(\d+)', raw_text)
                src_channel = tg_links[-1][0] if tg_links else ''
                src_link = f'https://t.me/{tg_links[-1][0]}/{tg_links[-1][1]}' if tg_links else ''
                display = re.sub(r'https://t\.me/\S+', '', raw_text).strip()
                result.append({
                    'text': display,
                    'description': display,
                    'title': display[:60],
                    'src_channel': src_channel,
                    'source_channel': f'@{src_channel}' if src_channel else '',
                    'src_link': src_link,
                    'tg_link': src_link,
                    'msg_link': link_el.get('href', '') if link_el else '',
                    'date': date_el.get('datetime', '') if date_el else '',
                    'category': 'chat',
                })
            result = [m for m in result if not _is_spam(m.get('text', '') or m.get('description', ''))]
            result.sort(key=lambda x: x['date'])
            old_history = _load_chat_history()
            merged = old_history + result
            _save_chat_history(merged)
            _chatiparsing_cache['data'] = _load_chat_history()
            _chatiparsing_cache['ts'] = _t.time()
        except Exception as e:
            print(f'[chatiparsing bg] error: {e}')
        _t.sleep(5)

import threading
threading.Thread(target=_bg_chatiparsing_poller, daemon=True, name='ChatiparsingPoller').start()
logger.info('Chatiparsing background poller started (every 5s)')

# ─── Автоочистка Индия/entertainment: удаляем события вне окна 14 дней ──────
def _india_entertainment_cleanup():
    """Каждые 6 часов удаляет из listings_india.json[entertainment]
    события, которые уже прошли или начнутся позже чем через 14 дней."""
    import time as _time
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    INTERVAL = 6 * 3600
    INDIA_FILE = os.path.join(os.path.dirname(__file__), 'listings_india.json')
    _first = True
    while True:
        _time.sleep(120 if _first else INTERVAL)  # при первом запуске ждём 2 мин (после поллера), далее каждые 6ч
        _first = False
        try:
            with open(INDIA_FILE) as _f:
                _data = json.load(_f)
            _ent = _data.get('entertainment', [])
            _now = _dt.now(_tz.utc)
            _cutoff = _now + _td(days=14)
            _kept = []
            _removed = 0
            for _item in _ent:
                _d_str = _item.get('date')
                if not _d_str:
                    _kept.append(_item)
                    continue
                try:
                    _d = _dt.fromisoformat(str(_d_str).replace(' ', 'T'))
                    if _d.tzinfo is None:
                        _d = _d.replace(tzinfo=_tz.utc)
                    if _now <= _d <= _cutoff:
                        _kept.append(_item)
                    else:
                        _removed += 1
                except Exception:
                    _kept.append(_item)
            if _removed > 0:
                _data['entertainment'] = _kept
                with open(INDIA_FILE, 'w') as _f:
                    json.dump(_data, _f, ensure_ascii=False, indent=2)
                logger.info('[india_ent_cleanup] Removed %d events outside 14-day window, kept %d', _removed, len(_kept))
        except Exception as _e:
            logger.warning('[india_ent_cleanup] Error: %s', _e)
        _time.sleep(INTERVAL)

threading.Thread(target=_india_entertainment_cleanup, daemon=True, name='IndiaEntCleanup').start()
logger.info('[india_ent_cleanup] Auto-cleanup started (every 6h, window=14d)')

@app.route('/api/chatiparsing/feed')
def chatiparsing_feed():
    """Живая лента из канала chatiparsing (кэш 60 с)"""
    import time as _time
    now = _time.time()
    if now - _chatiparsing_cache['ts'] < 5 and _chatiparsing_cache['data']:
        return jsonify(_chatiparsing_cache['data'])
    try:
        from bs4 import BeautifulSoup
        import re as _re
        resp = requests.get(
            'https://t.me/s/chatiparsing', timeout=12,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        soup = BeautifulSoup(resp.text, 'html.parser')
        result = []
        for wrap in soup.find_all('div', class_='tgme_widget_message_wrap'):
            text_el = wrap.find('div', class_='tgme_widget_message_text')
            date_el = wrap.find('time')
            link_el = wrap.find('a', class_='tgme_widget_message_date')
            if not text_el:
                continue
            raw_text = text_el.get_text('\n', strip=True)
            # Вытаскиваем ссылку на источник (последняя t.me-ссылка в тексте)
            tg_links = _re.findall(r'https://t\.me/([\w_]+)/(\d+)', raw_text)
            src_channel = tg_links[-1][0] if tg_links else ''
            src_link = f'https://t.me/{tg_links[-1][0]}/{tg_links[-1][1]}' if tg_links else ''
            # Убираем ссылку из текста отображения
            display = _re.sub(r'https://t\.me/\S+', '', raw_text).strip()
            result.append({
                'text': display,
                'src_channel': src_channel,
                'src_link': src_link,
                'msg_link': link_el.get('href', '') if link_el else '',
                'date': date_el.get('datetime', '') if date_el else '',
            })
        result.sort(key=lambda x: x['date'])
        _chatiparsing_cache['data'] = result
        _chatiparsing_cache['ts'] = now
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat-stats')
def chat_stats_local():
    """Статистика по каналам из локального listings_chat.json"""
    try:
        listings_file = 'listings_chat.json'
        if not os.path.exists(listings_file):
            return jsonify({'total': 0, 'per_channel': {}, 'channels': [], 'status': 'no_data'})
        with open(listings_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, list):
            return jsonify({'total': 0, 'per_channel': {}, 'channels': [], 'status': 'no_data'})
        from collections import Counter
        per_channel = dict(Counter(
            item.get('source_channel', '?').lstrip('@') for item in data if isinstance(item, dict)
        ))
        # Сортируем по кол-ву (убыв.)
        sorted_ch = sorted(per_channel.items(), key=lambda x: x[1], reverse=True)
        return jsonify({
            'total': len(data),
            'per_channel': per_channel,
            'channels': [{'channel': ch, 'count': cnt} for ch, cnt in sorted_ch],
            'status': 'ok'
        })
    except Exception as e:
        return jsonify({'error': str(e), 'status': 'error'}), 500


@app.route('/api/hf-stats')
def hf_space_stats():
    import requests as _req
    HF_URL = 'https://poweramanita-a.hf.space/status'
    try:
        r = _req.get(HF_URL, timeout=35)
        r.raise_for_status()
        data = r.json()
        try:
            import telethon_parser as _tp
            chat_per_ch = dict(_tp.STATS.get('per_channel', {}))
            if chat_per_ch:
                data['chat_per_channel'] = chat_per_ch
                data['chat_forwarded'] = _tp.STATS.get('forwarded', 0)
                data['chat_started_at'] = _tp.STATS.get('started_at')
                data['chat_user'] = _tp.STATS.get('user')
        except Exception:
            pass
        return jsonify(data)
    except _req.exceptions.Timeout:
        return jsonify({'error': 'timeout', 'message': 'HF Space не отвечает (спит или перезапускается). Попробуйте через 1-2 минуты.'}), 504
    except _req.exceptions.ConnectionError:
        return jsonify({'error': 'connection', 'message': 'Нет соединения с HF Space.'}), 502
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/telethon/stats')
def telethon_stats():
    try:
        from telethon_forwarder import STATS
        return jsonify(STATS)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


_poster_thread = None
_poster_status = {'running': False, 'posted': 0, 'total': 0, 'last': ''}


def _run_restaurant_poster():
    import json as _json
    import re as _re
    import time as _time
    global _poster_status

    CHANNEL = '@restoranvietnam'
    PROGRESS_FILE = 'post_progress.json'
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not bot_token:
        _poster_status['running'] = False
        return

    def _clean(title):
        t = _re.sub(r'^[\U0001F300-\U0001FFFF\u2600-\u26FF\u2700-\u27BF\s]+', '', title)
        t = _re.sub(r'^РЕСТОРАН:\s*|^НАЗВАНИЕ:\s*', '', t)
        t = _re.sub(r'\s*сапфир.*', '', t, flags=_re.IGNORECASE)
        t = _re.sub(r'\[.*?\]|\(.*?\)', '', t)
        t = _re.sub(r'\s{2,}', ' ', t).strip()
        return t

    def _download(url):
        try:
            r = requests.get(url, timeout=25, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200 and len(r.content) > 1000:
                return r.content
        except Exception:
            pass
        return None

    def _send(method, data=None, files=None):
        url = f'https://api.telegram.org/bot{bot_token}/{method}'
        for _ in range(3):
            try:
                if files:
                    r = requests.post(url, data=data, files=files, timeout=90)
                else:
                    r = requests.post(url, json=data, timeout=30)
                result = r.json()
                if result.get('ok'):
                    return result
                err = result.get('description', '')
                if 'Too Many Requests' in err:
                    m = _re.search(r'(\d+)', err)
                    _time.sleep(int(m.group(1)) + 5 if m else 40)
                    continue
                logging.warning(f'TG error: {err}')
                _time.sleep(3)
            except Exception as e:
                logging.warning(f'TG request error: {e}')
                _time.sleep(5)
        return None

    # Load restaurants
    with open('listings_vietnam.json', encoding='utf-8') as f:
        vn = _json.load(f)
    restaurants = []
    for item in vn['restaurants']:
        if item['title'] == 'Channel created':
            continue
        desc = item.get('description', '')
        if len(desc) < 80:
            continue
        photos = item.get('photos') or item.get('images') or []
        if not photos:
            continue
        restaurants.append({'id': item['id'], 'title': _clean(item['title']),
                             'description': desc, 'photos': photos[:10]})

    # Load progress
    progress = {'posted_ids': [], 'tg_data': {}}
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            progress = _json.load(f)
    posted_ids = set(progress['posted_ids'])
    tg_data = progress.get('tg_data', {})

    to_post = [r for r in restaurants if r['id'] not in posted_ids]
    _poster_status['total'] = len(to_post)
    _poster_status['posted'] = 0
    logging.info(f'Restaurant poster: {len(to_post)} to post')

    for r in to_post:
        if not _poster_status['running']:
            break
        _poster_status['last'] = r['title']
        caption = f"<b>🍽 {r['title']}</b>\n\n{r['description']}"

        imgs = []
        for url in r['photos']:
            img = _download(url)
            if img:
                imgs.append(img)
            _time.sleep(0.3)

        if not imgs:
            logging.warning(f'No photos for {r["title"]}')
            _time.sleep(3)
            continue

        if len(imgs) == 1:
            result = _send('sendPhoto', data={
                'chat_id': CHANNEL, 'caption': caption[:1024], 'parse_mode': 'HTML'
            }, files={'photo': ('p.jpg', imgs[0], 'image/jpeg')})
            imgs.clear()
            if result:
                msg = result['result']
                photo = msg.get('photo', [])
                fid = max(photo, key=lambda x: x.get('file_size', 0))['file_id'] if photo else None
                tg_data[r['id']] = {'message_id': msg['message_id'], 'file_ids': [fid] if fid else []}
        else:
            files = {}
            media = []
            for i, img in enumerate(imgs):
                k = f'p{i}'
                files[k] = (f'{k}.jpg', img, 'image/jpeg')
                entry = {'type': 'photo', 'media': f'attach://{k}'}
                if i == 0:
                    entry['caption'] = caption[:1024]
                    entry['parse_mode'] = 'HTML'
                media.append(entry)
            result = _send('sendMediaGroup', data={
                'chat_id': CHANNEL, 'media': _json.dumps(media)
            }, files=files)
            imgs.clear()
            files.clear()
            if result:
                msgs = result['result']
                fids = []
                for msg in msgs:
                    ph = msg.get('photo', [])
                    if ph:
                        fids.append(max(ph, key=lambda x: x.get('file_size', 0))['file_id'])
                tg_data[r['id']] = {'message_id': msgs[0]['message_id'] if msgs else None, 'file_ids': fids}

        if r['id'] in tg_data:
            posted_ids.add(r['id'])
            _poster_status['posted'] += 1
            progress['posted_ids'] = list(posted_ids)
            progress['tg_data'] = tg_data
            with open(PROGRESS_FILE, 'w') as f:
                _json.dump(progress, f, ensure_ascii=False)
            logging.info(f'Posted [{_poster_status["posted"]}/{_poster_status["total"]}]: {r["title"][:40]}  msg={tg_data[r["id"]].get("message_id")}')

        _time.sleep(5)

    # Update JSON with TG links
    if tg_data:
        with open('listings_vietnam.json', encoding='utf-8') as f:
            vn_data = _json.load(f)
        for item in vn_data['restaurants']:
            rid = item.get('id')
            if rid not in tg_data:
                continue
            info = tg_data[rid]
            if info.get('message_id'):
                item['telegram_link'] = f'https://t.me/restoranvietnam/{info["message_id"]}'
            if info.get('file_ids'):
                item['tg_file_ids'] = info['file_ids']
        with open('listings_vietnam.json', 'w', encoding='utf-8') as f:
            _json.dump(vn_data, f, ensure_ascii=False, indent=2)

        with open('listings_data.json', encoding='utf-8') as f:
            main_data = _json.load(f)
        vn_by_id = {r['id']: r for r in vn_data['restaurants']}
        for r in main_data['vietnam']['restaurants']:
            rid = r.get('id')
            if rid and rid in vn_by_id and rid in tg_data:
                r['telegram_link'] = vn_by_id[rid].get('telegram_link', r.get('telegram_link'))
                if vn_by_id[rid].get('tg_file_ids'):
                    r['tg_file_ids'] = vn_by_id[rid]['tg_file_ids']
        with open('listings_data.json', 'w', encoding='utf-8') as f:
            _json.dump(main_data, f, ensure_ascii=False, indent=2)
        logging.info('JSON updated with TG links')

    _poster_status['running'] = False
    logging.info(f'Restaurant poster done. Posted {len(posted_ids)} total.')


@app.route('/api/admin/post-restaurants', methods=['POST'])
def api_post_restaurants():
    global _poster_thread, _poster_status
    if _poster_status.get('running'):
        return jsonify({'status': 'already_running', 'posted': _poster_status['posted'],
                        'total': _poster_status['total'], 'last': _poster_status['last']})
    action = request.json.get('action', 'start') if request.is_json else 'start'
    if action == 'stop':
        _poster_status['running'] = False
        return jsonify({'status': 'stopped'})
    _poster_status['running'] = True
    _poster_thread = threading.Thread(target=_run_restaurant_poster, daemon=True)
    _poster_thread.start()
    return jsonify({'status': 'started'})


@app.route('/api/admin/post-restaurants', methods=['GET'])
def api_post_restaurants_status():
    progress = {'posted_ids': []}
    try:
        with open('post_progress.json') as f:
            progress = json.load(f)
    except Exception:
        pass
    return jsonify({
        'running': _poster_status.get('running', False),
        'posted': _poster_status.get('posted', 0),
        'total': _poster_status.get('total', 0),
        'last': _poster_status.get('last', ''),
        'total_done': len(progress.get('posted_ids', []))
    })


@app.route('/internal/git_push', methods=['POST'])
def internal_git_push():
    import subprocess as _sp
    base = os.path.dirname(os.path.abspath(__file__))
    SSH = '/nix/store/m031f7b9gc32vp5rhjdfzmsfmx92zpb7-pid2-runtime-path/bin/ssh'
    GIT = '/nix/store/6h39ipxhzp4r5in5g4rhdjz7p7fkicd0-replit-runtime-path/bin/git'
    KEY = '/home/runner/.ssh/github_goldantelope'
    env = {
        'PATH': '/nix/store/m031f7b9gc32vp5rhjdfzmsfmx92zpb7-pid2-runtime-path/bin:/usr/bin:/bin',
        'HOME': '/home/runner',
        'GIT_SSH_COMMAND': f'{SSH} -i {KEY} -o StrictHostKeyChecking=no',
        'GIT_AUTHOR_NAME': 'GoldAntelope Bot',
        'GIT_AUTHOR_EMAIL': 'bot@goldantelope.app',
        'GIT_COMMITTER_NAME': 'GoldAntelope Bot',
        'GIT_COMMITTER_EMAIL': 'bot@goldantelope.app',
    }
    # Remove stale lock files
    for lock in ['config.lock', 'index.lock', 'COMMIT_EDITMSG.lock']:
        lp = os.path.join(base, '.git', lock)
        if os.path.exists(lp):
            os.remove(lp)
    def run(cmd):
        r = _sp.run([GIT] + cmd, cwd=base, capture_output=True, text=True, env=env)
        return r.stdout.strip() + r.stderr.strip()
    msg = request.json.get('message', 'Update') if request.json else 'Update'
    out = []
    out.append(run(['add', '-A']))
    out.append(run(['commit', '--allow-empty', '-m', msg]))
    out.append(run(['push', 'origin', 'master']))
    return jsonify({'output': out, 'success': 'fatal' not in out[-1] and 'error' not in out[-1].lower()})


# ── Временный endpoint генерации Telethon StringSession ──
_tg_auth_state = {}  # phone_hash, client
_india_auth_state = {}  # separate state for india session regen


@app.route('/tg-auth-india', methods=['GET'])
def tg_auth_india_page():
    return '''<!DOCTYPE html><html><head><meta charset="utf-8">
<title>India Parser — Новая сессия</title>
<style>body{font-family:sans-serif;max-width:540px;margin:50px auto;padding:20px;background:#0d1117;color:#c9d1d9}
input{width:100%;padding:10px;margin:6px 0 12px;box-sizing:border-box;font-size:15px;background:#161b22;border:1px solid #30363d;color:#fff;border-radius:6px}
label{font-size:13px;color:#8b949e} button{background:#1f6feb;color:#fff;border:none;padding:12px 24px;font-size:15px;cursor:pointer;border-radius:6px;width:100%;margin-top:4px}
button.green{background:#238636} button.orange{background:#d97706}
.result{background:#161b22;padding:12px;border-radius:6px;word-break:break-all;margin-top:12px;font-size:12px;font-family:monospace;color:#3fb950}
h2{color:#f0883e;margin-bottom:4px} .sub{color:#8b949e;font-size:13px;margin-bottom:16px}
.err{color:#f85149} .info{color:#79c0ff;font-size:13px}</style></head>
<body>
<h2>🔑 India Parser — Пересоздание сессии</h2>
<p class="sub">API ID: 34174007 · API Hash: b8b86f94...bc81b9</p>
<div id="step1">
  <label>Номер телефона аккаунта</label>
  <input id="phone" value="+84362880850" placeholder="+7XXXXXXXXXX">
  <button onclick="sendCode()">📲 Получить код</button>
</div>
<div id="step2" style="display:none">
  <label>Код из Telegram</label>
  <input id="code" placeholder="12345" maxlength="6">
  <button onclick="verifyCode()">✅ Авторизоваться</button>
</div>
<div id="step3" style="display:none">
  <p class="info">✅ Новая сессия получена!</p>
  <div class="result" id="sess_str"></div>
  <button class="green" onclick="pushToSpace()" style="margin-top:12px">🚀 Обновить HF Space автоматически</button>
  <div id="push_result" style="margin-top:10px;font-size:13px"></div>
</div>
<div id="result" style="margin-top:10px;font-size:14px"></div>
<script>
async function sendCode(){
  const ph=document.getElementById('phone').value;
  document.getElementById('result').innerHTML='⏳ Отправка кода...';
  const r=await fetch('/tg-auth-india/send-code',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({phone:ph})});
  const d=await r.json();
  if(d.ok){
    document.getElementById('step1').style.display='none';
    document.getElementById('step2').style.display='';
    document.getElementById('result').innerHTML='✅ Код отправлен на '+ph;
  } else document.getElementById('result').innerHTML='<span class="err">❌ '+d.error+'</span>';
}
async function verifyCode(){
  const code=document.getElementById('code').value;
  document.getElementById('result').innerHTML='⏳ Авторизация...';
  const r=await fetch('/tg-auth-india/verify',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:code})});
  const d=await r.json();
  if(d.ok){
    document.getElementById('step2').style.display='none';
    document.getElementById('sess_str').innerText=d.session;
    document.getElementById('step3').style.display='';
    document.getElementById('result').innerHTML='Авторизован: <b>'+d.user+'</b>';
  } else document.getElementById('result').innerHTML='<span class="err">❌ '+d.error+'</span>';
}
async function pushToSpace(){
  document.getElementById('push_result').innerHTML='⏳ Загрузка в HF Space...';
  const sess=document.getElementById('sess_str').innerText;
  const r=await fetch('/tg-auth-india/push-to-hf',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({session:sess})});
  const d=await r.json();
  if(d.ok) document.getElementById('push_result').innerHTML='✅ '+d.message;
  else document.getElementById('push_result').innerHTML='<span class="err">❌ '+d.error+'</span>';
}
</script></body></html>'''


@app.route('/tg-auth-india/send-code', methods=['POST'])
def tg_auth_india_send_code():
    import asyncio as _aio, threading as _thr
    from telethon import TelegramClient as _TC
    from telethon.sessions import StringSession as _SS
    _INDIA_API_ID   = 34174007
    _INDIA_API_HASH = 'b8b86f94083feb5ccbdf3c6672bc81b9'
    data  = request.get_json()
    phone = data.get('phone', '').strip()
    res   = {}
    def run():
        loop = _aio.new_event_loop()
        async def _do():
            c = _TC(_SS(), _INDIA_API_ID, _INDIA_API_HASH)
            await c.connect()
            r = await c.send_code_request(phone)
            _india_auth_state['hash']  = r.phone_code_hash
            _india_auth_state['phone'] = phone
            res['ok'] = True
            await c.disconnect()
        try: loop.run_until_complete(_do())
        except Exception as e: res['error'] = str(e)
        finally: loop.close()
    t = _thr.Thread(target=run); t.start(); t.join(timeout=25)
    return jsonify({'ok': True} if res.get('ok') else {'ok': False, 'error': res.get('error', 'Ошибка')})


@app.route('/tg-auth-india/verify', methods=['POST'])
def tg_auth_india_verify():
    import asyncio as _aio, threading as _thr
    from telethon import TelegramClient as _TC
    from telethon.sessions import StringSession as _SS
    _INDIA_API_ID   = 34174007
    _INDIA_API_HASH = 'b8b86f94083feb5ccbdf3c6672bc81b9'
    data       = request.get_json()
    code       = data.get('code', '').strip()
    phone      = _india_auth_state.get('phone')
    phone_hash = _india_auth_state.get('hash')
    if not phone_hash:
        return jsonify({'ok': False, 'error': 'Сначала запросите код'})
    res = {}
    def run():
        loop = _aio.new_event_loop()
        async def _do():
            c = _TC(_SS(), _INDIA_API_ID, _INDIA_API_HASH)
            await c.connect()
            try:
                await c.sign_in(phone=phone, code=code, phone_code_hash=phone_hash)
                me = await c.get_me()
                res['ok']      = True
                res['session'] = c.session.save()
                res['user']    = f'{me.first_name} (id={me.id})'
            except Exception as e:
                res['error'] = str(e)
            finally:
                try: await c.disconnect()
                except: pass
        try: loop.run_until_complete(_do())
        except Exception as e: res['error'] = str(e)
        finally: loop.close()
    t = _thr.Thread(target=run); t.start(); t.join(timeout=30)
    if res.get('ok'):
        _india_auth_state['session'] = res['session']
        return jsonify({'ok': True, 'session': res['session'], 'user': res['user']})
    return jsonify({'ok': False, 'error': res.get('error', 'Ошибка верификации')})


@app.route('/tg-auth-india/push-to-hf', methods=['POST'])
def tg_auth_india_push_hf():
    import re as _re, io as _io
    data    = request.get_json()
    new_sess = data.get('session', '').strip()
    if not new_sess:
        return jsonify({'ok': False, 'error': 'Нет сессии'})
    try:
        _hf_token = os.environ.get('HF_TOKEN', '')
        from huggingface_hub import HfApi as _HF
        _hf = _HF(token=_hf_token)
        path = _hf.hf_hub_download('poweramanita/indiaparsing', 'app.py', repo_type='space')
        with open(path) as f:
            src = f.read()
        old_sess_m = _re.search(r"SESSION\s*=\s*'([^']+)'", src)
        if not old_sess_m:
            return jsonify({'ok': False, 'error': 'SESSION не найдена в app.py'})
        src = src.replace(old_sess_m.group(0), f"SESSION  = '{new_sess}'")
        buf = _io.BytesIO(src.encode())
        buf.name = 'app.py'
        _hf.upload_file(path_or_fileobj=buf, path_in_repo='app.py',
                        repo_id='poweramanita/indiaparsing', repo_type='space',
                        commit_message='update: new Telethon session string')
        _hf.restart_space('poweramanita/indiaparsing', token=_hf_token)
        return jsonify({'ok': True, 'message': 'HF Space обновлён и перезапущен!'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})



@app.route('/tg-auth', methods=['GET'])
def tg_auth_page():
    return '''<!DOCTYPE html><html><head><meta charset="utf-8">
<title>TG Auth</title>
<style>body{font-family:sans-serif;max-width:520px;margin:50px auto;padding:20px;background:#111;color:#eee}
input{width:100%;padding:10px;margin:6px 0 12px;box-sizing:border-box;font-size:15px;background:#222;border:1px solid #444;color:#fff;border-radius:6px}
label{font-size:13px;color:#8b949e}
button{background:#2563eb;color:#fff;border:none;padding:12px 24px;font-size:16px;cursor:pointer;border-radius:6px;width:100%;margin-top:4px}
.result{background:#1a1a1a;padding:12px;border-radius:6px;word-break:break-all;margin-top:12px;font-size:12px;font-family:monospace;color:#56d364}
h2{color:#58a6ff;margin-bottom:16px}
.note{font-size:12px;color:#666;margin-bottom:16px}</style></head>
<body>
<h2>🔑 Новая Telegram сессия</h2>
<div id="step1">
  <p class="note">Шаг 1: Укажите данные приложения и номер телефона</p>
  <label>API ID</label><input id="api_id" value="38294687">
  <label>API Hash</label><input id="api_hash" value="4cc4e56b6e0fabe46b643bb14696793f">
  <label>Номер телефона</label><input id="phone" value="+84777373211">
  <button onclick="sendCode()">📲 Получить код</button>
</div>
<div id="step2" style="display:none">
  <p class="note">Шаг 2: Введите код из Telegram</p>
  <label>Код подтверждения</label><input id="code" placeholder="12345" maxlength="5">
  <button onclick="verifyCode()">✅ Авторизоваться</button>
</div>
<div id="result"></div>
<script>
async function sendCode(){
  const ph=document.getElementById('phone').value;
  const aid=document.getElementById('api_id').value;
  const ahash=document.getElementById('api_hash').value;
  document.getElementById('result').innerHTML='⏳ Отправка...';
  const r=await fetch('/tg-auth/send-code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:ph,api_id:aid,api_hash:ahash})});
  const d=await r.json();
  if(d.ok){document.getElementById('step1').style.display='none';document.getElementById('step2').style.display='';document.getElementById('result').innerHTML='✅ Код отправлен на '+ph;}
  else document.getElementById('result').innerHTML='❌ '+d.error;
}
async function verifyCode(){
  const code=document.getElementById('code').value;
  document.getElementById('result').innerHTML='⏳ Авторизация...';
  const r=await fetch('/tg-auth/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:code})});
  const d=await r.json();
  if(d.ok){document.getElementById('result').innerHTML='<b>✅ '+d.user+' — авторизован!</b><br><details open><summary style="cursor:pointer;color:#58a6ff;margin:8px 0">📋 Строка сессии (TELETHON_SESSION2)</summary><div class="result" id="sess">'+d.session+'</div></details><button onclick="navigator.clipboard.writeText(document.getElementById(\'sess\').innerText)" style="margin-top:8px">📋 Скопировать</button><p style="margin-top:12px;font-size:13px;color:#8b949e">Вставьте эту строку в HF Space Secrets как <b>TELETHON_SESSION2</b></p>';}
  else document.getElementById('result').innerHTML='❌ '+d.error;
}
</script></body></html>'''

@app.route('/tg-auth/send-code', methods=['POST'])
def tg_auth_send_code():
    import asyncio, threading
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    data = request.get_json()
    phone    = data.get('phone', '').strip()
    API_ID   = int(data.get('api_id', 32881984))
    API_HASH = data.get('api_hash', 'd2588f09dfbc5103ef77ef21c07dbf8b').strip()

    result = {}
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async def _do():
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            r = await client.send_code_request(phone)
            _tg_auth_state['hash'] = r.phone_code_hash
            _tg_auth_state['phone'] = phone
            _tg_auth_state['api_id'] = API_ID
            _tg_auth_state['api_hash'] = API_HASH
            result['ok'] = True
            await client.disconnect()
        try:
            loop.run_until_complete(_do())
        except Exception as e:
            result['error'] = str(e)
        finally:
            loop.close()
    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=20)
    if result.get('ok'):
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': result.get('error', 'Не удалось отправить код')})

@app.route('/tg-auth/verify', methods=['POST'])
def tg_auth_verify():
    import asyncio, threading
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    data = request.get_json()
    code = data.get('code', '').strip()
    phone      = _tg_auth_state.get('phone')
    phone_hash = _tg_auth_state.get('hash')
    if not phone_hash:
        return jsonify({'ok': False, 'error': 'Сначала запросите код'})
    API_ID   = _tg_auth_state.get('api_id', 32881984)
    API_HASH = _tg_auth_state.get('api_hash', 'd2588f09dfbc5103ef77ef21c07dbf8b')
    result = {}
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async def _do():
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=phone_hash)
                me = await client.get_me()
                result['ok'] = True
                result['session'] = client.session.save()
                result['user'] = f'{me.first_name} (id={me.id})'
            except Exception as e:
                result['ok'] = False
                result['error'] = str(e)
            finally:
                try: await client.disconnect()
                except: pass
        try:
            loop.run_until_complete(_do())
        except Exception as e:
            result['ok'] = False
            result['error'] = str(e)
        finally:
            loop.close()
    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=30)
    if result.get('ok'):
        sess_str = result['session']
        with open('parser_session.txt', 'w') as f:
            f.write(sess_str)
        app.logger.info(f'TG Auth OK: {result["user"]} — session saved, starting parser')
        try:
            import telethon_parser
            telethon_parser.start()
        except Exception as _pe:
            app.logger.warning(f'Parser start error: {_pe}')
        return jsonify({'ok': True, 'session': sess_str, 'user': result['user']})
    return jsonify({'ok': False, 'error': result.get('error', 'Ошибка')})


@app.route('/api/admin/india-indo-parser-status')
def india_indo_parser_status():
    try:
        from india_indo_parser import status as ps
        return jsonify(ps)
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/parser-status')
def parser_status_page():
    try:
        import telethon_parser
        status = telethon_parser.get_status()
        is_running = telethon_parser.STATS.get('running', False)
    except Exception as e:
        status = f'Ошибка импорта парсера: {e}'
        is_running = False
    color = '#2ecc71' if is_running else '#e67e22'
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Parser Status</title>
<meta http-equiv="refresh" content="15">
<style>body{{font-family:monospace;background:#111;color:#eee;padding:20px;}}
h2{{color:#d4af37;}} pre{{background:#1a1a1a;padding:15px;border-radius:8px;white-space:pre-wrap;font-size:13px;}}
.badge{{display:inline-block;padding:4px 12px;border-radius:12px;background:{color};color:#000;font-weight:bold;margin-bottom:12px;}}
a{{color:#d4af37;}}</style></head>
<body>
<h2>🦌 GoldAntelope — Статус парсера</h2>
<div class="badge">{"✅ РАБОТАЕТ" if is_running else "⏳ ОЖИДАНИЕ"}</div>
<pre>{status}</pre>
<p><a href="/tg-auth">🔐 Авторизация</a> | <a href="/">🏠 Главная</a></p>
</body></html>'''


try:
    import telethon_parser as _tp_init
    if _tp_init.get_session():
        logger.info('TELETHON_SESSION найдена — запускаю парсер...')
        _tp_init.start()
    else:
        logger.info('TELETHON_SESSION не задана — авторизуйтесь через /tg-auth')
except Exception as _e:
    logger.warning(f'Парсер не запущен: {_e}')

RATES_UPDATE_INTERVAL = 1800

def _detect_city_paymens(text):
    """Detect city from post text for paymens_vn listings."""
    if not text:
        return 'Вьетнам'
    t = text.lower()
    city_kw = {
        'Нячанг': ['нячанг', 'nha trang', 'nhatrang'],
        'Дананг': ['дананг', 'da nang', 'danang'],
        'Хошимин': ['хошимин', 'ho chi minh', 'hochiminh', 'сайгон', 'saigon', 'hcm'],
        'Ханой': ['ханой', 'hanoi'],
        'Фукуок': ['фукуок', 'phu quoc', 'phuquoc'],
    }
    for city, kws in city_kw.items():
        for kw in kws:
            if kw in t:
                return city
    return 'Вьетнам'

def update_paymens_rates():
    """Background task: scrape @paymens_vn every 30 min, save rates + regular posts."""
    import time as _t
    while True:
        _t.sleep(RATES_UPDATE_INTERVAL)
        try:
            logger.info('[RATES] Updating from @paymens_vn...')
            from vietnamparsing_parser import scrape_extra_channel_page
            msgs = scrape_extra_channel_page('paymens_vn')
            if not msgs:
                logger.warning('[RATES] No messages from paymens_vn')
                continue

            rate_pattern = re.compile(r'➤.*VNĐ')

            with file_lock:
                fpath = 'listings_vietnam.json'
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    data = {}

                exchange_list = data.get('money_exchange', [])
                existing_ids = {item.get('id') for item in exchange_list}
                updated = False

                for msg_idx, msg in enumerate(msgs):
                    text = (msg.get('text', '') or '').replace('\xa0', ' ')
                    post_id = msg.get('post_id', 0) or msg.get('id', 0)
                    if not text.strip():
                        continue
                    if text.strip() in ('Channel created', 'Channel photo updated'):
                        continue
                    if not post_id:
                        post_id = f'auto_{msg_idx}'
                    item_id = f'paymens_vn_{post_id}'

                    city = _detect_city_paymens(text)
                    lines_raw = [l.strip() for l in text.split('\n') if l.strip()]
                    title_line = lines_raw[0] if lines_raw else 'Обмен валют'

                    new_item = {
                        'id': item_id,
                        'source_group': 'paymens_vn',
                        'title': title_line[:120],
                        'text': msg.get('text', ''),
                        'description': msg.get('text', ''),
                        'city': city,
                        'city_ru': city,
                        'date': msg.get('date', ''),
                        'images': [f'/tg_img/paymens_vn/{post_id}'] if msg.get('images') else [],
                        'contact': '@paymens_vn',
                        'contact_name': 'paymens_vn',
                        'telegram_link': f'https://t.me/paymens_vn/{post_id}' if post_id else 'https://t.me/paymens_vn',
                    }

                    if item_id not in existing_ids:
                        exchange_list.insert(0, new_item)
                        existing_ids.add(item_id)
                        updated = True
                        logger.info(f'[RATES] Added post: {item_id} (city={city})')
                    else:
                        for i, item in enumerate(exchange_list):
                            if item.get('id') == item_id:
                                new_item['city'] = city
                                exchange_list[i] = new_item
                                updated = True
                                break

                if updated:
                    data['money_exchange'] = exchange_list
                    with open(fpath, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    data_cache.pop('vietnam', None)
                    data_cache.pop('all', None)
                    logger.info('[RATES] paymens_vn data updated successfully')
                else:
                    logger.info('[RATES] No new posts from paymens_vn')

        except Exception as e:
            logger.error(f'[RATES] Error updating: {e}')

_rates_thread = threading.Thread(target=update_paymens_rates, daemon=True)
_rates_thread.start()
logger.info(f'[RATES] Background rates updater started (every {RATES_UPDATE_INTERVAL}s)')

# ── India / Indonesia real estate poller ────────────────────────────────────
# NOTE: Disabled — forwarding is handled by HF Space poweramanita/indiaparsing
# Running both simultaneously causes Telegram session conflict (same session, two IPs)
# logger.info('[PARSER] India/Indo poller disabled — HF Space handles forwarding')

# ── Telegram Feed (parsing channels → Bot API) ──────────────────────────────
import tg_feed as _tg_feed
# Отключаем собственный getUpdates — обновления приходят из _gavibeshub_poller
_tg_feed._external_poll_mode = True
_tg_feed.start()

@app.route('/api/tg-feed')
def api_tg_feed():
    channel = request.args.get('channel')
    limit   = min(int(request.args.get('limit', 50)), 200)
    offset  = int(request.args.get('offset', 0))
    posts   = _tg_feed.get_posts(channel=channel, limit=limit, offset=offset)
    return jsonify({'ok': True, 'posts': posts, 'count': len(posts)})

@app.route('/api/tg-feed/stats')
def api_tg_feed_stats():
    return jsonify(_tg_feed.get_stats())

@app.route('/tg-feed')
def tg_feed_page():
    channel = request.args.get('channel', '')
    limit   = min(int(request.args.get('limit', 50)), 200)
    posts   = _tg_feed.get_posts(channel=channel or None, limit=limit)
    stats   = _tg_feed.get_stats()
    channels = list(_tg_feed.CHANNELS.items())
    return render_template('tg_feed.html',
                           posts=posts,
                           stats=stats,
                           channels=channels,
                           active_channel=channel)

@app.route('/api/admin/tg-feed-import', methods=['GET', 'POST'])
def api_tg_feed_import():
    """
    GET  — статус импорта по каждому каналу.
    POST — запустить bulk import (параметр channels=ch1,ch2 или все).
    """
    if request.method == 'POST':
        ch_param = request.form.get('channels', '') or request.args.get('channels', '')
        channels = [c.strip().lstrip('@') for c in ch_param.split(',')] if ch_param else None
        # Проверяем что уже не идёт импорт
        status = _tg_feed.get_import_status()
        in_progress = [c for c, s in status.items() if not s.get('done', True)]
        if in_progress:
            return jsonify({'ok': False, 'error': f'Импорт уже идёт: {in_progress}'})
        def _run():
            _tg_feed.run_bulk_import(channels)
        threading.Thread(target=_run, daemon=True, name='TgFeedBulkImport').start()
        targets = channels or list(_tg_feed.CHANNELS.keys())
        return jsonify({'ok': True, 'started': targets,
                        'limits': {c: _tg_feed.CHANNEL_IMPORT_LIMITS.get(c, 'все') for c in targets}})
    # GET — статус
    status = _tg_feed.get_import_status()
    stats  = _tg_feed.get_stats()
    return jsonify({'ok': True, 'import_status': status, 'feed_stats': stats})


if __name__ == '__main__':
    import threading
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
