FROM python:3.12-slim
WORKDIR /app

# no build tooling needed — pure-python + fastapi + uvicorn
COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir "/app[dashboard,exporter]" && \
    apt-get purge -y --auto-remove && rm -rf /var/lib/apt/lists/*

# state lives on a mounted volume — never inside the image
ENV INCIDENT_BUFFER_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8765

# healthcheck hits the app's own /api/health
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request as u,sys; \
                 sys.exit(0 if u.urlopen('http://127.0.0.1:8765/api/health',timeout=2).status==200 else 1)"

# ingest / viewer tokens supplied at runtime via -e
# ponytail: no wrapper script — uvicorn is the process, honours SIGTERM
CMD ["uvicorn", "incident_buffer.dashboard.app:app", \
     "--host", "0.0.0.0", "--port", "8765"]
