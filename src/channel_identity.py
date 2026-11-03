"""Rilevamento di identificativi tecnici e risoluzione dei nomi editoriali."""
import re
from collections import defaultdict

from keys import clean_display_name

_TECH_PATTERNS = [
    re.compile(r'^IT(?=(?:[A-Z0-9]*\d){4})[A-Z0-9]{7,14}$'),                    # ITBC11000047F, IT3800010K1, IT700005C9
    re.compile(r'^[0-9A-F]{16,}$'),                         # hash / ObjectId esadecimali
    re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I),
    re.compile(r'^\d{5,}$'),
    re.compile(r'^[A-Za-z0-9]{12,}$'),                      # token opachi senza spazi (con cifre, vedi is_technical_id)
    re.compile(r'^[a-z0-9]+(?:[_-][a-z0-9]+)*[_-]\d{3,}$'),  # nomi macchina: slug_1234
    re.compile(r'^(?:plu|pluto)[-_ ]?[0-9a-f]{16,}$', re.I),
]


def is_technical_id(value):
    v = (value or '').strip()
    if not v:
        return True
    if ' ' in v:
        return False
    for i, pat in enumerate(_TECH_PATTERNS):
        if pat.match(v):
            if i == 4:
                # token lunghi: tecnici solo se mescolano cifre e lettere
                return bool(re.search(r'\d', v)) and bool(re.search(r'[A-Za-z]', v))
            return True
    return False


def pick_editorial_name(tvg_name, display_name):
    for candidate in (tvg_name, display_name):
        name = clean_display_name(candidate)
        if name and not is_technical_id(name):
            return name
    return ''


def _url_identity(url):
    return re.sub(r'[?#].*$', '', url or '').strip().lower()


def resolve_missing_names(entries):
    """Per le voci senza nome editoriale cerca lo stesso canale in altre sorgenti (stesso flusso, tvg-id o logo).

    Restituisce l'elenco con il campo 'editorial_name' valorizzato dove possibile.
    """
    by_url, by_id, by_logo = defaultdict(set), defaultdict(set), defaultdict(set)
    for e in entries:
        name = e.get('editorial_name')
        if not name:
            continue
        if e.get('url'):
            by_url[_url_identity(e['url'])].add(name)
        if e.get('tvg_id'):
            by_id[e['tvg_id']].add(name)
        if e.get('tvg_logo'):
            by_logo[e['tvg_logo']].add(name)

    for e in entries:
        if e.get('editorial_name'):
            continue
        for index, key in ((by_url, _url_identity(e.get('url'))), (by_id, e.get('tvg_id')), (by_logo, e.get('tvg_logo'))):
            names = index.get(key) if key else None
            # accettato solo se non ambiguo
            if names and len(names) == 1:
                e['editorial_name'] = next(iter(names))
                break
    return entries


class UnionFind:
    def __init__(self, weight=None):
        self.parent = {}
        self.weight = weight or {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # rappresentante: chiave con piu' voci, poi la minore (risultato deterministico)
            lo, hi = sorted((ra, rb), key=lambda k: (-self.weight.get(k, 0), k))
            self.parent[hi] = lo


def build_alias_map(channel_map, max_group=3):
    """Unifica le chiavi che condividono lo stesso flusso con una voce dall'identificativo tecnico.

    Solo gli URL visti in almeno una voce con tvg-id tecnico generano alias: sono i casi in cui lo stesso
    canale editoriale compare con piu' codici. Il rappresentante e' la chiave con piu' voci (a parita' la minore).
    Gli URL condivisi da piu' di max_group chiavi sono ignorati (segnaposto/URL generici).
    """
    by_url = defaultdict(set)
    technical_urls = set()
    for key, entries in channel_map.items():
        for e in entries:
            u = _url_identity(e.get('url'))
            if not u:
                continue
            by_url[u].add(key)
            if e.get('technical'):
                technical_urls.add(u)
    weight = {k: len(v) for k, v in channel_map.items()}
    uf = UnionFind(weight)
    for u, keys in by_url.items():
        if u in technical_urls and 1 < len(keys) <= max_group:
            ordered = sorted(keys)
            for other in ordered[1:]:
                uf.union(ordered[0], other)
    return {k: uf.find(k) for k in sorted(uf.parent) if uf.find(k) != k}