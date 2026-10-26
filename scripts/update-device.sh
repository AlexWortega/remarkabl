#!/bin/sh
# Update the diary after pairing and building, without replacing user notes.
set -eu
cd "$(dirname "$0")/.."
device_host=${1:-10.11.99.1}
ssh_key=.secrets/ssh/remarkabl
ssh -i "$ssh_key" -o BatchMode=yes -o ConnectTimeout=5 "root@$device_host" '
    test "$(cat /sys/devices/soc0/machine)" = "reMarkable 2.0"
    test "$(sed -n "s/^IMG_VERSION=//p" /etc/os-release | tr -d "\"")" = 3.20.0.92
    mkdir -p /home/root/remarkabl /home/root/xovi/exthome/appload/remarkabl
'
scp -i "$ssh_key" -o ConnectTimeout=5 -o BatchMode=yes dist/remarkabl/remarkabl-diary "root@$device_host:/home/root/remarkabl/remarkabl-diary.new"
scp -i "$ssh_key" -o ConnectTimeout=5 -o BatchMode=yes tablet/launch.sh tablet/external.manifest.json tablet/icon.png "root@$device_host:/home/root/xovi/exthome/appload/remarkabl/"
scp -i "$ssh_key" -o ConnectTimeout=5 -o BatchMode=yes tablet/start-xovi-safe.sh tablet/remarkabl-sync.service tablet/remarkabl-xovi.service "root@$device_host:/home/root/remarkabl/"
scp -i "$ssh_key" -o ConnectTimeout=5 -o BatchMode=yes tablet/pdf-panel.qml tablet/fonts/Caveat.ttf "root@$device_host:/home/root/remarkabl/"
scp -i "$ssh_key" -o ConnectTimeout=5 -o BatchMode=yes tablet/fast-ink.qmd tablet/pdf-panel.qmd "root@$device_host:/home/root/xovi/exthome/qt-resource-rebuilder/"
ssh -i "$ssh_key" -o BatchMode=yes -o ConnectTimeout=5 "root@$device_host" '
    systemctl stop remarkabl-sync
    chmod 755 /home/root/remarkabl/remarkabl-diary.new
    mv /home/root/remarkabl/remarkabl-diary.new /home/root/xovi/exthome/appload/remarkabl/remarkabl-diary
    chmod 755 /home/root/remarkabl/start-xovi-safe.sh /home/root/xovi/exthome/appload/remarkabl/launch.sh
    cp /home/root/remarkabl/remarkabl-sync.service /home/root/remarkabl/remarkabl-xovi.service /etc/systemd/system/
    systemctl daemon-reload
    cp /home/root/xovi/inactive-extensions/qt-command-executor.so /home/root/xovi/extensions.d/qt-command-executor.so
    rm -f /home/root/remarkabl/xovi-boot-failed
    systemctl enable remarkabl-sync remarkabl-xovi
    systemctl start remarkabl-sync
'
