# Documentation

Everything about running, configuring and exposing the torproxy panel.

## Sections

| Path | Contents |
|---|---|
| [`usage.md`](usage.md) | Panel workflow, every environment variable, HTTP API, data files, troubleshooting |
| [`security.md`](security.md) | Exposure model, authentication, bridge handling, open-proxy risks |
| [`architecture.md`](architecture.md) | Container layout, process supervision, bridge update pipeline |
| [`screenshots/`](screenshots/) | UI screenshots used by the root README |

## Repository map

| Path | Contents |
|---|---|
| `README.md` | Landing page and quick start |
| `docker-compose.yml` | Standalone stack with loopback port bindings |
| `Dockerfile` | Image based on `avpnusr/torprivoxy` with the panel added |
| `entrypoint.sh` | Seeds `bridges.conf` on first start and assembles `torrc` |
| `reload.sh` | Rebuilds `torrc` from the template and restarts Tor |
| `app/panel.py` | Bridge manager, health checks and HTTP server |
| `app/index.html` | Single-page UI |

## Provenance

The container is built on [`avpnusr/torprivoxy`](https://hub.docker.com/r/avpnusr/torprivoxy),
which bundles Tor, Privoxy, obfs4proxy and runit. Bridge candidates come from the Tor
Project [Moat API](https://gitlab.torproject.org/tpo/anti-censorship/rdsys/-/blob/main/doc/moat.md)
and are validated locally before use. The panel itself is Python standard library only.

This project is not affiliated with the Tor Project. Bridge lines are credentials for
reaching the Tor network and are never shipped in this repository.
