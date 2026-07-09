#!/usr/bin/env python3
import os
import sys
import threading
import time
from collections import Counter

from flask import Flask, make_response, request
from transmission_rpc import Client
from transmission_rpc.error import TransmissionConnectError, TransmissionError

__version__ = '2.1.1'

METRIC_PREFIX = 'transmission'
PORT = 29091


def _require_env(name):
    value = os.getenv(name)
    if not value:
        print(f'ERROR: required environment variable {name} is missing, exiting..', file=sys.stderr)
        sys.exit(1)
    return value


def _parse_int(name, value):
    try:
        return int(value)
    except ValueError:
        print(f'ERROR: environment variable {name} must be an integer, got {value!r}, exiting..', file=sys.stderr)
        sys.exit(1)


# Transmission connection (required)
TRANSMISSION_HOST = _require_env('TRANSMISSION_HOST')
TRANSMISSION_PORT = _parse_int('TRANSMISSION_PORT', _require_env('TRANSMISSION_PORT'))
TRANSMISSION_USERNAME = _require_env('TRANSMISSION_USERNAME')
TRANSMISSION_PASSWORD = _require_env('TRANSMISSION_PASSWORD')

# Optional tunables
TRANSMISSION_PROTOCOL = os.getenv('TRANSMISSION_PROTOCOL', 'http')
POLL_DELAY_SECONDS = _parse_int('POLL_DELAY_SECONDS', os.getenv('POLL_DELAY_SECONDS', '10'))
RPC_TIMEOUT_SECONDS = _parse_int('RPC_TIMEOUT_SECONDS', os.getenv('RPC_TIMEOUT_SECONDS', '15'))

app = Flask(__name__)

# Cached snapshot served to every scrape so a slow or failing RPC never blocks
# (or gaps) Prometheus: each section keeps its last-known values across failed
# polls, so transient daemon timeouts don't make graphs choppy. Staleness is
# visible instead through transmission_collector_success (per section) and
# transmission_last_collection_timestamp_seconds. Protected by the lock.
_state_lock = threading.Lock()
_last_values = None      # per-section last-known values (+ 'collectors_ok'), or None
_last_success_ts = 0.0   # unix time of the last poll with any successful section
_last_poll_ok = False    # did any section succeed on the most recent poll?

_client = None           # long-lived RPC client, (re)connected lazily


def _get_client():
    global _client
    if _client is None:
        _client = Client(
            protocol=TRANSMISSION_PROTOCOL,
            host=TRANSMISSION_HOST,
            port=TRANSMISSION_PORT,
            username=TRANSMISSION_USERNAME,
            password=TRANSMISSION_PASSWORD,
            timeout=RPC_TIMEOUT_SECONDS,
        )
    return _client


def _stats_values(stats):
    return {
        'downloaded_bytes': stats.downloaded_bytes,
        'files_added': stats.files_added,
        'seconds_active': stats.seconds_active,
        'session_count': stats.session_count,
        'uploaded_bytes': stats.uploaded_bytes,
    }


def _collect_stats(client):
    stats = client.session_stats()
    return {
        'download_speed': stats.download_speed,
        'upload_speed': stats.upload_speed,
        'cumulative_stats': _stats_values(stats.cumulative_stats),
        'current_stats': _stats_values(stats.current_stats),
    }


def _collect_free_space(client):
    # The session download-dir can change (or disappear) at runtime, in which
    # case the daemon answers the free-space query with an error.
    session = client.get_session()
    return client.free_space(session.download_dir)


def _collect_torrents(client):
    # Only the status field is needed; keeping the payload minimal matters when
    # there are many torrents.
    torrents = client.get_torrents(arguments=['status'])
    return dict(Counter(str(t.status) for t in torrents))


# Independent collection sections: one failing (e.g. free-space on a
# download-dir that no longer exists) must not lose the others.
_SECTIONS = (
    ('stats', _collect_stats),
    ('free_space', _collect_free_space),
    ('torrents', _collect_torrents),
)


# Map the snake_case Stats attributes to the original camelCase metric-name
# suffixes so emitted metric names stay byte-for-byte identical (the published
# Grafana dashboard depends on them).
_STATS_ITEMS = (
    ('downloaded_bytes', 'downloadedBytes'),
    ('files_added', 'filesAdded'),
    ('seconds_active', 'secondsActive'),
    ('session_count', 'sessionCount'),
    ('uploaded_bytes', 'uploadedBytes'),
)

# Map transmission-rpc's status strings to the original metric-name suffixes
# (order follows the old integer status codes). Rendering iterates this whole
# table so every status is emitted, defaulting to 0 when absent.
_STATUS_METRICS = (
    ('stopped', 'paused'),
    ('check pending', 'queued_to_check'),
    ('checking', 'checking'),
    ('download pending', 'queued_to_download'),
    ('downloading', 'downloading'),
    ('seed pending', 'queued_to_seed'),
    ('seeding', 'seeding'),
)


