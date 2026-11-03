import pytest

from channel_identity import build_alias_map, is_technical_id, pick_editorial_name, resolve_missing_names
from keys import clean_display_name, name_key


@pytest.mark.parametrize('value', [
    'ITBC11000047F', 'IT3800010K1', '699D87093D5DBA980705CCA8',
    '6786235bd648bf0008c2780a', '123456', '',
])
def test_technical_ids(value):
    assert is_technical_id(value)


@pytest.mark.parametrize('value', ['Rai 1', '20', 'Italianissima', 'Pluto TV Film', 'TV2000', 'Studio Uno', 'ITALIA1'])
def test_editorial_names_are_not_technical(value):
    assert not is_technical_id(value)


def test_clean_display_name_removes_noise():
    assert clean_display_name('Rai Scuola [Geo-blocked]') == 'Rai Scuola'
    assert clean_display_name('Discovery Channel (Italy) (1080p)') == 'Discovery Channel'
    assert clean_display_name('LA7 HD') == 'LA7'


def test_keys_ignore_quality_suffix():
    assert name_key('Rai 1 HD') == name_key('Rai 1')
    assert name_key('20 [Geo-blocked]') == name_key('20')


def test_pick_editorial_name_falls_back_to_display_name():
    assert pick_editorial_name('ITBC11000047F', 'CHILI - Film Romantici') == 'CHILI - Film Romantici'
    assert pick_editorial_name('', '699D87093D5DBA980705CCA8') == ''
