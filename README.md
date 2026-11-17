# torproxy-panel

A small self-hosted web panel that keeps an
[obfs4](https://community.torproject.org/relay/setup/bridge/) bridge
configuration for a `torprivoxy` container up to date.

It fetches candidate bridges from the Tor Project Moat API, tests each
candidate with a throwaway Tor process, writes the working ones to
`/data/bridges.conf`, and restarts the supervised Tor instance. Everything in
the panel is Python standard library; the image is built on top of
[`avpnusr/torprivoxy`](https://hub.docker.com/r/avpnusr/torprivoxy).

## Features

- Country-targeted and built-in obfs4 bridge lists from the Tor Project Moat API.
- Per-bridge connectivity test before anything is applied; only working bridges
  are written.
- Manual bridge entry, Tor restart, and an end-to-end proxy health check.
- Automatic refresh on a timer and after repeated health-check failures.
- Persistent state and a short action history in `/data`.
- Optional HTTP Basic authentication, with a generated password when none is set.
- Single static page; no build step, no external assets at runtime.

## How it works

```text
Moat API ──▶ candidates ──▶ throwaway tor test ──▶ /data/bridges.conf
                                                        │
                                                        ▼
                                                  reload tor
                                                        │
                                          privoxy :8118 ─┴─ socks :9050
```

The base image runs Tor and Privoxy under runit. The panel runs as a second
supervised service and never edits Tor settings beyond the bridge block that
`entrypoint.sh` and `reload.sh` assemble from the image template.

## Quick start

```sh
git clone https://github.com/<owner>/torproxy-panel.git
cd torproxy-panel

# The container runs as tordocker (UID 102, GID 103); the bind mount must be
# writable by that user.
mkdir -p data
sudo chown 102:103 data

docker compose up -d --build
```

Open <http://127.0.0.1:8099/>. The default user is `admin`. If `PANEL_PASS` is
not set, the panel generates a password on first start, writes it to
`data/panel_password`, and prints it to the container log:

```sh
docker logs torproxy 2>&1 | grep 'generated panel password'
```

A working SOCKS proxy is then available on `127.0.0.1:9050` and an HTTP proxy on
`127.0.0.1:8118`.

## Configuration

All settings are environment variables. The compose file contains a working
example; the table lists every option the panel reads.

| Variable | Default | Meaning |
|---|---|---|
| `PANEL_PORT` | `8099` | Panel HTTP port inside the container. |
| `PANEL_BIND` | `0.0.0.0` | Panel bind address inside the container. |
| `PANEL_USER` | `admin` | Basic-auth user name. |
| `PANEL_PASS` | empty | Basic-auth password. When empty, one is generated into `/data/panel_password`. |
| `PANEL_DISABLE_AUTH` | `0` | Set to `1` to disable authentication. Only safe on a trusted network. |
| `PANEL_DATA` | `/data` | Directory for bridges, state, history, and the password. |
| `MOAT_COUNTRY` | empty | Two-letter country code for the Moat settings request. When empty, only the built-in bridge list is used. |
| `MOAT_DIRECT` | `1` | Resolve Moat through DNS-over-HTTPS and connect directly; falls back to the local Privoxy. |
| `TARGET_BRIDGES` | `4` | Number of working bridges to keep. |
| `TEST_TIMEOUT` | `60` | Seconds allowed for one bridge test. |
| `MAX_TEST` | `10` | Maximum candidates tested per update. |
| `REFRESH_HOURS` | `168` | Automatic refresh interval. `0` disables the timer. |
| `HEALTH_INTERVAL` | `300` | Seconds between proxy health checks. |
| `FAIL_THRESHOLD` | `3` | Consecutive health failures that trigger an automatic bridge update. |
| `BRIDGE` | empty | First-run seed only. Ignored once `/data/bridges.conf` exists. |

## Data directory

`/data` holds runtime state and is deliberately excluded from git:

| File | Contents |
|---|---|
| `bridges.conf` | Active bridges. Treat as sensitive. |
| `bridges.conf.bak` | Previous bridge set. |
| `state.json` | Auto-refresh settings and action history. |
| `moat_ip` | Cached Moat API address. |
| `panel_password` | Generated password, mode `0600`. |

Bridge lines contain endpoint addresses, fingerprints, and `cert=` values. They
are credentials for reaching the Tor network and should not be published.

## HTTP API

The panel uses the same endpoints as its own UI. All POST calls require the
`X-Requested-With: XMLHttpRequest` header and Basic authentication when enabled.

| Method and path | Purpose |
|---|---|
| `GET /` | Panel page. |
| `GET /api/status` | Tor state, bridges, job progress, settings, history. |
| `POST /api/update` | Start a bridge update. |
| `POST /api/health` | Check the proxy and report the exit IP. |
| `POST /api/restart` | Restart Tor. |
| `POST /api/apply` | Validate, write, and apply a user-supplied bridge list. |
| `POST /api/config` | Change refresh, health, and failure settings. |
| `POST /api/auto` | Enable or disable automatic updates. |

## Security notes

- The panel speaks plain HTTP and has no CSRF token beyond the required
  `X-Requested-With` header. Keep it on a trusted LAN, bind it to loopback, or
  put it behind a TLS reverse proxy.
- Bind the proxy ports to loopback unless other hosts are meant to use them.
  An open Privoxy or SOCKS port is an open proxy.
- Do not commit `/data`, bridge lines, or the generated password.
- `PANEL_DISABLE_AUTH=1` removes the only access control the panel has.

## Requirements

- Docker with Compose v2.
- Outbound access to the Tor Project and the Tor network.
- Linux on the host; the base image includes Tor, Privoxy, and obfs4proxy.

## Layout

```text
Dockerfile        image based on avpnusr/torprivoxy
docker-compose.yml
entrypoint.sh     seeds bridges.conf and assembles torrc on first start
reload.sh         rebuilds torrc and restarts the supervised Tor process
app/panel.py      bridge manager and HTTP server
app/index.html    single-page UI
```

## Limitations

- The UI text is Russian; the API and logs are English.
- Bridges are tested sequentially, so an update can take a few minutes.
- Only obfs4 bridges are requested and validated.
