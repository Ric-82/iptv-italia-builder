from pathlib import Path
import yaml
from downloader import download_playlists

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / 'config' / 'sources.yaml'
CACHE_DIR = BASE_DIR / 'cache' / 'playlists'

with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f)

sources = data['sources']
print(f'Sorgenti configurate: {len(sources)}')
download_playlists(sources, CACHE_DIR)
