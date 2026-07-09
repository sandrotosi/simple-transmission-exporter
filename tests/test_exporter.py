from pathlib import Path

import pytest
from transmission_rpc.error import TransmissionConnectError, TransmissionError

import simple_transmission_exporter as ste

GOLDEN_DIR = Path(__file__).parent

# Same input used to generate tests/golden_metrics.txt.
GOLDEN_VALUES = {
    'stats': {
        'download_speed': 1000,
        'upload_speed': 2000,
        'cumulative_stats': {'downloaded_bytes': 10, 'files_added': 20, 'seconds_active': 30, 'session_count': 40, 'uploaded_bytes': 50},
        'current_stats': {'downloaded_bytes': 11, 'files_added': 21, 'seconds_active': 31, 'session_count': 41, 'uploaded_bytes': 51},
    },
    'free_space': 123456789,
    'torrents': {'stopped': 1, 'downloading': 2, 'seeding': 3},
    'collectors_ok': {'stats': True, 'free_space': True, 'torrents': True},
    'duration': 0.125,
}

ALL_STATUS_SUFFIXES = [suffix for _, suffix in ste._STATUS_METRICS]


def _values(status_counts):
    out = dict(GOLDEN_VALUES)
    out['torrents'] = status_counts
    return out


# --- golden metric-name test (folded Phase 5: drift protection) --------------

def test_render_matches_golden():
    """Locks every emitted metric name byte-for-byte so the Grafana dashboard
    (ID 13265) never silently breaks. The version label is templatized."""
    expected = (GOLDEN_DIR / 'golden_metrics.txt').read_text().rstrip('\n')
    expected = expected.replace('__VERSION__', ste.__version__)
    actual = ste.render_metrics(GOLDEN_VALUES, up=True, collected_at=1700000000.0)
    assert actual == expected


def test_render_without_values_emits_only_health():
    """A failed startup (no successful poll yet) still serves valid metrics."""
    out = ste.render_metrics(None, up=False, collected_at=0.0)
    assert 'transmission_up 0' in out
    assert 'transmission_exporter_build_info' in out
    assert 'transmission_downloadSpeed' not in out  # no data metrics


# --- status mapping ----------------------------------------------------------

def test_status_mapping_covers_all_states():
    counts = {
        'stopped': 5, 'check pending': 1, 'checking': 2, 'download pending': 3,
        'downloading': 4, 'seed pending': 6, 'seeding': 7,
    }
    out = ste.render_metrics(_values(counts), up=True, collected_at=0.0)
    assert 'transmission_status_paused 5' in out
    assert 'transmission_status_queued_to_check 1' in out
    assert 'transmission_status_checking 2' in out
    assert 'transmission_status_queued_to_download 3' in out
    assert 'transmission_status_downloading 4' in out
    assert 'transmission_status_queued_to_seed 6' in out
    assert 'transmission_status_seeding 7' in out


def test_status_zero_init_for_absent_states():
    out = ste.render_metrics(_values({}), up=True, collected_at=0.0)
    for suffix in ALL_STATUS_SUFFIXES:
        assert f'transmission_status_{suffix} 0' in out


# --- config validation -------------------------------------------------------

def test_require_env_exits_when_missing(monkeypatch):
    monkeypatch.delenv('DOES_NOT_EXIST', raising=False)
    with pytest.raises(SystemExit) as exc:
        ste._require_env('DOES_NOT_EXIST')
    assert exc.value.code != 0


def test_parse_int_exits_on_non_integer():
    with pytest.raises(SystemExit) as exc:
        ste._parse_int('SOME_VAR', 'not-a-number')
    assert exc.value.code != 0


def test_parse_int_accepts_integer():
    assert ste._parse_int('SOME_VAR', '42') == 42


# --- collector failure handling ----------------------------------------------

SECTION_NAMES = [name for name, _ in ste._SECTIONS]


def _sections(**overrides):
    """A _SECTIONS table returning golden data, with per-name overrides."""
    collectors = {
        'stats': lambda client: GOLDEN_VALUES['stats'],
        'free_space': lambda client: GOLDEN_VALUES['free_space'],
        'torrents': lambda client: GOLDEN_VALUES['torrents'],
    }
    collectors.update(overrides)
    return tuple((name, collectors[name]) for name in SECTION_NAMES)


