from collections import Counter

import cleanup
from selector import set_streams


def stream(url, score=0, status='ok', playlist='A', logo=''):
    return {'editorial_name': '', 'url': url, 'score': score, 'probe_status': status, 'playlist': playlist,
            'tvg_logo': logo}


def entry(name, url, playlist='A', logo='', score=0):
    return {'editorial_name': name, 'url': url, 'score': score, 'probe_status': 'ok', 'playlist': playlist,
            'tvg_logo': logo}


def make_item(key, name, streams, lcn=None, scope=None, official=None):
    item = {'lcn': lcn, 'scope': scope, 'official_name': official, 'channel': key, 'name': name,
            'tvg_logo': '', 'members': [key]}
    set_streams(item, streams)
    return item


def run(items, channels, prio=None, overrides=None):
    return cleanup.finalize(items, channels, prio or {'LiveTVCollector': 0, 'A': 1}, overrides or {})


def test_choose_name_prefers_consistent_form():
    assert cleanup.choose_name(Counter({'Radio KISS KISS TV': 3, 'Radio Kiss Kiss TV': 1})) == 'Radio Kiss Kiss TV'
    assert cleanup.choose_name(Counter({'RadioFreccia': 3, 'Radio Freccia': 1})) == 'Radio Freccia'
    assert cleanup.choose_name(Counter({'è Tv Marche': 1, 'eTv Marche': 1})) == 'è Tv Marche'
    assert cleanup.choose_name(Counter({'Fashion TV': 1, 'FashionTV': 1})) == 'Fashion TV'
    assert cleanup.fix_case('NOve') == 'Nove'


def test_override_and_official_name_precedence():
    channels = {'NOVE': [entry('NOve', 'u1')], 'RAI1': [entry('RAI 1', 'u2')], 'LA7': [entry('LA7', 'u3')]}
    items = [make_item('RAI1', 'RAI 1', [channels['RAI1'][0]], 1, 'national', 'Rai 1 HD'),
             make_item('LA7', 'LA7', [channels['LA7'][0]], 7, 'national', 'La7'),
             make_item('NOVE', 'NOve', [channels['NOVE'][0]])]
    final, _ = run(items, channels, overrides={'NOVE': 'Nove'})
    assert [i['name'] for i in final] == ['Rai 1', 'La7', 'Nove']


def test_numbered_same_url_keeps_first_lcn_and_reports():
    s = entry('Rai 3', 'same')
    channels = {'RAI3': [s], 'RAI3B': [entry('Rai 3', 'same')]}
    items = [make_item('RAI3', 'Rai 3', [s], 3, 'national', 'Rai 3 HD'),
             make_item('RAI3B', 'Rai 3', [channels['RAI3B'][0]], 103, 'national', 'Rai 3 HD')]
    final, report = run(items, channels)
    assert [i['lcn'] for i in final] == [3]
    assert report['numbered_duplicates_removed'][0]['removed']['lcn'] == 103


def test_plain_with_numbered_url_is_removed():
    n, p = entry('Italia 1', 'u'), entry('Italia Uno Europa', 'u')
    channels = {'ITALIA1': [n], 'EU': [p]}
    items = [make_item('ITALIA1', 'Italia 1', [n], 6, 'national', 'Italia 1'), make_item('EU', 'Italia Uno Europa', [p])]
    final, report = run(items, channels)
    assert [i['name'] for i in final] == ['Italia 1']
    assert len(report['plain_removed_as_numbered']) == 1


def test_plain_duplicates_are_merged_with_backups_and_livetv_logo():
    a = entry('Barbie & Friends', 'u1', 'A', 'logoA', score=100)
    b = entry('Barbie & Friends', 'u2', 'LiveTVCollector', 'logoLT', score=50)
    channels = {'PLUTOBARBIE': [a], 'BARBIEFRIENDS': [b], 'ZULU': [entry('Zulu', 'u3')]}
    items = [make_item('PLUTOBARBIE', 'Barbie & Friends', [a]), make_item('BARBIEFRIENDS', 'Barbie & Friends', [b]),
             make_item('ZULU', 'Zulu', [channels['ZULU'][0]])]
    final, report = run(items, channels)
    assert [i['name'] for i in final] == ['Barbie & Friends', 'Zulu']
    merged = final[0]
    assert merged['best']['url'] == 'u1' and merged['backup1']['url'] == 'u2'
    assert merged['tvg_logo'] == 'logoLT'
    assert report['plain_merged'][0]['same_url'] is False


