"""Pulizia finale della playlist: nomi uniformi, deduplicazione di flussi e canali.

Opera dopo l'assegnazione LCN e la scelta del flusso migliore, senza modificarle.
"""
from collections import Counter, defaultdict
import re
import unicodedata

from keys import clean_display_name, name_key
from selector import pick_logo, rank_streams, set_streams, sort_text

DECORATIONS = [
    re.compile(r'\([^)]*\)'),
    re.compile(r'\s-\s*(pluto|rakuten tv)\b.*$', re.I),
    re.compile(r'\b(pluto tv|samsung channels|rakuten tv)\b', re.I),
    re.compile(r'\s(IT|TV)$', re.I),
]
NOISE = re.compile(r'\s+backup\s*$', re.I)
SUFFIXES = re.compile(r'\s-\s|\sItaly$')


def fold(value):
    value = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    return value.casefold().replace('&', 'and')


def loose_key(value):
    return re.sub(r'[^a-z0-9]+', '', fold(value))


def core_key(value):
    for pattern in DECORATIONS:
        value = pattern.sub(' ', value)
    return loose_key(value)


def clean_name(value):
    value = ''.join(c for c in value if unicodedata.category(c) not in ('So', 'Sk', 'Cs', 'Cf', 'Mn'))
    value = re.sub(r'\s+', ' ', value).strip()
    value = NOISE.sub('', value).strip()
    return clean_display_name(value) or value


def fix_case(name):
    # "NOve" -> "Nove": maiuscola iniziale doppia seguita da minuscole
    return ' '.join(w.capitalize() if re.fullmatch(r'[A-Z]{2}[a-z]{2,3}', w) else w for w in name.split(' '))


def _words(name):
    return re.findall(r"[^\W\d_]+", name)


def case_penalty(name):
    words = _words(name)
    mixed = any(w != w.upper() for w in words)
    penalty = sum(1 for w in words if mixed and w.isupper() and len(w) > 3)
    penalty += sum(1 for w in name.split(' ') if re.fullmatch(r'[A-Z]{2}[a-z]{2,3}', w))
    return penalty


def tv_penalty(name):
    return sum(1 for w in _words(name) if w == 'Tv')


def glued_penalty(name):
    return len(re.findall(r'\b[A-Z][a-z]+[A-Z][a-z]+|[a-z][A-Z]{2,}\b', name))


def decoration_penalty(name):
    return sum(1 for p in DECORATIONS[:3] if p.search(name)) + len(SUFFIXES.findall(name))


def merge_key(name):
    """Chiave editoriale: ignora '- Pluto', '- Rakuten TV', '(...)' e il suffisso Italy/IT."""
    name = re.sub(r'\([^)]*\)', ' ', name)
    name = re.sub(r'\s-\s*(pluto|rakuten tv)\b.*$', ' ', name, flags=re.I)
    name = re.sub(r'\s(italy|it)$', '', name.strip(), flags=re.I)
    return loose_key(name)


def base_key(name):
    """Chiave del nome prima di ' - ' (es. 'HGTV - Home&Garden' -> 'hgtv'); vuota se troppo corta."""
    if ' - ' not in name:
        return ''
    base = merge_key(name.split(' - ', 1)[0])
    return base if len(base) >= 4 else ''


def accents(name):
    return sum(1 for c in name if ord(c) > 127 and c.isalpha())


def choose_name(counts):
    def score(name):
        return (case_penalty(name), decoration_penalty(name), glued_penalty(name),
                -accents(name), tv_penalty(name), -counts[name], len(name), name)
    return min(counts, key=score)


def member_names(channels, keys):
    counts = Counter()
    for key in keys:
        for entry in channels[key]:
            n = clean_name(entry.get('editorial_name') or '')
            if n:
                counts[fix_case(n)] += 1
    return counts


def resolve_name(item, channels, keys, overrides):
    counts = member_names(channels, keys)
    name = choose_name(counts) if counts else item['name']
    if name_key(name) in overrides:
        return overrides[name_key(name)]
    official = clean_name(item['official_name']) if item.get('official_name') else ''
    # il nome ufficiale prevale sulla scrittura, se coincide con il canale e non e' tutto maiuscolo
    if official and name_key(official) == name_key(name) and official != official.upper() \
            and case_penalty(official) == 0:
        return official
    return name


class Groups:
    def __init__(self, items):
        self.parent = list(range(len(items)))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a, b):
        self.parent[self.find(b)] = self.find(a)


def compatible(a, b):
    ca, cb = core_key(a), core_key(b)
    return bool(ca and cb) and (ca == cb or (min(len(ca), len(cb)) >= 4 and (ca in cb or cb in ca)))


