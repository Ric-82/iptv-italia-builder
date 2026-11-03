"""Costruzione della tabella LCN esclusivamente dai PDF ufficiali del Ministero (MIMIT).

I PDF vengono individuati dal contenuto, mai dal nome file. La tabella viene ricostruita
solo se i documenti ufficiali sono realmente cambiati.
"""
import hashlib
import io
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import pdfplumber
import requests
import yaml
from bs4 import BeautifulSoup

from keys import name_key

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / 'config' / 'official_sources.yaml'
REGION_FILE = BASE_DIR / 'config' / 'user_region.yaml'
PDF_DIR = BASE_DIR / 'config' / 'lcn_pdfs'
STATE_DIR = BASE_DIR / 'state'
TABLE_FILE = STATE_DIR / 'lcn_official.json'
FINGERPRINT_FILE = STATE_DIR / 'official_fingerprint.json'
OUTPUT_DIR = BASE_DIR / 'output'
CHANGES_FILE = OUTPUT_DIR / 'lcn_changes.json'
BUILD_REPORT_FILE = OUTPUT_DIR / 'lcn_build_report.json'

USER_AGENT = 'Mozilla/5.0 (iptv-italia-builder LCN updater)'
SCOPE_ORDER = {'national': 0}
SENTINELS = {'RAI1', 'RAI2', 'RAI3', 'CANALE5', 'LA7'}

IT_MONTHS = {m: i for i, m in enumerate(
    ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio', 'agosto',
     'settembre', 'ottobre', 'novembre', 'dicembre'], 1)}


class OfficialSourceError(Exception):
    pass


def fetch(url, retries=3, timeout=60):
    last = None
    for _ in range(retries):
        try:
            r = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
    raise OfficialSourceError(f'download fallito: {url}: {last}')


def find_pdf_links(html, base_url):
    soup = BeautifulSoup(html, 'html.parser')
    links, seen = [], set()
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if not urlparse(href).path.lower().endswith('.pdf'):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        links.append({'url': url, 'text': a.get_text(' ', strip=True), 'order': len(links)})
    return links


def _meta_date(metadata):
    for field in ('ModDate', 'CreationDate'):
        m = re.match(r'D:(\d{4})(\d{2})(\d{2})', str((metadata or {}).get(field, '')))
        if m:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    return ''


def _text_date(text):
    text = unquote(text or '')
    m = re.search(r'(\d{1,2})\s+(' + '|'.join(IT_MONTHS) + r')\w*\s+(\d{4})', text, re.I)
    if m:
        return _safe_date(int(m[3]), IT_MONTHS[m[2].lower()], int(m[1]))
    m = re.search(r'(\d{1,2})[._-](\d{1,2})[._-](\d{4})', text)
    if m:
        return _safe_date(int(m[3]), int(m[2]), int(m[1]))
    return ''


def _safe_date(y, m, d):
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return ''


def read_pdf(data):
    """Restituisce testo delle prime pagine e data dai metadati, o None se il PDF non e' leggibile."""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = '\n'.join((p.extract_text() or '') for p in pdf.pages[:2])
            return {'text': text, 'meta_date': _meta_date(pdf.metadata)}
    except Exception:
        return None


def classify(kind_cfg, text):
    upper = text.upper()
    if kind_cfg['kind'] == 'national':
        return ('FORNITORE' in upper and 'MARCHIO' in upper and 'LCN' in upper
                and not re.search(r'\bAT\s*-?\s*\d{1,2}\b', upper))
    at = kind_cfg['area_tecnica']
    return (bool(re.search(rf'\bAT\s*-?\s*0*{at}\b', upper))
            and kind_cfg['region'].upper() in upper and 'LCN' in upper)


def doc_date(link, info, last_modified, url):
    """Data del documento: metadati PDF, poi testo del link, nome file, Last-Modified."""
    return (info['meta_date'] or _text_date(link['text']) or _text_date(url)
            or _text_date(last_modified or ''))


def _cells(row):
    return [re.sub(r'\s+', ' ', (c or '').replace('\n', ' ')).strip() for c in row]


