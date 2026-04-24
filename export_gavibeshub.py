import requests
import re
import time
from bs4 import BeautifulSoup

CHANNEL = 'media_vn'
BASE_URL = f'https://t.me/s/{CHANNEL}'

EMOJI_RE = re.compile(
    "["
    u"\U0001F600-\U0001F64F"
    u"\U0001F300-\U0001F5FF"
    u"\U0001F680-\U0001F6FF"
    u"\U0001F1E0-\U0001F1FF"
    u"\U00002700-\U000027BF"
    u"\U0001F900-\U0001F9FF"
    u"\U0001FA00-\U0001FA6F"
    u"\U0001FA70-\U0001FAFF"
    u"\U00002600-\U000026FF"
    u"\U00002B00-\U00002BFF"
    u"\U0000FE00-\U0000FE0F"
    u"\U0001F700-\U0001F77F"
    u"\U0001F780-\U0001F7FF"
    u"\U0001F800-\U0001F8FF"
    u"\U000024C2-\U0001F251"
    u"\U0001f004"
    u"\U0001f0cf"
    "]+",
    flags=re.UNICODE
)


def clean_text(text: str) -> str:
    if not text:
        return ''
    text = EMOJI_RE.sub('', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n+', ' ', text)
    text = text.strip()
    return text


def fetch_page(url, before=None):
    params = {}
    if before:
        params['before'] = before
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_posts(html):
    soup = BeautifulSoup(html, 'html.parser')
    posts = []
    for wrap in soup.select('.tgme_widget_message_wrap'):
        msg_div = wrap.select_one('.tgme_widget_message')
        if not msg_div:
            continue
        data_post = msg_div.get('data-post', '')
        msg_id = int(data_post.split('/')[-1]) if '/' in data_post else 0

        has_photo = bool(
            msg_div.select('.tgme_widget_message_photo_wrap') or
            msg_div.select('.tgme_widget_message_photo') or
            msg_div.select('a.tgme_widget_message_photo_wrap')
        )
        if not has_photo:
            continue

        text_el = msg_div.select_one('.tgme_widget_message_text')
        raw_text = text_el.get_text('\n') if text_el else ''
        cleaned = clean_text(raw_text)

        date_el = msg_div.select_one('.tgme_widget_message_date time')
        date_str = date_el.get('datetime', '')[:10] if date_el else ''

        posts.append({
            'id': msg_id,
            'date': date_str,
            'text': cleaned,
        })
    return posts


def get_oldest_id(html):
    soup = BeautifulSoup(html, 'html.parser')
    ids = []
    for msg_div in soup.select('.tgme_widget_message'):
        data_post = msg_div.get('data-post', '')
        if '/' in data_post:
            try:
                ids.append(int(data_post.split('/')[-1]))
            except ValueError:
                pass
    return min(ids) if ids else None


def main():
    all_posts = {}
    before = None
    page = 0

    print(f"Scraping @{CHANNEL} from t.me/s/...")

    while True:
        url = BASE_URL
        print(f"  Page {page+1}, before={before}", flush=True)
        try:
            html = fetch_page(url, before=before)
        except Exception as e:
            print(f"  Error: {e}")
            break

        posts = parse_posts(html)
        if not posts:
            print("  No photo posts on this page, done.")
            break

        for p in posts:
            if p['id'] not in all_posts:
                all_posts[p['id']] = p

        oldest_id = get_oldest_id(html)
        if oldest_id is None or oldest_id <= 1:
            print("  Reached beginning.")
            break

        before = oldest_id
        page += 1
        time.sleep(1.5)

    print(f"\nTotal unique posts with photo: {len(all_posts)}")

    output_lines = []
    seen_texts = set()
    for p in sorted(all_posts.values(), key=lambda x: x['id']):
        text = p['text']
        if not text:
            continue
        key = re.sub(r'\s+', '', text).lower()[:120]
        if key in seen_texts:
            continue
        seen_texts.add(key)
        prefix = f"[{p['date']} #{p['id']}]" if p['date'] else f"[#{p['id']}]"
        output_lines.append(f"{prefix} {text}")

    result = '\n'.join(output_lines)

    with open('gavibeshub_export.txt', 'w', encoding='utf-8') as f:
        f.write(result)

    print(f"Saved {len(output_lines)} unique text posts to gavibeshub_export.txt")

    print("\n--- PREVIEW (first 20) ---")
    for line in output_lines[:20]:
        print(line)


if __name__ == '__main__':
    main()
