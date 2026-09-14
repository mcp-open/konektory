FROM python:3.13.15-alpine3.23@sha256:7ea3f82de8ea6d4fb7e5d2bbe3fe3c9d931700b7a529f1fe5769e42abe514ca1 AS build

WORKDIR /build
COPY sdk ./sdk
COPY vyfakturuj ./connector
RUN python -m venv /opt/openmcp \
    && /opt/openmcp/bin/pip install --no-cache-dir --no-compile ./sdk ./connector \
    && /opt/openmcp/bin/pip check \
    && rm -rf /opt/openmcp/lib/python3.13/site-packages/pip \
              /opt/openmcp/lib/python3.13/site-packages/pip-*.dist-info \
    && rm -f /opt/openmcp/bin/pip /opt/openmcp/bin/pip3 /opt/openmcp/bin/pip3.13

FROM python:3.13.15-alpine3.23@sha256:7ea3f82de8ea6d4fb7e5d2bbe3fe3c9d931700b7a529f1fe5769e42abe514ca1 AS runtime

RUN apk upgrade --no-cache \
    && apk add --no-cache 'libuuid>=2.41.6-r1' \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip \
              /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13

COPY --from=build --chown=10001:10001 /opt/openmcp /opt/openmcp

RUN addgroup -S -g 10001 openmcp \
    && adduser -S -D -H -u 10001 -G openmcp -s /sbin/nologin openmcp

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/openmcp/bin:$PATH" \
    OPENMCP_HTTP_ADDR=:8123
USER 10001:10001
WORKDIR /app
EXPOSE 8123
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8123/health/ready', timeout=2)"
ENTRYPOINT ["python", "-m", "connector_vyfakturuj"]
