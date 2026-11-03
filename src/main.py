import subprocess
import sys
from pathlib import Path

import yaml

from downloader import download_playlists
import lcn_official

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / 'src'
CONFIG_FILE = BASE_DIR / 'config' / 'sources.yaml'
CACHE_DIR = BASE_DIR / 'cache' / 'playlists'


def run_step(script):
    print(f'== {script}')
    return subprocess.run([sys.executable, str(SRC_DIR / script)], cwd=BASE_DIR).returncode


def main():
    sources = yaml.safe_load(CONFIG_FILE.read_text(encoding='utf-8'))['sources']
    print(f'Sorgenti configurate: {len(sources)}')

    # se i PDF ufficiali non sono aggiornabili resta l'ultima tabella valida: l'errore viene solo segnalato
    lcn_status = lcn_official.main()

    download_playlists(sources, CACHE_DIR)
    for script in ('parser.py', 'prober.py', 'selector.py'):
        code = run_step(script)
        if code != 0:
            print(f'[ERRORE] {script} terminato con codice {code}')
            return code
    return lcn_status


if __name__ == '__main__':
    sys.exit(main())
