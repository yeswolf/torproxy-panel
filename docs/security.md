# Security

The panel controls a proxy, so its exposure and its data matter more than its code size.

## Exposure model

The compose example publishes every port on loopback only. Keep it that way unless other
hosts must use the proxy, and expose the panel only to networks you trust.

If the panel must be reachable from a LAN or the internet:

1. Put it behind a TLS reverse proxy.
2. Keep authentication enabled.
3. Restrict access with a firewall or a VPN.
4. Never publish the SOCKS or Privoxy ports to an untrusted network; an open proxy can
   be abused by anyone who finds it.

## Authentication

The panel uses HTTP Basic authentication. Set `PANEL_PASS` explicitly, or let the panel
generate a password on first start and store it in `/data/panel_password` with mode
`0600`.

`PANEL_DISABLE_AUTH=1` removes the only access control the panel has. Use it only when
the port is unreachable from untrusted hosts.

POST endpoints also require the `X-Requested-With: XMLHttpRequest` header. Browsers do
not attach it to cross-site form posts, which blocks the simplest CSRF attempts, but it
is not a substitute for authentication.

## Transport

The panel speaks plain HTTP. There is no TLS, no cookie session and no token expiry. On
a shared network, credentials and bridge lines can be observed by anyone who can read
the traffic. Terminate TLS in front of the panel if that matters.

## Bridge data

A bridge line contains an endpoint, a fingerprint and a `cert=` value. Together they are
credentials for reaching the Tor network through a specific bridge. Treat
`/data/bridges.conf`, `bridges.conf.bak` and the history in `state.json` as sensitive:

- Do not commit `/data`.
- Do not paste bridge lines into issues, screenshots or logs you publish.
- Do not share a container volume that contains them.

## Container scope

The container does not mount the Docker socket and does not need host networking. The
panel can restart Tor inside its own container and write files under `/data`; that is
the full extent of its authority.

## What the panel does not protect

- It cannot make a compromised host safe.
- It cannot hide the fact that Tor is running from a local observer.
- It does not encrypt the proxy traffic between clients and the container.
