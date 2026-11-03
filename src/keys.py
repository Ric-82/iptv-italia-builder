"""Chiavi canoniche dei canali, condivise da parser, tabella LCN ufficiale e selector."""
import re

TECH_SUFFIX_RE = re.compile(r'^(?P<base>.+?)(?:FHD|UHD|HD|SD|4K|HEVC|H265)+(?:\d{2,3})?$')
KEY_ALIASES = {
    '20MEDIASET': '20',
    '27TWENTYSEVEN': 'TWENTYSEVEN',
}
# "4K" is part of the channel name here, not a quality suffix
PROTECTED_KEYS = {'RAI4K'}

# tag di qualita'/stato aggiunti dalle playlist, non fanno parte del nome editoriale
_NOISE_RE = re.compile(
    r'\s*(?:\((?:\d{3,4}[pi]|italy|italia|it)\)|\[[^\]]*\])',
    re.IGNORECASE,
)


def clean_display_name(name):
    name = _NOISE_RE.sub('', name or '')
    name = re.sub(r'\s+(?:FHD|UHD|HD|SD)$', '', name.strip(), flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', name).strip(' -_')


def raw_key(tvg_id, tvg_name, display_name):
    key = (tvg_id or tvg_name or display_name or '').upper()
    key = re.sub(r'\.IT$', '', key)
    key = re.sub(r'[^A-Z0-9]', '', key)
    return key


def normalize_key(key):
    # removes technical suffixes (HD, FHD, 4K...) and the trailing channel number that follows them
    prev = None
    while key != prev:
        prev = key
        key = KEY_ALIASES.get(key, key)
        if key in PROTECTED_KEYS:
            break
        m = TECH_SUFFIX_RE.match(key)
        if m:
            key = m.group('base')
    return key


def canonical_key(tvg_id, tvg_name, display_name):
    return normalize_key(raw_key(tvg_id, tvg_name, display_name))


def name_key(name):
    return canonical_key('', '', clean_display_name(name))
