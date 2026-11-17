# Usage guide

Everything between "it starts" and "the proxy survives a bad bridge day".

## The panel

The page refreshes `/api/status` every 2.5 seconds and shows:

- **Tor running / stopped** and the last proxy health result, including the exit IP.
- The number of configured bridges, auto-refresh state and the last update time.
- **Actions:** update bridges, check connectivity, restart Tor.
- **Current bridges** with transport, endpoint and parameters.
- **Apply your own bridges:** one line per bridge, with or without the `Bridge` prefix.
- **Settings:** auto-refresh on/off and the rotation interval in hours.
- **History:** the last actions with timestamps, result and detail.

## Typical workflows

**Refresh bridges manually.** Press **Update bridges**. The panel fetches candidates,
tests them one by one, writes the working set and restarts Tor. Progress and per-bridge
results are shown while the job runs.

**Recover from a dead proxy.** Press **Check connectivity**. If the check fails
repeatedly, the automatic loop starts an update by itself.

**Use your own bridges.** Paste them into the textarea, press **Apply**, and confirm.
The panel validates every line, writes `/data/bridges.conf`, and restarts Tor.

**Change the rotation interval.** Set the number of hours and press **Save**. Set
auto-refresh off to stop both the timer and the failure-triggered updates.

## Configuration

All settings are environment variables. The compose file contains a working example.

| Variable | Default | Meaning |
|---|---|---|
| `PANEL_PORT` | `8099` | Panel HTTP port inside the container. |
| `PANEL_BIND` | `0.0.0.0` | Panel bind address inside the container. |
| `PANEL_USER` | `admin` | Basic-auth user name. |
| `PANEL_PASS` | empty | Basic-auth password. When empty, one is generated into `/data/panel_password`. |
| `PANEL_DISABLE_AUTH` | `0` | Set to `1` to disable authentication. Only safe on a trusted network. |
| `PANEL_DATA` | `/data` | Directory for bridges, state, history and the password. |
| `PANEL_INDEX` | `/opt/panel/index.html` | UI file served at `/`. |
| `PANEL_RELOAD` | `/opt/panel/reload.sh` | Script used to restart Tor. |
| `MOAT_COUNTRY` | empty | Two-letter country code for the Moat settings request. Empty uses the built-in bridge list only. |
| `MOAT_DIRECT` | `1` | Resolve Moat through DNS-over-HTTPS and connect directly; fall back to Privoxy. |
| `TARGET_BRIDGES` | `4` | Number of working bridges to keep. |
| `TEST_TIMEOUT` | `60` | Seconds allowed for one bridge test. |
| `MAX_TEST` | `10` | Maximum candidates tested per update. |
| `HEALTH_INTERVAL` | `300` | Seconds between proxy health checks. |
| `FAIL_THRESHOLD` | `3` | Consecutive health failures that trigger an automatic update. |
| `REFRESH_HOURS` | `168` | Automatic refresh interval. `0` disables the timer. |
| `BRIDGE` | empty | First-run seed only. Ignored once `/data/bridges.conf` exists. |

The refresh interval, health interval and failure threshold can also be changed at
runtime through the panel; runtime values are stored in `/data/state.json` and take
precedence over the initial environment values.

## Data directory

| File | Contents |
|---|---|
| `bridges.conf` | Active bridges. Treat as sensitive. |
| `bridges.conf.bak` | Previous bridge set. |
| `state.json` | Auto-refresh settings, timestamps and action history. |
| `moat_ip` | Cached Moat API address. |
| `panel_password` | Generated password, mode `0600`. |

The container runs as `tordocker` (UID 102, GID 103). On a bind mount, create the host
directory with matching ownership:

```sh
mkdir -p data
sudo chown 102:103 data
```

## HTTP API

All POST calls require the `X-Requested-With: XMLHttpRequest` header and Basic
authentication when it is enabled.

| Method and path | Purpose |
|---|---|
| `GET /` | Panel page. |
| `GET /api/status` | Tor state, health, bridges, job progress, settings, history. |
| `POST /api/update` | Start a bridge update. Body: `{"reason": "manual"}`. |
| `POST /api/health` | Check the proxy and report the exit IP. |
| `POST /api/restart` | Restart Tor. |
| `POST /api/apply` | Validate, write and apply a bridge list. Body: `{"bridges": ["obfs4 ..."]}`. |
| `POST /api/config` | Change `refresh_hours`, `health_interval` or `fail_threshold`. |
| `POST /api/auto` | Enable or disable automatic updates. Body: `{"enabled": true}`. |

## Logs and troubleshooting

```sh
docker logs -f torproxy
```

- **First start.** Look for `generated panel password:` in the log, or read
  `data/panel_password`.
- **`Moat returned no bridges`.** The host could not reach the Moat API and the local
  Tor proxy was not usable either. Check outbound connectivity and Tor state.
- **`no working bridges found`.** The previous set stays active. Retry later or paste
  known-good bridges manually.
- **`Tor restart failed`.** The supervised service did not come back. Inspect the
  container log; no panel state is lost.
- **Permission errors on `/data`.** Fix ownership as described above.
- **Times look wrong.** Set `TZ` on the container; the panel stores Unix timestamps and
  renders them in the browser locale.

## Limitations

- Bridges are tested sequentially, so an update can take a few minutes.
- Only obfs4 bridges are requested and validated.
- The panel is a single process with in-memory job state; restarting the container
  cancels a running update. The applied bridge set is unaffected.
- There is no history database: the last 50 actions are kept in `/data/state.json`.
