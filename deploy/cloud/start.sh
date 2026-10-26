#!/bin/sh
set -eu
umask 077
: "${CLOUD_USER:?CLOUD_USER is required}"
: "${CLOUD_PASSWORD:?CLOUD_PASSWORD is required}"
: "${JWT_SECRET_KEY:?JWT_SECRET_KEY is required}"
mkdir -p "${DATADIR:-/data}"
# Seed the owner before listening. Otherwise upstream accepts the first public login.
rmfakecloud setuser -u "$CLOUD_USER" -p "$CLOUD_PASSWORD" -a -s
exec rmfakecloud
