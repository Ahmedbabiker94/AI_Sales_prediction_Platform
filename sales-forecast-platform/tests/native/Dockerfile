# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm@sha256:90d117a7f1ef6535dc75831e1a9184725757fede0d4d424d7977fb5429d50610
COPY requirements-b1.lock /tmp/requirements-b1.lock
RUN --mount=type=secret,id=system_ca,required=true PIP_CERT=/run/secrets/system_ca python -m pip install --no-cache-dir -r /tmp/requirements-b1.lock