def merge_plain(plain, merges=None):
    merges = merges or {}
    groups = Groups(plain)
    by_loose, by_url, by_merge = defaultdict(list), defaultdict(list), defaultdict(list)
    for idx, item in enumerate(plain):
        by_loose[loose_key(item['name'])].append(idx)
        by_url[item['best']['url']].append(idx)
        key = merge_key(item['name'])
        by_merge[merges.get(key, key)].append(idx)
    for index in (by_loose, by_merge):
        for idxs in index.values():
            for other in idxs[1:]:
                groups.union(idxs[0], other)
    # varianti "Base - Qualcosa" si uniscono solo se trasmettono lo stesso flusso
    for idxs in by_url.values():
        for a in idxs:
            for b in idxs:
                if a < b and (compatible(plain[a]['name'], plain[b]['name'])
                              or (base_key(plain[a]['name']) and base_key(plain[a]['name']) == base_key(plain[b]['name']))):
                    groups.union(a, b)
    clusters = defaultdict(list)
    for idx in range(len(plain)):
        clusters[groups.find(idx)].append(plain[idx])
    return list(clusters.values())


def stream_key(stream):
    return (stream.get('probe_status') == 'ok', stream.get('score') or 0)


def union_streams(items):
    seen, streams = set(), []
    for item in items:
        for s in item['streams']:
            if s['url'] not in seen:
                seen.add(s['url'])
                streams.append(s)
    return rank_streams(streams)


def finalize(final, channels, logo_priority, overrides, merges=None):
    merges = {merge_key(alias): merge_key(target)
              for target, aliases in (merges or {}).items() for alias in aliases}
    numbered = [i for i in final if i['lcn'] is not None]
    plain = [i for i in final if i['lcn'] is None]
    for item in final:
        item['members'] = [item['channel']]
    before_names = {id(i): i['name'] for i in final}
    for item in final:
        item['name'] = resolve_name(item, channels, item['members'], overrides)

    # 4) numerati con lo stesso URL finale: resta la prima occorrenza in ordine LCN
    seen, kept, removed_numbered = {}, [], []
    for item in numbered:
        first = seen.get(item['best']['url'])
        if first:
            removed_numbered.append({
                'url': item['best']['url'],
                'kept': {'lcn': first['lcn'], 'scope': first['scope'], 'name': first['name']},
                'removed': {'lcn': item['lcn'], 'scope': item['scope'], 'official_name': item['official_name'],
                            'channel': item['channel']},
            })
            continue
        seen[item['best']['url']] = item
        kept.append(item)
    numbered = kept

    # 3) alfabetici con lo stesso URL di un numerato: resta solo il numerato
    removed_plain_numbered, rest = [], []
    for item in plain:
        owner = seen.get(item['best']['url'])
        if owner:
            removed_plain_numbered.append({'name': item['name'], 'channel': item['channel'],
                                           'url': item['best']['url'],
                                           'numbered': {'lcn': owner['lcn'], 'name': owner['name']}})
        else:
            rest.append(item)
    plain = rest

    # alfabetici che rappresentano un canale numerato con URL diverso: diventano backup del numerato
    by_loose = {}
    for item in numbered:
        by_loose.setdefault(merge_key(item['name']), item)
    folded, rest = [], []
    for item in plain:
        owner = by_loose.get(merge_key(item['name'])) or by_loose.get(base_key(item['name']))
        if owner:
            owner['members'] += item['members']
            owner['streams'] = union_streams([owner, item])
            folded.append({'name': item['name'], 'channel': item['channel'], 'url': item['best']['url'],
                           'numbered': {'lcn': owner['lcn'], 'name': owner['name']}})
        else:
            rest.append(item)
    plain = rest

    # 2) duplicati alfabetici: un solo canale editoriale, gli altri flussi restano backup
    merged_report, merged = [], []
    for cluster in merge_plain(plain, merges):
        if len(cluster) == 1:
            merged.append(cluster[0])
            continue
        cluster.sort(key=lambda i: stream_key(i['best']), reverse=True)
        primary = cluster[0]
        for other in cluster[1:]:
            primary['members'] += other['members']
        primary['streams'] = union_streams(cluster)
        primary['name'] = resolve_name(primary, channels, primary['members'], overrides)
        merged_report.append({
            'name': primary['name'],
            'final_url': primary['streams'][0]['url'],
            'variants': [{'channel': i['channel'], 'name': i['name'], 'url': i['best']['url'],
                          'playlist': i['best'].get('playlist')} for i in cluster],
            'differences': sorted({i['name'] for i in cluster} - {primary['name']}),
            'same_url': len({i['best']['url'] for i in cluster}) == 1,
        })
        merged.append(primary)
    plain = merged

    for item in numbered + plain:
        set_streams(item, item['streams'])
        entries = [e for key in item['members'] for e in channels[key]]
        item['tvg_logo'] = pick_logo(entries, logo_priority)
    plain.sort(key=lambda i: (sort_text(i['name']), i['channel']))
    result = numbered + plain

    renamed = [{'channel': i['channel'], 'from': before_names[id(i)], 'to': i['name']}
               for i in result if before_names.get(id(i)) not in (None, i['name'])]
    return result, {
        'numbered_duplicates_removed': removed_numbered,
        'plain_removed_as_numbered': removed_plain_numbered,
        'plain_folded_into_numbered': folded,
        'plain_merged': merged_report,
        'names_normalized': renamed,
    }
