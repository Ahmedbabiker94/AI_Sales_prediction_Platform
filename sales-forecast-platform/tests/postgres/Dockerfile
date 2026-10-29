# Separate database-test client. Never used by the accepted native launchers.
FROM sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2
COPY requirements.lock /build/requirements.lock
COPY wheels /build/wheels
RUN python -m pip install --no-cache-dir --no-index --no-deps --require-hashes --find-links=/build/wheels -r /build/requirements.lock
