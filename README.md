# A simple Prometheus exporter for Transmission

This project aims at providing a [Prometheus](https://prometheus.io/) exporter for the [Transmission](https://transmissionbt.com/) BitTorrent client.

It is inspired by [Transmission Exporter](https://github.com/metalmatze/transmission-exporter) but takes a different, simpler, approach: instead of exporting metrics for every torrent, this exporter only provides higher-level metrics such as:

* torrents active/paused
* download/upload speed
* session/cumulative counters
* etc

### Configuration

Transmission connection parameters are to be specified via environment variables when starting the container:

| Variable | Value |
| --- | --- |
| `TRANSMISSION_HOST` | the hostname where Transmission RPC is running |
| `TRANSMISSION_PORT` | the port where Transmission RPC is listening |
| `TRANSMISSION_USERNAME` | the username to connect to Transmission RPC |
| `TRANSMISSION_PASSWORD` | the password to connect to Transmission RPC |

### Docker

```shell script
docker pull sandrotosi/simple_transmission_exporter

docker run -e TRANSMISSION_HOST=xxx \
           -e TRANSMISSION_PORT=xxx \
           -e TRANSMISSION_USERNAME=xxx \
           -e TRANSMISSION_PASSWORD=xxx \
           -d -p 29091:29091 sandrotosi/simple_transmission_exporter
```

### Implementation details

The exporter is written in Python, and uses [Flask](https://flask.palletsprojects.com/en/1.1.x/) to expose the HTTP scraping endpoint.

### Grafana dashboard

From these metrics, i wrote a Grafana dashboard, available on [grafana.com (ID 13265)](https://grafana.com/grafana/dashboards/13265) and also as a JSON file [from this repo](grafana/Transmission%20(by%20simple%20exporter).json)

You can find a snapshot of the dashboard [here](https://snapshot.raintank.io/dashboard/snapshot/St5kHTCdEhwzRZp1j1i644szrUdbpdGn)

### References

- [Writing Prometheus exporters](https://prometheus.io/docs/instrumenting/writing_exporters/)
- [Transmission RPC](https://github.com/transmission/transmission/blob/master/extras/rpc-spec.txt)
- [transmissionrpc python module](https://pypi.org/project/transmissionrpc/)
- [Containerized Python Development (3 part) series](https://www.docker.com/blog/tag/python-env-series/)