"""
Миграция: заменяет все /api/tgphoto/<file_id> URL
на прямые CDN ссылки https://api.telegram.org/file/bot<TOKEN>/<file_path>
"""
import json, os, time, requests, re

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
if not TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN не установлен")

FILES = [
    'listings_vietnam.json',
    'listings_thailand.json',
    'listings_india.json',
    'listings_indonesia.json',
]

PROXY_RE = re.compile(r'^/api/tgphoto/(.+)$')


def resolve_file_id(fid: str) -> str | None:
    """Возвращает прямой CDN URL или None при ошибке."""
    try:
        r = requests.get(
            f'https://api.telegram.org/bot{TOKEN}/getFile',
            params={'file_id': fid}, timeout=10
        )
        if r.status_code == 200 and r.json().get('ok'):
            fp = r.json()['result']['file_path']
            return f'https://api.telegram.org/file/bot{TOKEN}/{fp}'
    except Exception as e:
        print(f"  ERR getFile({fid[:30]}...): {e}")
    return None


def fix_url_list(urls: list) -> tuple[list, int]:
    """Возвращает (исправленный список, кол-во замен)."""
    result = []
    fixed = 0
    for u in urls:
        m = PROXY_RE.match(str(u))
        if m:
            cdn = resolve_file_id(m.group(1))
            if cdn:
                result.append(cdn)
                fixed += 1
                time.sleep(0.1)  # не спамим Bot API
            else:
                result.append(u)  # оставляем как есть
        else:
            result.append(u)
    return result, fixed


total_fixed = 0

for fname in FILES:
    if not os.path.exists(fname):
        continue
    with open(fname, encoding='utf-8') as f:
        data = json.load(f)

    file_fixed = 0
    for cat, items in data.items():
        if not isinstance(items, list):
            continue
        for item in items:
            changed = False

            photos, n = fix_url_list(item.get('photos') or [])
            if n:
                item['photos'] = photos
                changed = True
                file_fixed += n

            all_imgs, n = fix_url_list(item.get('all_images') or [])
            if n:
                item['all_images'] = all_imgs
                changed = True
                file_fixed += n

            img = item.get('image_url', '')
            if img and PROXY_RE.match(str(img)):
                m = PROXY_RE.match(str(img))
                cdn = resolve_file_id(m.group(1))
                if cdn:
                    item['image_url'] = cdn
                    changed = True
                    file_fixed += 1
                    time.sleep(0.1)

            # Синхронизируем image_url с первым photos
            if changed and item.get('photos'):
                item['image_url'] = item['photos'][0]

    if file_fixed:
        with open(fname, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✅ {fname}: исправлено {file_fixed} URL")
    else:
        print(f"   {fname}: нет proxy-URL (всё чисто)")

    total_fixed += file_fixed

print(f"\nИтого заменено: {total_fixed} URL")
