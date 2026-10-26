#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
device_host=${1:-10.11.99.1}
ssh -i .secrets/ssh/remarkabl -o BatchMode=yes -o ConnectTimeout=5 "root@$device_host" '
    systemctl disable remarkabl-xovi
    /home/root/xovi/stock
    echo "Stock interface restored. Notes and cloud sync are retained."
'
