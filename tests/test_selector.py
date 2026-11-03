import selector as sel
from channel_identity import is_technical_id

PRIO = {'A': 1, 'B': 2}


def ch(name, url='http://s/x', playlist='A', score=0, status='ok'):
    return {'editorial_name': name, 'url': url, 'playlist': playlist, 'score': score,
            'probe_status': status, 'tvg_logo': ''}


def table(*rows):
    return [{'lcn': n, 'scope': scope, 'name': name, 'aka': [], 'company': '', 'provinces': []}
            for n, scope, name in rows]


def build(channels, entries, aliases=None):
    return sel.build_playlists(channels, {}, entries, PRIO, aliases or {})


def test_numbered_first_by_lcn_then_alphabetical_tail():
    channels = {'ZETA': [ch('Zeta', 'u1')], 'ALFA': [ch('Alfa', 'u2')], 'RAI1': [ch('Rai 1', 'u3')],
                'CANALE5': [ch('Canale 5', 'u4')], 'ECO': [ch('Èco', 'u5')]}
    final, _ = build(channels, table((5, 'national', 'CANALE 5'), (1, 'national', 'Rai 1 HD')))
    assert [(i['lcn'], i['name']) for i in final] == [
        (1, 'Rai 1'), (5, 'Canale 5'), (None, 'Alfa'), (None, 'Èco'), (None, 'Zeta')]


def test_official_channels_without_stream_are_ignored():
    channels = {'RAI1': [ch('Rai 1', '')], 'RAI2': [ch('Rai 2', 'u')]}
    final, report = build(channels, table((1, 'national', 'Rai 1'), (2, 'national', 'Rai 2')))
    assert [i['name'] for i in final] == ['Rai 2']
    assert report['official_without_stream'][0]['lcn'] == 1


def test_multiple_official_numbers_keep_both_entries():
    final, _ = build({'RAI3': [ch('Rai 3', 'u', score=10)]},
                     table((3, 'national', 'Rai 3 HD'), (103, 'national', 'Rai 3 HD')))
    assert [i['lcn'] for i in final] == [3, 103]


def test_national_has_absolute_precedence_over_sicily():
    channels = {'TGS': [ch('TGS', 'u1')], 'ALTRO': [ch('Altro', 'u2')]}
    final, report = build(channels, table((12, 'national', 'ALTRO'), (12, 'sicilia', 'TGS')))
    assert [(i['lcn'], i['name']) for i in final] == [(12, 'Altro'), (None, 'TGS')]
    assert report['skipped'][0]['reason'] == 'precedenza nazionale'


def test_best_stream_prefers_verified_and_highest_score():
    channels = {'RAI1': [ch('Rai 1', 'bad', score=9999, status='timeout'), ch('Rai 1', 'low', score=10),
                         ch('Rai 1', 'high', score=500)]}
    final, _ = build(channels, [])
    assert final[0]['best']['url'] == 'high'
    assert final[0]['backup1']['url'] == 'low'


def test_name_aliases_and_discovery_prefix_match():
    channels = {'RETE4': [ch('Rete 4', 'u1')], 'NOVE': [ch('Nove', 'u2')]}
    final, _ = build(channels, table((4, 'national', 'RETEQUATTRO'), (9, 'national', 'Discovery NOVE')),
                     {'RETEQUATTRO': 'RETE4'})
    assert [i['lcn'] for i in final] == [4, 9]


def test_technical_names_never_reach_playlist():
    channels = {'X': [ch('ITBC11000047F', 'u')], 'RAI1': [ch('Rai 1', 'u2')]}
    final, report = build(channels, [])
    assert report['quarantined'][0]['channel'] == 'X'
    for text in sel.render(final):
        for line in text.splitlines():
            if line.startswith('#EXTINF'):
                assert not is_technical_id(line.rsplit(',', 1)[-1].split(' [')[0])


def test_output_is_deterministic():
    a = {'B': [ch('B', 'u1')], 'A': [ch('A', 'u2')]}
    b = dict(reversed(list(a.items())))
    assert sel.render(build(a, [])[0]) == sel.render(build(b, [])[0])


OFFICIAL = {'margin': 0.10, 'penalty': 0.10, 'suffixes': ('rai.it',), 'patterns': ()}


def test_official_domain_wins_within_margin():
    third = ch('X', 'http://cdn.example.com/a', score=1000)
    official = ch('X', 'https://mediapolis.rai.it/a', score=920)
    assert sel.rank_streams([third, official], OFFICIAL)[0]['url'] == official['url']


def test_third_party_wins_when_official_is_far_worse():
    third = ch('X', 'http://cdn.example.com/a', score=1000)
    official = ch('X', 'https://mediapolis.rai.it/a', score=800)
    assert sel.rank_streams([third, official], OFFICIAL)[0]['url'] == third['url']


def test_third_party_penalty_orders_remaining_streams():
    a = ch('X', 'http://one.example.com/a', score=1000)
    b = ch('X', 'https://mediapolis.rai.it/a', score=950)
    c = ch('X', 'http://two.example.com/a', score=960)
    assert [s['url'] for s in sel.rank_streams([a, c, b], OFFICIAL)][0] == b['url']


def test_unverified_official_never_outranks_verified():
    third = ch('X', 'http://cdn.example.com/a', score=1000)
    official = ch('X', 'https://mediapolis.rai.it/a', score=0, status='unreachable')
    assert sel.rank_streams([official, third], OFFICIAL)[0]['url'] == third['url']


def test_pick_epg_follows_source_priority_and_flags_conflicts():
    entries = [{'playlist': 'B', 'tvg_id': 'Rai1.it', 'group_title': 'Italy'},
               {'playlist': 'A', 'tvg_id': 'Rai.1.HD..101.it', 'group_title': 'Rai'}]
    epg = sel.pick_epg(entries, {'A': 0, 'B': 1})
    assert epg == {'tvg_id': 'Rai.1.HD..101.it', 'group_title': 'Rai', 'conflict': True}


def test_pick_epg_uses_ids_merged_by_url_and_skips_generic_group():
    entries = [{'playlist': 'A', 'tvg_id': '', 'group_title': 'Italy',
                'tvg_ids': {'B': 'X.it'}, 'groups': {'B': 'News'}}]
    epg = sel.pick_epg(entries, {'A': 0, 'B': 1})
    assert (epg['tvg_id'], epg['group_title'], epg['conflict']) == ('X.it', 'News', False)


def test_render_keeps_epg_header_and_attributes():
    item = {'lcn': 1, 'name': 'Rai 1', 'tvg_logo': 'l', 'tvg_id': 'Rai1.it', 'group_title': 'Rai',
            'best': {'url': 'http://a', 'playlist': 'A'}, 'backup1': None, 'backup2': None, 'fallback': []}
    m3u, full = sel.render([item], ['http://epg/1.xml.gz', 'http://epg/2.xml.gz'])
    assert m3u.splitlines()[0] == '#EXTM3U x-tvg-url="http://epg/1.xml.gz,http://epg/2.xml.gz" url-tvg="http://epg/1.xml.gz,http://epg/2.xml.gz"'
    assert 'tvg-id="Rai1.it"' in m3u and 'group-title="Rai"' in m3u and 'tvg-id="Rai1.it"' in full