def _detect_header(cells):
    up = [c.upper() for c in cells]
    brand = next((i for i, c in enumerate(up) if c.startswith('MARCHIO')), None)
    lcn = next((i for i, c in enumerate(up) if c.startswith('LCN')), None)
    if brand is None or lcn is None:
        return None
    company = next((i for i, c in enumerate(up) if c.startswith(('FORNITORE', 'SOCIET'))), None)
    return {'brand': brand, 'lcn': lcn, 'company': company}


def extract_rows(data):
    rows, cols = [], None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for raw in table:
                    cells = _cells(raw)
                    header = _detect_header(cells)
                    if header:
                        cols = header
                        continue
                    if not cols or len(cells) <= max(cols['brand'], cols['lcn']):
                        continue
                    lcn = cells[cols['lcn']]
                    name = cells[cols['brand']]
                    if not re.fullmatch(r'\d{1,3}', lcn) or not name:
                        continue
                    company = cells[cols['company']] if cols['company'] is not None else ''
                    provinces = [c for c in cells[cols['lcn'] + 1:] if re.fullmatch(r'[A-Z]{2}', c)]
                    rows.append(_make_row(int(lcn), name, company, provinces))
    return rows


def _make_row(lcn, name, company, provinces):
    name = re.sub(r'\(\*\)', '', name).strip()
    aka = []
    m = re.search(r'\((?:gi[aà]|ex)\s+([^)]+)\)', name, re.I)
    if m:
        aka.append(m[1].strip())
        name = (name[:m.start()] + name[m.end():]).strip()
    name = re.sub(r'\s+', ' ', name)
    return {'lcn': lcn, 'name': name, 'aka': aka, 'company': company, 'provinces': provinces}


def validate_rows(scope, cfg, rows, previous_entries):
    if len(rows) < cfg.get('min_rows', 1):
        raise OfficialSourceError(f'{scope}: troppe poche righe ({len(rows)})')
    if cfg['kind'] == 'national':
        keys = {name_key(r['name']) for r in rows}
        if not SENTINELS <= keys:
            raise OfficialSourceError(f'{scope}: canali sentinella mancanti {sorted(SENTINELS - keys)}')
    prev = len([e for e in previous_entries if e['scope'] == scope])
    drop = cfg.get('max_drop_pct', 30)
    if prev and len(rows) < prev * (1 - drop / 100):
        raise OfficialSourceError(f'{scope}: calo righe sospetto ({prev} -> {len(rows)})')


def build_scope_entries(scope, rows):
    seen, entries = set(), []
    for r in rows:
        key = (r['lcn'], r['name'].upper())
        if key in seen:
            continue
        seen.add(key)
        entries.append({'lcn': r['lcn'], 'scope': scope, 'name': r['name'], 'aka': r['aka'],
                        'company': r['company'], 'provinces': r['provinces']})
    return entries


def sort_entries(entries):
    return sorted(entries, key=lambda e: (e['lcn'], SCOPE_ORDER.get(e['scope'], 1), e['scope'], e['name'].upper()))


def table_hash(entries):
    blob = json.dumps(entries, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def discover(scope, cfg, fingerprint, fetcher=fetch):
    """Sceglie il PDF piu' recente valido per lo scope.

    Ritorna (candidato, righe) oppure (candidato, None) se il PDF e' identico a quello gia' elaborato.
    """
    page = fetcher(cfg['page'])
    links = find_pdf_links(page.text, cfg['page'])
    hints = [re.compile(h, re.I) for h in cfg.get('hints', [])]
    hinted = [l for l in links if any(h.search(l['text'] + ' ' + unquote(l['url'])) for h in hints)] if hints else links
    tiers = [hinted] + ([[l for l in links if l not in hinted]] if hints else [])

    last_error = None
    for tier in tiers:
        candidates = []
        for link in tier:
            try:
                resp = fetcher(link['url'])
            except OfficialSourceError as exc:
                last_error = exc
                continue
            info = read_pdf(resp.content)
            if not info or not classify(cfg, info['text']):
                continue
            candidates.append({
                'url': link['url'], 'text': link['text'], 'order': link['order'],
                'sha256': hashlib.sha256(resp.content).hexdigest(), 'data': resp.content,
                'doc_date': doc_date(link, info, resp.headers.get('Last-Modified'), link['url']),
            })
        candidates.sort(key=lambda c: (c['doc_date'], -c['order']), reverse=True)
        for cand in candidates:
            if fingerprint.get(scope, {}).get('sha256') == cand['sha256']:
                return cand, None
            try:
                rows = extract_rows(cand['data'])
            except Exception as exc:
                last_error = exc
                continue
            return cand, rows
    raise OfficialSourceError(f'{scope}: nessun PDF ufficiale valido trovato ({last_error})')


def _write_if_changed(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding='utf-8') == text:
        return False
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)
    return True


