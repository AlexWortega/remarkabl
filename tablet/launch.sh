#!/bin/sh
set -eu
cd "$(dirname "$0")"
set -a
. /home/root/remarkabl/device.env
set +a
exec ./remarkabl-diary
