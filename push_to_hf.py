"""Пуш изменений на Hugging Face Space poweramanita/GA"""
import os
import sys

TOKEN = os.environ.get('HF_TOKEN', '')
if not TOKEN and len(sys.argv) > 1:
    TOKEN = sys.argv[1]
if not TOKEN:
    try:
        with open('.env.hf') as f:
            for line in f:
                if line.startswith('HF_TOKEN='):
                    TOKEN = line.strip().split('=', 1)[1]
    except Exception:
        pass

if not TOKEN:
    print("ERROR: HF_TOKEN не найден. Передайте как аргумент: python push_to_hf.py <token>")
    sys.exit(1)

from huggingface_hub import HfApi

REPO = "poweramanita/GA"
api = HfApi(token=TOKEN)

FILES = [
    ("Dockerfile",                 "Dockerfile"),
    ("README.md",                  "README.md"),
    ("requirements.txt",           "requirements.txt"),
    ("main.py",                    "main.py"),
    ("app.py",                     "app.py"),
    ("telegram_bot.py",            "telegram_bot.py"),
    ("bot_channel_parser.py",      "bot_channel_parser.py"),
    ("channel_parser.py",          "channel_parser.py"),
    ("chat_parser.py",             "chat_parser.py"),
    ("additional_parser.py",       "additional_parser.py"),
    ("vietnamparsing_parser.py",   "vietnamparsing_parser.py"),
    ("thailandparsing_parser.py",  "thailandparsing_parser.py"),
    ("tg_feed.py",                 "tg_feed.py"),
    ("listings_vietnam.json",      "listings_vietnam.json"),
    ("listings_thailand.json",     "listings_thailand.json"),
    ("listings_india.json",        "listings_india.json"),
    ("listings_indonesia.json",    "listings_indonesia.json"),
    ("listings_data.json",         "listings_data.json"),
    ("listings_chat.json",         "listings_chat.json"),
    ("file_id_index.json",         "file_id_index.json"),
    ("banner_config.json",         "banner_config.json"),
    ("banner_data.json",           "banner_data.json"),
    ("tg_feed_posts.json",         "tg_feed_posts.json"),
    ("tg_file_paths_cache.json",   "tg_file_paths_cache.json"),
    ("tg_photo_cache.json",        "tg_photo_cache.json"),
    ("analytics.json",             "analytics.json"),
    ("ads_channels_vietnam.json",  "ads_channels_vietnam.json"),
    ("parser_config_vietnam.json", "parser_config_vietnam.json"),
    ("groups_stats_vietnam.json",  "groups_stats_vietnam.json"),
    ("groups_stats_thailand.json", "groups_stats_thailand.json"),
    ("chat_history.json",          "chat_history.json"),
    ("bot_sources.json",           "bot_sources.json"),
    ("vietnam_channels.json",      "vietnam_channels.json"),
    ("thailand_channels.json",     "thailand_channels.json"),
    ("pending_vietnam.json",       "pending_vietnam.json"),
]

TEMPLATE_FILES = [
    ("templates/dashboard.html",   "templates/dashboard.html"),
    ("templates/tg_feed.html",     "templates/tg_feed.html"),
]

print(f"Загружаю файлы в {REPO}...\n")
ok = 0
fail = 0

# Папка static/ целиком
if os.path.isdir("static"):
    size = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, files in os.walk("static") for f in files
    ) / 1024 / 1024
    print(f"Загружаю static/ ({size:.1f} MB)...")
    try:
        api.upload_folder(
            folder_path="static",
            path_in_repo="static",
            repo_id=REPO,
            repo_type="space",
            commit_message="Update static assets",
        )
        print("  ✓ static/")
        ok += 1
    except Exception as e:
        print(f"  ✗ static/: {e}")
        fail += 1

for local_path, repo_path in FILES + TEMPLATE_FILES:
    if not os.path.exists(local_path):
        print(f"  ~ пропуск (нет файла): {local_path}")
        continue
    size = os.path.getsize(local_path) / 1024 / 1024
    print(f"Загружаю {local_path} ({size:.1f} MB)...")
    try:
        api.upload_file(
            path_or_fileobj=local_path,
            path_in_repo=repo_path,
            repo_id=REPO,
            repo_type="space",
            commit_message=f"Update {repo_path}",
        )
        print(f"  ✓ {repo_path}")
        ok += 1
    except Exception as e:
        print(f"  ✗ {repo_path}: {e}")
        fail += 1

print(f"\nГотово! {ok} загружено, {fail} ошибок")
print(f"Space: https://huggingface.co/spaces/{REPO}")
