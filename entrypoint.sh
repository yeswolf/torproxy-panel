#!/bin/sh
set -e

DATA_DIR="${PANEL_DATA:-/data}"
BRIDGES_CONF="$DATA_DIR/bridges.conf"

mkdir -p "$DATA_DIR" 2>/dev/null || true

# First run: seed bridges.conf from the old BRIDGE env variable. After that the
# panel owns the file and the env value is ignored.
if [ -n "$BRIDGE" ] && [ ! -s "$BRIDGES_CONF" ]; then
  printf '%s\n' "$BRIDGE" | while IFS= read -r b; do
    [ -n "$b" ] && printf 'Bridge %s\n' "$b"
  done > "$BRIDGES_CONF" 2>/dev/null || true
fi

cp -f /etc/service/tor/torrc.base /etc/service/tor/torrc

if [ -s "$BRIDGES_CONF" ]; then
  echo "UseBridges 1" >> /etc/service/tor/torrc
  echo "ClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy" >> /etc/service/tor/torrc
  printf '%%include %s\n' "$BRIDGES_CONF" >> /etc/service/tor/torrc
elif [ -n "$BRIDGE" ]; then
  # Volume is missing or read-only: behave like the stock image.
  echo "UseBridges 1" >> /etc/service/tor/torrc
  echo "ClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy" >> /etc/service/tor/torrc
  printf '%s\n' "$BRIDGE" | while IFS= read -r b; do
    [ -n "$b" ] && printf 'Bridge %s\n' "$b"
  done >> /etc/service/tor/torrc
fi

exec "$@"