def render_metrics(values, up, collected_at):
    """Render Prometheus exposition text from cached values (pure, no I/O)."""
    lines = []

    def emit(name, mtype, value, labels=''):
        full = f'{METRIC_PREFIX}_{name}'
        lines.append(f'# TYPE {full} {mtype}')
        lines.append(f'{full}{labels} {value}')

    if values is not None:
        # Section values are last-known (cached across failed polls); they are
        # only None — and their metrics omitted — before the first success.
        stats = values['stats']
        if stats is not None:
            emit('downloadSpeed', 'gauge', stats['download_speed'])
        if values['free_space'] is not None:
            emit('download_dir_free_space', 'gauge', values['free_space'])
        if stats is not None:
            emit('uploadSpeed', 'gauge', stats['upload_speed'])
            for group in ('cumulative_stats', 'current_stats'):
                for attr, suffix in _STATS_ITEMS:
                    emit(f'{group}_{suffix}', 'counter', stats[group][attr])
        status_counts = values['torrents']
        if status_counts is not None:
            for status_str, suffix in _STATUS_METRICS:
                emit(f'status_{suffix}', 'gauge', status_counts.get(status_str, 0))
        emit('scrape_duration_seconds', 'gauge', values['duration'])
        # One family with a sample per section, so only one # TYPE line.
        collectors_ok = values['collectors_ok']
        lines.append(f'# TYPE {METRIC_PREFIX}_collector_success gauge')
        for name, _ in _SECTIONS:
            ok = 1 if collectors_ok.get(name) else 0
            lines.append(f'{METRIC_PREFIX}_collector_success{{collector="{name}"}} {ok}')

    # https://prometheus.io/docs/instrumenting/writing_exporters/#metrics-about-the-scrape-itself
    emit('up', 'gauge', 1 if up else 0)
    emit('last_collection_timestamp_seconds', 'gauge', collected_at)
    emit('exporter_build_info', 'gauge', 1, labels=f'{{version="{__version__}"}}')

    return '\n'.join(lines)


def _poll_once():
    """Run one poll, updating the cached snapshot. Never raises.

    A plain TransmissionError means the daemon answered but the query failed
    (RPC-level), so only that section fails and the connection is kept.
    Anything else — including TransmissionConnectError, which subclasses
    TransmissionError — means nothing further can succeed this poll: abandon
    it and drop the client so the next poll reconnects. Either way a failed
    section keeps its last-known values; only its collectors_ok flag drops.
    """
    global _last_values, _last_success_ts, _last_poll_ok, _client
    fresh = {}
    ok = {}
    started = time.monotonic()
    try:
        client = _get_client()
        for name, collector in _SECTIONS:
            try:
                fresh[name] = collector(client)
                ok[name] = True
            except TransmissionConnectError:
                raise
            except TransmissionError as exc:
                print(f'WARNING: {name} collection failed: {exc}', file=sys.stderr)
                ok[name] = False
    except Exception as exc:
        print(f'WARNING: poll failed: {exc}', file=sys.stderr)
        _client = None
        # sections never attempted this poll count as failed
        for name, _ in _SECTIONS:
            ok.setdefault(name, False)
    duration = time.monotonic() - started
    any_ok = any(ok.values())
    with _state_lock:
        prev = _last_values or {}
        values = {name: fresh.get(name, prev.get(name)) for name, _ in _SECTIONS}
        values['collectors_ok'] = ok
        values['duration'] = duration
        _last_values = values
        _last_poll_ok = any_ok
        if any_ok:
            _last_success_ts = time.time()


def _poll_loop():
    """Fixed-delay loop: poll, wait, repeat (the delay starts after each poll
    finishes, so a slow poll can never overlap or pile up)."""
    while True:
        _poll_once()
        time.sleep(POLL_DELAY_SECONDS)


def _start_collector():
    threading.Thread(target=_poll_loop, name='collector', daemon=True).start()


@app.route('/')
def homepage():
    """
    https://prometheus.io/docs/instrumenting/writing_exporters/#landing-page
    """
    landing_page = f"""A simple Prometheus exporter for Transmission
https://github.com/sandrotosi/simple-transmission-exporter

metric page: {request.host_url}metrics
"""
    response = make_response(landing_page, 200)
    response.mimetype = "text/plain"
    return response


@app.route('/metrics')
def metrics():
    with _state_lock:
        values, collected_at, up = _last_values, _last_success_ts, _last_poll_ok
    response = make_response(render_metrics(values, up=up, collected_at=collected_at), 200)
    response.mimetype = "text/plain"
    return response


if __name__ == '__main__':
    _start_collector()
    app.run(debug=False, port=PORT, host='0.0.0.0')
