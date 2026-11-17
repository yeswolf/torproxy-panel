# Architecture

The container runs three supervised processes and one small state directory.

```text
                    ┌──────────────────────────────┐
  browser ──HTTP──▶ │ panel.py :8099               │
                    │  ├─ Moat client              │
                    │  ├─ bridge tester            │
                    │  └─ /data state              │
                    └──────────┬───────────────────┘
                               │ writes bridges.conf, restarts tor
                               ▼
                    ┌──────────────────────────────┐
  clients ─────────▶│ Tor :9050  +  Privoxy :8118  │
                    └──────────────────────────────┘
```

## Processes

`avpnusr/torprivoxy` uses runit. The panel is installed as a second service in
`/etc/service/panel`; Tor and Privoxy keep their own service directories. The panel
never edits Tor settings beyond the bridge block appended to `torrc` by
`entrypoint.sh` and `reload.sh`.

## Bridge update pipeline

1. Resolve `bridges.torproject.org` through DNS-over-HTTPS, then request the Moat
   settings endpoint (optionally country-targeted) and the built-in bridge list.
2. Deduplicate candidates, including bridges that are already configured.
3. Test up to `MAX_TEST` candidates sequentially by starting a throwaway Tor process
   with that bridge and asking it for an exit circuit.
4. Keep the fastest `TARGET_BRIDGES` working lines, write them to `bridges.conf`, and
   restart the supervised Tor process.
5. Poll the proxy for up to 90 seconds and record the exit IP in the history.

If no candidate works, the previous bridge set stays in place. Automatic refreshes run
on the `REFRESH_HOURS` timer and after `FAIL_THRESHOLD` consecutive health failures.

## State

All runtime state lives in `/data`, which is a bind mount in the compose example and is
excluded from git. The panel holds no database and no external dependencies; deleting
`/data` returns the container to a first-run state.
