# The proxy's coordination store (the litellm-valkey Application): Valkey with the estate's
# settings built in, run as the image's own valkey user by number so the images lock can record
# it. Digest of 8.1.6-alpine read from docker.io on 2026-11-17, the release platform-valkey runs;
# Renovate bumps tag and digest.
FROM docker.io/valkey/valkey:8.1.6-alpine@sha256:1cb6b20b70d927560cc4cc5397b5f045e74aa603ff7696274778880bb6fadc75

COPY valkey.conf /etc/valkey/valkey.conf
COPY valkey-start /usr/local/bin/valkey-start

USER 999:1000
ENTRYPOINT ["/usr/local/bin/valkey-start"]
