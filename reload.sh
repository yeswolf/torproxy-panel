#!/bin/sh
# Regenerate torrc from the image template plus /data/bridges.conf and restart
# the supervised tor process.

DATA_DIR="${PANEL_DATA:-/data}"
BRIDGES_CONF="$DATA_DIR/bridges.conf"

cp -f /etc/service/tor/torrc.base /etc/service/tor/torrc
if [ -s "$BRIDGES_CONF" ]; then
  echo "UseBridges 1" >> /etc/service/tor/torrc
  echo "ClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy" >> /etc/service/tor/torrc
  printf '%%include %s\n' "$BRIDGES_CONF" >> /etc/service/tor/torrc
fi

for sv in /usr/sbin/sv /sbin/sv /usr/bin/sv; do
  if [ -x "$sv" ]; then
    "$sv" restart tor && exit 0
  fi
done
if command -v sv >/dev/null 2>&1; then
  sv restart tor && exit 0
fi

# Fallback for images without runit's sv in PATH.
pid=$(pidof tor 2>/dev/null | awk '{print $1}')
if [ -n "$pid" ]; then
  kill -HUP "$pid"
  exit 0
fi

exit 1
