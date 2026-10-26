#!/bin/sh
# Run locally: the tablet password is read by ssh from your own terminal.
set -eu
cd "$(dirname "$0")/.."
umask 077
mkdir -p .secrets/ssh
if [ ! -f .secrets/ssh/remarkabl ]; then
  ssh-keygen -q -t ed25519 -N '' -C 'remarkabl personal installer' -f .secrets/ssh/remarkabl
fi
ssh -o StrictHostKeyChecking=accept-new root@"${REMARKABL_DEVICE_HOST:-10.11.99.1}" \
  'umask 077; mkdir -p /home/root/.ssh; touch /home/root/.ssh/authorized_keys; read -r key; if ! grep -qxF "$key" /home/root/.ssh/authorized_keys; then printf "%s\n" "$key" >> /home/root/.ssh/authorized_keys; fi; cat /etc/os-release; uname -m' \
  < .secrets/ssh/remarkabl.pub
printf '\nSSH pairing complete. You can return to Codex.\n'
