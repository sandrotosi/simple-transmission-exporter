#!/usr/bin/env python3
import datetime
import os
import sys

from flask import Flask, make_response
import transmissionrpc


METRIC_PREFIX = 'transmission'

# verify all the environment variables are set
for envvar in ["TRANSMISSION_HOST", "TRANSMISSION_PORT", "TRANSMISSION_USERNAME", "TRANSMISSION_PASSWORD"]:
    tmp = os.getenv(envvar)
    if not tmp:
        print(f'ERROR: required environment variable {envvar} is missing, exiting..')
        sys.exit(-1)
    exec(envvar + " = tmp")

app = Flask(__name__)


@app.route('/metrics')
def metrics():

    _return = []
    start = datetime.datetime.now()
    tc = transmissionrpc.Client(address=TRANSMISSION_HOST, port=TRANSMISSION_PORT, user=TRANSMISSION_USERNAME, password=TRANSMISSION_PASSWORD)
    stats = tc.session_stats()

    for metric in ['activeTorrentCount', 'downloadSpeed', 'download_dir_free_space', 'pausedTorrentCount', 'torrentCount', 'uploadSpeed']:
        _metric_name = f'{METRIC_PREFIX}_{metric}'
        _return.append((f'# TYPE {_metric_name}', 'gauge'))
        _return.append((_metric_name, stats._fields[metric].value))

    for metric in ['cumulative_stats', 'current_stats']:
        for item in ['downloadedBytes', 'filesAdded', 'secondsActive', 'sessionCount', 'uploadedBytes']:
            _metric_name = f'{METRIC_PREFIX}_{metric}_{item}'
            _return.append((f'# TYPE {_metric_name}', 'counter'))
            _return.append((_metric_name, stats._fields[metric].value[item]))

    # https://prometheus.io/docs/instrumenting/writing_exporters/#metrics-about-the-scrape-itself
    _metric_name = f'{METRIC_PREFIX}_scrape_duration_seconds'
    _return.append((f'# TYPE {_metric_name}', 'gauge'))
    _return.append((_metric_name,  (datetime.datetime.now() - start).total_seconds()))

    response = make_response('\n'.join([f'{x[0]} {x[1]}' for x in _return]), 200)
    response.mimetype = "text/plain"
    return response
