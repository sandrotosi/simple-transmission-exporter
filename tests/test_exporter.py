from pathlib import Path

import pytest

import simple_transmission_exporter as ste

GOLDEN_DIR = Path(__file__).parent

# Same input used to generate tests/golden_metrics.txt.
GOLDEN_VALUES = {
    'download_speed': 1000,
    'upload_speed': 2000,
    'free_space': 123456789,
    'cumulative_stats': {'downloaded_bytes': 10, 'files_added': 20, 'seconds_active': 30, 'session_count': 40, 'uploaded_bytes': 50},
    'current_stats': {'downloaded_bytes': 11, 'files_added': 21, 'seconds_active': 31, 'session_count': 41, 'uploaded_bytes': 51},
    'status_counts': {'stopped': 1, 'downloading': 2, 'seeding': 3},
    'duration': 0.125,
}

ALL_STATUS_SUFFIXES = [suffix for _, suffix in ste._STATUS_METRICS]


def _values(status_counts):
    out = dict(GOLDEN_VALUES)
    out['status_counts'] = status_counts
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

def test_failed_poll_keeps_snapshot_and_flips_up(monkeypatch):
    good = _values(GOLDEN_VALUES['status_counts'])
    monkeypatch.setattr(ste, 'collect', lambda: good)
    ste._poll_once()
    assert ste._last_poll_ok is True
    assert ste._last_values == good

    def boom():
        raise RuntimeError('rpc down')

    monkeypatch.setattr(ste, 'collect', boom)
    ste._poll_once()
    assert ste._last_poll_ok is False
    assert ste._last_values == good  # previous snapshot retained


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
