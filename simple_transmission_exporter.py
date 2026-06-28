#!/usr/bin/env python3
import os
import sys
import threading
import time

from flask import Flask, make_response, request
from transmission_rpc import Client

__version__ = '1.0.0'

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

# Cached snapshot of the last successful poll, served to every scrape so a slow
# or failing RPC never blocks (or distorts) Prometheus. Protected by the lock.
_state_lock = threading.Lock()
_last_values = None      # dict from the last successful collect(), or None
_last_success_ts = 0.0   # unix time of the last successful poll
_last_poll_ok = False    # did the most recent poll attempt succeed?

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


def collect():
    """Query Transmission once and return a plain dict of metric values."""
    client = _get_client()
    started = time.monotonic()
    stats = client.session_stats()
    session = client.get_session()
    free_space = client.free_space(session.download_dir)
    return {
        'download_speed': stats.download_speed,
        'upload_speed': stats.upload_speed,
        'free_space': free_space if free_space is not None else 0,
        'cumulative_stats': _stats_values(stats.cumulative_stats),
        'current_stats': _stats_values(stats.current_stats),
        'duration': time.monotonic() - started,
    }


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


def render_metrics(values, up, collected_at):
    """Render Prometheus exposition text from cached values (pure, no I/O)."""
    lines = []

    def emit(name, mtype, value, labels=''):
        full = f'{METRIC_PREFIX}_{name}'
        lines.append(f'# TYPE {full} {mtype}')
        lines.append(f'{full}{labels} {value}')

    if values is not None:
        emit('downloadSpeed', 'gauge', values['download_speed'])
        emit('download_dir_free_space', 'gauge', values['free_space'])
        emit('uploadSpeed', 'gauge', values['upload_speed'])
        for group in ('cumulative_stats', 'current_stats'):
            stats = values[group]
            for attr, suffix in _STATS_ITEMS:
                emit(f'{group}_{suffix}', 'counter', stats[attr])
        # status metrics are added in a later phase
        emit('scrape_duration_seconds', 'gauge', values['duration'])

    # https://prometheus.io/docs/instrumenting/writing_exporters/#metrics-about-the-scrape-itself
    emit('up', 'gauge', 1 if up else 0)
    emit('last_collection_timestamp_seconds', 'gauge', collected_at)
    emit('exporter_build_info', 'gauge', 1, labels=f'{{version="{__version__}"}}')

    return '\n'.join(lines)


def _poll_once():
    """Run one poll, updating the cached snapshot. Never raises."""
    global _last_values, _last_success_ts, _last_poll_ok, _client
    try:
        values = collect()
    except Exception as exc:
        print(f'WARNING: poll failed: {exc}', file=sys.stderr)
        with _state_lock:
            _last_poll_ok = False
        _client = None  # drop the client so the next poll reconnects
        return
    with _state_lock:
        _last_values = values
        _last_success_ts = time.time()
        _last_poll_ok = True


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