@pytest.fixture
def poll_env(monkeypatch):
    """Isolate _poll_once from the network and from previous global state."""
    monkeypatch.setattr(ste, '_get_client', lambda: object())
    monkeypatch.setattr(ste, '_client', object())
    monkeypatch.setattr(ste, '_last_values', None)
    monkeypatch.setattr(ste, '_last_poll_ok', False)
    monkeypatch.setattr(ste, '_last_success_ts', 0.0)
    return monkeypatch


def test_rpc_error_fails_only_that_section(poll_env):
    def boom(client):
        raise TransmissionError('Query failed with result "No such file or directory".')

    poll_env.setattr(ste, '_SECTIONS', _sections(free_space=boom))
    ste._poll_once()
    assert ste._last_poll_ok is True  # the daemon answered: still up
    assert ste._last_values['stats'] == GOLDEN_VALUES['stats']
    assert ste._last_values['free_space'] is None  # no earlier value to keep
    assert ste._last_values['torrents'] == GOLDEN_VALUES['torrents']
    assert ste._last_values['collectors_ok'] == {'stats': True, 'free_space': False, 'torrents': True}
    assert ste._client is not None  # connection was fine, no reconnect churn


def test_failed_section_keeps_last_known_value(poll_env):
    poll_env.setattr(ste, '_SECTIONS', _sections())
    ste._poll_once()  # a good poll first

    def boom(client):
        raise TransmissionError('Query failed with result "No such file or directory".')

    poll_env.setattr(ste, '_SECTIONS', _sections(free_space=boom))
    ste._poll_once()
    assert ste._last_values['free_space'] == GOLDEN_VALUES['free_space']  # cached
    assert ste._last_values['collectors_ok']['free_space'] is False


@pytest.mark.parametrize('exc', [
    TransmissionConnectError('connection refused'),  # subclasses TransmissionError!
    ConnectionError('socket error'),                 # anything non-RPC-level
])
def test_transport_error_fails_poll_but_keeps_values(poll_env, exc):
    poll_env.setattr(ste, '_SECTIONS', _sections())
    ste._poll_once()  # a good poll first

    def boom(client):
        raise exc

    poll_env.setattr(ste, '_SECTIONS', _sections(stats=boom))
    ste._poll_once()
    assert ste._last_poll_ok is False
    for name in SECTION_NAMES:
        assert ste._last_values[name] == GOLDEN_VALUES[name]  # cached snapshot
        assert ste._last_values['collectors_ok'][name] is False
    assert ste._client is None  # dropped so the next poll reconnects


def test_render_keeps_cached_metrics_and_flags_failed_section():
    values = dict(GOLDEN_VALUES)
    values['collectors_ok'] = {'stats': True, 'free_space': False, 'torrents': True}
    out = ste.render_metrics(values, up=True, collected_at=0.0)
    assert 'transmission_download_dir_free_space 123456789' in out  # cached value
    assert 'transmission_downloadSpeed 1000' in out
    assert 'transmission_collector_success{collector="free_space"} 0' in out
    assert 'transmission_collector_success{collector="stats"} 1' in out
    assert 'transmission_collector_success{collector="torrents"} 1' in out


def test_render_omits_metrics_never_collected():
    values = dict(GOLDEN_VALUES)
    values['free_space'] = None  # no success yet since startup
    values['collectors_ok'] = {'stats': True, 'free_space': False, 'torrents': True}
    out = ste.render_metrics(values, up=True, collected_at=0.0)
    assert 'transmission_download_dir_free_space' not in out
    assert 'transmission_downloadSpeed 1000' in out


# --- HTTP smoke --------------------------------------------------------------

@pytest.fixture
def client():
    ste.app.config.update(TESTING=True)
    return ste.app.test_client()


def test_homepage(client):
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.mimetype == 'text/plain'


def test_metrics_endpoint(client):
    resp = client.get('/metrics')
    assert resp.status_code == 200
    assert resp.mimetype == 'text/plain'
    assert 'transmission_up' in resp.get_data(as_text=True)
