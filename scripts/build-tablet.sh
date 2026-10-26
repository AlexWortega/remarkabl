#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
uv run --group native python -m ziglang version >/dev/null
export PATH="$PWD/scripts:$PATH"
cargo zigbuild --manifest-path tablet/Cargo.toml --locked --release --target armv7-unknown-linux-musleabihf
mkdir -p dist/remarkabl
cp tablet/target/armv7-unknown-linux-musleabihf/release/remarkabl-diary dist/remarkabl/
cp tablet/launch.sh tablet/external.manifest.json dist/remarkabl/
chmod +x dist/remarkabl/launch.sh dist/remarkabl/remarkabl-diary