def test_plain_same_name_as_numbered_becomes_backup():
    n, p = entry('Radio Freccia', 'u1'), entry('RadioFreccia', 'u2')
    channels = {'RF': [n], 'RF2': [p]}
    items = [make_item('RF', 'Radio Freccia', [n], 258, 'national', 'RADIOFRECCIA'), make_item('RF2', 'RadioFreccia', [p])]
    final, report = run(items, channels)
    assert len(final) == 1 and final[0]['backup1']['url'] == 'u2'
    assert len(report['plain_folded_into_numbered']) == 1


def test_unrelated_channels_with_different_urls_stay_separate():
    channels = {'A1': [entry('Alfa', 'u1')], 'B1': [entry('Beta', 'u2')]}
    items = [make_item('A1', 'Alfa', channels['A1']), make_item('B1', 'Beta', channels['B1'])]
    final, _ = run(items, channels)
    assert [i['name'] for i in final] == ['Alfa', 'Beta']


def test_suffix_variants_are_merged_by_name_with_different_urls():
    names = ['Affare fatto', 'Affare Fatto - Pluto', 'Andromeda', 'Andromeda Italy']
    channels = {f'K{n}': [entry(name, f'u{n}')] for n, name in enumerate(names)}
    items = [make_item(f'K{n}', name, channels[f'K{n}']) for n, name in enumerate(names)]
    final, _ = run(items, channels)
    assert [i['name'] for i in final] == ['Affare fatto', 'Andromeda']
    assert all(i['backup1'] for i in final)


def test_dash_variant_folds_into_numbered_base():
    n, p = entry('HGTV', 'u1'), entry('HGTV - Home&Garden', 'u2')
    channels = {'HGTV': [n], 'HG2': [p]}
    items = [make_item('HGTV', 'HGTV', [n], 56, 'national', 'HGTV'), make_item('HG2', 'HGTV - Home&Garden', [p])]
    final, _ = run(items, channels)
    assert len(final) == 1 and final[0]['lcn'] == 56 and final[0]['backup1']['url'] == 'u2'


def test_pluto_suffix_does_not_merge_distinct_channels():
    channels = {'A': [entry('Film - Pluto', 'u1')], 'B': [entry('Film Azione', 'u2')]}
    items = [make_item('A', 'Film - Pluto', channels['A']), make_item('B', 'Film Azione', channels['B'])]
    final, _ = run(items, channels)
    assert len(final) == 2


def test_livetv_logo_survives_url_dedup():
    import parser as p
    a = {'playlist': 'OpenIPTVItaly', 'url': 'u', 'tvg_logo': 'logoOpen', 'tvg_chno': '', 'group_title': ''}
    b = {'playlist': 'LiveTVCollector', 'url': 'u', 'tvg_logo': 'logoLT', 'tvg_chno': '', 'group_title': ''}
    kept = p.dedupe_by_url([a, b])
    assert len(kept) == 1
    from selector import pick_logo
    assert pick_logo(kept, {'LiveTVCollector': 0, 'OpenIPTVItaly': 3}) == 'logoLT'

def test_base_dash_variants_not_merged_unless_same_stream():
    names = ['Mediaset Italia', 'Mediaset Italia - America', 'Mediaset Italia - Australia e Asia']
    channels = {'M0': [entry(names[0], 'u0')], 'M1': [entry(names[1], 'u1')], 'M2': [entry(names[2], 'u1')]}
    items = [make_item(k, n, channels[k]) for k, n in zip(channels, names)]
    final, _ = run(items, channels)
    assert [i['name'] for i in final] == ['Mediaset Italia', 'Mediaset Italia - America']
    assert final[0]['best']['url'] == 'u0'

def test_explicit_merge_map_unifies_confirmed_variants_with_different_urls():
    names = ['Mediaset Italia', 'Mediaset Italia - America', 'Mediaset Italia - Australia e Asia']
    channels = {'M0': [entry(names[0], 'u0')], 'M1': [entry(names[1], 'u1')], 'M2': [entry(names[2], 'u2')]}
    items = [make_item(k, n, channels[k]) for k, n in zip(channels, names)]
    merges = {'Mediaset Italia - America': ['Mediaset Italia - Australia e Asia']}
    final, _ = cleanup.finalize(items, channels, {'A': 1}, {}, merges)
    assert [i['name'] for i in final] == ['Mediaset Italia', 'Mediaset Italia - America']
    assert final[1]['backup1']['url'] in ('u1', 'u2')