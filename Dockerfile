# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY requirements.txt .
RUN python -m venv /venv \
 && /venv/bin/pip install --require-hashes --only-binary=:all: -r requirements.txt

FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.source="https://github.com/RouNNdeL/gtrmnl" \
      org.opencontainers.image.description="Garmin Connect to TRMNL webhook collector"
# 568 is the TrueNAS SCALE "apps" user; override with `user:` in compose.
ARG UID=568
ARG GID=568
RUN groupadd --gid "${GID}" gtrmnl \
 && useradd --uid "${UID}" --gid "${GID}" --no-create-home --home-dir /tmp --shell /usr/sbin/nologin gtrmnl \
 && mkdir -p /data && chown "${UID}:${GID}" /data && chmod 0700 /data
COPY --from=build /venv /venv
# The package is pure Python: copy it instead of building a wheel, so the
# only third-party code in the image is what requirements.txt pins by hash.
COPY src/gtrmnl /app/gtrmnl
RUN printf '#!/bin/sh\nexec python -m gtrmnl "$@"\n' > /usr/local/bin/gtrmnl \
 && chmod 0755 /usr/local/bin/gtrmnl \
 && python -m compileall -q /app
ENV PATH=/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HOME=/tmp \
    DATA_DIR=/data \
    TZ=Europe/Warsaw
VOLUME ["/data"]
USER ${UID}:${GID}
HEALTHCHECK --interval=5m --timeout=20s --start-period=2m --retries=2 CMD ["gtrmnl", "health"]
ENTRYPOINT ["gtrmnl"]
CMD ["run"]
