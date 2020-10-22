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
| `TRANSMISSION_HOST` | the hostname where [Transmission RPC](https://github.com/transmission/transmission/blob/master/extras/rpc-spec.txt) is running |
| `TRANSMISSION_PORT` | the port where Transmission RPC is listening |
| `TRANSMISSION_USERNAME` | the username to connect to Transmission RPC |
| `TRANSMISSION_PASSWORD` | the password to connect to Transmission RPC |

### Docker

```shell script
docker run -e TRANSMISSION_HOST=xxx \
           -e TRANSMISSION_PORT=xxx \
           -e TRANSMISSION_USERNAME=xxx \
           -e TRANSMISSION_PASSWORD=xxx \
           -d -p 29091:29091 simple_transmission_exporter:latest
```

### Implementation details

The exporter is written in Python, and uses [Flask](https://flask.palletsprojects.com/en/1.1.x/) to expose the HTTP scraping endpoint.

### References

- [Writing Prometheus exporters](https://prometheus.io/docs/instrumenting/writing_exporters/)