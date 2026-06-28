# --- Builder: install dependencies into an isolated venv ---------------------
FROM python:3.14-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# --- Final: minimal runtime image -------------------------------------------
FROM python:3.14-slim

# Run as an unprivileged user.
RUN useradd --create-home --uid 1000 exporter

# Bring in only the venv and the app; no build tooling or pip cache.
COPY --from=builder /opt/venv /opt/venv
COPY simple_transmission_exporter.py /app/simple_transmission_exporter.py

ENV PATH="/opt/venv/bin:$PATH"
USER exporter
EXPOSE 29091

# Liveness: the landing page is always served regardless of Transmission state
# (the transmission_up metric reports backend health separately).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:29091/', timeout=3).status == 200 else 1)"

# Exec form so the process is PID 1 and receives signals directly.
CMD ["python", "/app/simple_transmission_exporter.py"]