def dump(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + '\n'


def diff_entries(old, new):
    o = {(e['scope'], e['name'].upper(), e['lcn']) for e in old}
    n = {(e['scope'], e['name'].upper(), e['lcn']) for e in new}
    return {'added': sorted(n - o), 'removed': sorted(o - n)}


def discover_local(scope, cfg, fingerprint, pdf_root):
    """Sceglie il PDF locale piu' recente valido per lo scope (riconosciuto dal contenuto, non dal nome).

    Ritorna None se la cartella non contiene PDF, altrimenti (candidato, righe | None se identico al gia' elaborato).
    """
    folder = pdf_root / cfg['local_dir']
    files = sorted(folder.glob('*.pdf')) if folder.is_dir() else []
    if not files:
        return None
    candidates, last_error = [], None
    for path in files:
        data = path.read_bytes()
        info = read_pdf(data)
        if not info or not classify(cfg, info['text']):
            continue
        candidates.append({
            'url': path.relative_to(pdf_root.parent.parent).as_posix(), 'text': path.name, 'order': 0,
            'sha256': hashlib.sha256(data).hexdigest(), 'data': data,
            'doc_date': info['meta_date'] or _text_date(path.name),
        })
    candidates.sort(key=lambda c: (c['doc_date'], c['text']), reverse=True)
    for cand in candidates:
        if fingerprint.get(scope, {}).get('sha256') == cand['sha256']:
            return cand, None
        try:
            return cand, extract_rows(cand['data'])
        except Exception as exc:
            last_error = exc
    raise OfficialSourceError(f'{scope}: nessun PDF locale valido in {folder} ({last_error})')


def remote_check(scope, cfg, cand, fetcher):
    """Controllo facoltativo e solo diagnostico sul sito del Ministero: non modifica mai la tabella."""
    if not cfg.get('page'):
        return None
    try:
        remote, rows = discover(scope, cfg, {scope: {'sha256': cand['sha256']}}, fetcher)
    except Exception as exc:
        return {'status': 'non raggiungibile', 'detail': str(exc)[:200]}
    if rows is None:
        return {'status': 'identico al PDF locale'}
    newer = remote['doc_date'] > cand['doc_date']
    return {'status': 'PDF piu\' recente disponibile' if newer else 'PDF diverso dal locale',
            'url': remote['url'], 'doc_date': remote['doc_date'], 'local_doc_date': cand['doc_date']}


def active_scopes(config, region):
    """Scope nazionale piu' la sola regione scelta dall'utente (se configurata)."""
    scopes = config['scopes']
    chosen = {name: cfg for name, cfg in scopes.items() if cfg['kind'] == 'national'}
    if region:
        if region not in scopes or scopes[region]['kind'] == 'national':
            known = ', '.join(n for n, c in scopes.items() if c['kind'] != 'national')
            raise OfficialSourceError(f'regione "{region}" non configurata (disponibili: {known})')
        chosen[region] = scopes[region]
    return chosen


def load_region(path=REGION_FILE):
    if not path.exists():
        return ''
    return str((yaml.safe_load(path.read_text(encoding='utf-8')) or {}).get('region') or '').strip().lower()


def update(config, fetcher=fetch, table_file=TABLE_FILE, fingerprint_file=FINGERPRINT_FILE,
           pdf_root=PDF_DIR, region='', check_remote=False, remote_fetcher=None):
    table = json.loads(table_file.read_text(encoding='utf-8')) if table_file.exists() else {'entries': [], 'sources': {}}
    fingerprint = json.loads(fingerprint_file.read_text(encoding='utf-8')) if fingerprint_file.exists() else {}
    scopes = active_scopes(config, region) if region is not None and region != '' else config['scopes']
    entries = [e for e in table['entries'] if e['scope'] in scopes]
    sources = {k: v for k, v in table.get('sources', {}).items() if k in scopes}
    changes, errors = {}, []

    for scope, cfg in scopes.items():
        previous_scope = [e for e in entries if e['scope'] == scope]
        try:
            found = discover_local(scope, cfg, fingerprint, pdf_root) if cfg.get('local_dir') else None
            origin = 'local'
            if found is None:
                origin = 'remote'
                found = discover(scope, cfg, fingerprint, fetcher)
            cand, rows = found
            if rows is None and not previous_scope:
                # fingerprint presente ma tabella assente: ricostruzione forzata
                fingerprint.pop(scope, None)
                found = discover_local(scope, cfg, fingerprint, pdf_root) if origin == 'local' else discover(scope, cfg, fingerprint, fetcher)
                cand, rows = found
            if rows is None:
                changes[scope] = {'changed': False, 'source': origin, 'pdf': cand['url']}
            else:
                validate_rows(scope, cfg, rows, entries)
                new_scope = build_scope_entries(scope, rows)
                digest = table_hash(sort_entries(new_scope))
                if sources.get(scope, {}).get('table_sha256') == digest and previous_scope:
                    changes[scope] = {'changed': False, 'note': 'PDF ripubblicato con tabella identica'}
                else:
                    entries = [e for e in entries if e['scope'] != scope] + new_scope
                    sources[scope] = {'table_sha256': digest, 'rows': len(new_scope)}
                    changes[scope] = {'changed': True, **diff_entries(previous_scope, new_scope)}
                changes[scope].update({'source': origin, 'pdf': cand['url']})
            fingerprint[scope] = {'url': cand['url'], 'sha256': cand['sha256'], 'doc_date': cand['doc_date'],
                                  'link_text': cand['text']}
            if check_remote and origin == 'local':
                changes[scope]['remote_check'] = remote_check(scope, cfg, cand, remote_fetcher or fetcher)
        except Exception as exc:
            errors.append(f'{scope}: {exc}')
            changes[scope] = {'changed': False, 'error': str(exc)}

    new_table = {'schema': 1, 'sources': sources, 'entries': sort_entries(entries)}
    wrote = _write_if_changed(table_file, dump(new_table))
    _write_if_changed(fingerprint_file, dump({k: v for k, v in fingerprint.items() if k in scopes}))
    return {'changed': wrote, 'scopes': changes, 'errors': errors}


def load_table(table_file=TABLE_FILE):
    return json.loads(table_file.read_text(encoding='utf-8'))


def main():
    config = yaml.safe_load(CONFIG_FILE.read_text(encoding='utf-8'))
    region = load_region() or config.get('default_region', '')
    check = bool(config.get('remote_check', True)) and os.environ.get('LCN_REMOTE_CHECK', '1') != '0'
    try:
        result = update(config, region=region or None, check_remote=check,
                        remote_fetcher=lambda url: fetch(url, retries=1, timeout=10))
    except OfficialSourceError as exc:
        print('[ERRORE]', exc, file=sys.stderr)
        return 1
    OUTPUT_DIR.mkdir(exist_ok=True)
    CHANGES_FILE.write_text(dump(result), encoding='utf-8')
    print(f'Regione: {region or "(nessuna)"}')
    print('LCN ufficiale aggiornata:' if result['changed'] else 'LCN ufficiale invariata.')
    for scope, info in result['scopes'].items():
        remote = info.get('remote_check')
        print(f'  {scope}: changed={info["changed"]} fonte={info.get("source", "-")} pdf={info.get("pdf", "-")}'
              + (f' | MIMIT: {remote["status"]}' if remote else '') + (f' | {info["error"]}' if info.get('error') else ''))
    if result['errors']:
        for e in result['errors']:
            print('[ERRORE]', e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
