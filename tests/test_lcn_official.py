import json
from types import SimpleNamespace

import pytest

import lcn_official as lo

CFG = {'scopes': {'national': {'kind': 'national', 'page': 'http://page/', 'min_rows': 2, 'max_drop_pct': 30}}}
HTML = '<a href="/docs/{a}.pdf">Elenco</a><a href="/docs/{b}.pdf">Vecchio elenco</a><a href="/note.pdf">Note</a>'


def rows(*pairs):
    return [{'lcn': n, 'name': name, 'aka': [], 'company': '', 'provinces': []} for n, name in pairs]


BASE = rows((1, 'Rai 1 HD'), (2, 'Rai 2 HD'), (3, 'Rai 3 HD'), (5, 'CANALE 5'), (7, 'La7'))


@pytest.fixture
def env(tmp_path, monkeypatch):
    st = {'html': HTML.format(a='zzz_new', b='aaa_old'), 'rows': BASE,
          'pdfs': {'/docs/zzz_new.pdf': b'new', '/docs/aaa_old.pdf': b'old', '/note.pdf': b'note'}}

    def fetcher(url):
        if url == 'http://page/':
            return SimpleNamespace(text=st['html'], content=b'', headers={})
        return SimpleNamespace(text='', content=st['pdfs'][url.replace('http://page', '')], headers={})

    def read_pdf(data):
        if data == b'note':
            return {'text': 'Nota informativa', 'meta_date': '2025-12-31'}
        date = '2024-01-01' if data == b'old' else '2025-12-10'
        return {'text': 'FORNITORE MARCHIO LCN ASSEGNATO', 'meta_date': date}

    monkeypatch.setattr(lo, 'read_pdf', read_pdf)
    monkeypatch.setattr(lo, 'extract_rows', lambda data: rows((1, 'Vecchia')) if data == b'old' else st['rows'])
    table, fp = tmp_path / 'table.json', tmp_path / 'fp.json'
    return SimpleNamespace(st=st, table=table, fp=fp,
                           run=lambda: lo.update(CFG, fetcher=fetcher, table_file=table, fingerprint_file=fp))


def test_picks_latest_pdf_by_content_not_name(env):
    result = env.run()
    assert result['changed'] and not result['errors']
    assert json.loads(env.fp.read_text(encoding='utf-8'))['national']['url'].endswith('zzz_new.pdf')
    assert [e['lcn'] for e in json.loads(env.table.read_text(encoding='utf-8'))['entries']] == [1, 2, 3, 5, 7]


def test_renamed_pdf_is_still_found(env):
    env.st['html'] = HTML.format(a='totally_different_name', b='aaa_old')
    env.st['pdfs']['/docs/totally_different_name.pdf'] = b'new'
    assert env.run()['changed']


def test_unchanged_sources_leave_files_untouched(env):
    env.run()
    before = (env.table.read_bytes(), env.fp.read_bytes(), env.table.stat().st_mtime_ns)
    assert not env.run()['changed']
    assert (env.table.read_bytes(), env.fp.read_bytes(), env.table.stat().st_mtime_ns) == before


def test_republished_pdf_with_same_table_keeps_numbering(env):
    env.run()
    before = env.table.read_bytes()
    env.st['pdfs']['/docs/zzz_new.pdf'] = b'new-republished'
    result = env.run()
    assert not result['changed'] and env.table.read_bytes() == before
    assert result['scopes']['national']['changed'] is False


def test_changed_table_is_rebuilt_with_diff(env):
    env.run()
    env.st['pdfs']['/docs/zzz_new.pdf'] = b'new2'
    env.st['rows'] = BASE + rows((8, 'TV8'))
    result = env.run()
    assert result['changed']
    assert result['scopes']['national']['added'] == [('national', 'TV8', 8)]


def test_unreachable_or_changed_page_keeps_previous_table(env):
    env.run()
    before = env.table.read_bytes()
    env.st['html'] = '<p>pagina cambiata</p>'
    result = env.run()
    assert result['errors'] and not result['changed']
    assert env.table.read_bytes() == before


def test_suspicious_drop_is_rejected(env):
    env.run()
    before = env.table.read_bytes()
    env.st['pdfs']['/docs/zzz_new.pdf'] = b'new2'
    env.st['rows'] = rows((1, 'Rai 1 HD'), (2, 'Rai 2 HD'))
    assert env.run()['errors']
    assert env.table.read_bytes() == before


def test_text_dates():
    assert lo._text_date('LCN AT17 - Sicilia 26 ottobre 2026') == '2026-10-26'
    assert lo._text_date('LCN_PUBBLICAZIONE_10.12.2025.pdf') == '2025-12-10'


def test_local_pdfs_are_used_without_network(tmp_path, monkeypatch):
    folder = tmp_path / 'pdfs' / 'nazionale'
    folder.mkdir(parents=True)
    (folder / 'qualunque_nome.pdf').write_bytes(b'new')
    monkeypatch.setattr(lo, 'read_pdf', lambda d: {'text': 'FORNITORE MARCHIO LCN', 'meta_date': '2025-12-10'})
    monkeypatch.setattr(lo, 'extract_rows', lambda d: BASE)

    def offline(url):
        raise lo.OfficialSourceError('timeout')

    cfg = {'scopes': {'national': {**CFG['scopes']['national'], 'local_dir': 'nazionale'}}}
    table, fp = tmp_path / 't.json', tmp_path / 'f.json'
    result = lo.update(cfg, fetcher=offline, table_file=table, fingerprint_file=fp,
                       pdf_root=tmp_path / 'pdfs', check_remote=True)
    assert result['changed'] and not result['errors']
    assert result['scopes']['national']['source'] == 'local'
    assert result['scopes']['national']['remote_check']['status'] == 'non raggiungibile'
    assert not lo.update(cfg, fetcher=offline, table_file=table, fingerprint_file=fp, pdf_root=tmp_path / 'pdfs')['changed']


def test_only_selected_region_is_active():
    cfg = {'scopes': {'national': {'kind': 'national'}, 'sicilia': {'kind': 'area_tecnica'},
                      'lombardia': {'kind': 'area_tecnica'}}}
    assert set(lo.active_scopes(cfg, 'lombardia')) == {'national', 'lombardia'}
    with pytest.raises(lo.OfficialSourceError):
        lo.active_scopes(cfg, 'atlantide')
