#!/bin/sh
set -eu
# A failed boot attempt stays on disk, so the next boot returns to stock UI.
state=/home/root/remarkabl
marker="$state/xovi-boot-attempt"
version=$(sed -n 's/^IMG_VERSION=//p' /etc/os-release | tr -d '"')
if [ "$version" != 3.20.0.92 ]; then
    echo "XOVI skipped: firmware $version requires a compatibility check."
    exit 0
fi
if [ -f "$marker" ] || [ -f "$state/xovi-boot-failed" ]; then
    if [ -f "$marker" ]; then
        mv "$marker" "$state/xovi-boot-failed"
    fi
    echo "Previous XOVI boot failed; keeping the stock interface."
    exit 0
fi
# Reapplying setup while the diary is open must not restart the interface.
pid=$(systemctl show xochitl -p MainPID --value)
if [ -S /tmp/qtfb.sock ] && grep -q '/extensions.d/appload.so' "/proc/$pid/maps"; then
    exit 0
fi
mkdir -p "$state"
touch "$marker"
sync
/home/root/xovi/start
pid=$(systemctl show xochitl -p MainPID --value)
sleep 10
if [ "$(systemctl show xochitl -p MainPID --value)" = "$pid" ] && [ -S /tmp/qtfb.sock ]; then
    rm "$marker"
    sync
else
    /home/root/xovi/stock
    mv "$marker" "$state/xovi-boot-failed"
    echo "AppLoad did not start; restored the stock interface."
fi
