#!/usr/bin/env python3
"""Small bridge manager for the torproxy container.

It fetches obfs4 bridges from the Tor Project Moat API, tests each candidate by
starting a throwaway tor instance, writes the winners to /data/bridges.conf and
restarts the supervised tor process. Everything is stdlib only.
"""

import base64
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA_DIR = os.environ.get("PANEL_DATA", "/data")
BRIDGES_FILE = os.path.join(DATA_DIR, "bridges.conf")
BRIDGES_BACKUP = os.path.join(DATA_DIR, "bridges.conf.bak")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
IP_CACHE_FILE = os.path.join(DATA_DIR, "moat_ip")
PASSWORD_FILE = os.path.join(DATA_DIR, "panel_password")
INDEX_FILE = os.environ.get("PANEL_INDEX", "/opt/panel/index.html")
RELOAD_SCRIPT = os.environ.get("PANEL_RELOAD", "/opt/panel/reload.sh")

TORRC = "/etc/service/tor/torrc"
TORRC_BASE = "/etc/service/tor/torrc.base"
TORRC_BRIDGE = "/etc/service/tor/torrc.bridge"
TOR_BIN = shutil.which("tor") or "/usr/bin/tor"
OBFS4_BIN = shutil.which("obfs4proxy") or "/usr/bin/obfs4proxy"

MOAT_HOST = "bridges.torproject.org"
DOH_URLS = ("https://8.8.8.8/resolve", "https://1.1.1.1/dns-query")
MOAT_COUNTRY = os.environ.get("MOAT_COUNTRY", "")
MOAT_DIRECT = os.environ.get("MOAT_DIRECT", "1") != "0"
PROXY_URL = "http://127.0.0.1:8118"

TARGET_BRIDGES = int(os.environ.get("TARGET_BRIDGES", "4"))
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "60"))
MAX_TEST = int(os.environ.get("MAX_TEST", "10"))
HEALTH_TIMEOUT = 15

PANEL_PORT = int(os.environ.get("PANEL_PORT", "8099"))
PANEL_BIND = os.environ.get("PANEL_BIND", "0.0.0.0")
PANEL_USER = os.environ.get("PANEL_USER", "admin")
PANEL_PASS = os.environ.get("PANEL_PASS", "")
PANEL_DISABLE_AUTH = os.environ.get("PANEL_DISABLE_AUTH", "0") == "1"
VERSION = "1.0"

TRANSPORT_RE = re.compile(r"^(obfs4|webtunnel|snowflake|meek_lite|vanilla)\s+(\S+:\d+)\s+\S+")
LOG_LOCK = threading.Lock()

state_lock = threading.Lock()
state = {
    "auto": True,
    "refresh_hours": int(os.environ.get("REFRESH_HOURS", "168")),
    "health_interval": int(os.environ.get("HEALTH_INTERVAL", "300")),
    "fail_threshold": int(os.environ.get("FAIL_THRESHOLD", "3")),
    "last_update": 0,
    "last_refresh": 0,
    "history": [],
}

job_lock = threading.Lock()
job = {
    "running": False,
    "phase": "idle",
    "message": "",
    "reason": "",
    "started": 0,
    "finished": 0,
    "candidates": [],
}

last_health = {"ts": 0, "ok": None, "ip": "", "tor": None, "error": ""}
_last_health_lock = threading.Lock()
_runtime = {"last_health_ts": 0, "consecutive_fails": 0, "started": 0}


def log(message):
    with LOG_LOCK:
        print("[panel] %s" % message, flush=True)


def now():
    return int(time.time())


def run(cmd, timeout=30, input_text=None):
    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", "timeout"
    except OSError as exc:
        return 127, "", str(exc)


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    with state_lock:
        for key in ("auto", "refresh_hours", "health_interval", "fail_threshold",
                    "last_update", "last_refresh", "history"):
            if key in data and isinstance(data[key], (bool, int, float, list)):
                state[key] = data[key]


def save_state():
    with state_lock:
        payload = json.dumps(state, ensure_ascii=False)
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, STATE_FILE)
    except OSError as exc:
        log("state save failed: %s" % exc)


def add_history(action, ok, detail):
    entry = {"ts": now(), "action": action, "ok": bool(ok), "detail": detail[:500]}
    with state_lock:
        state["history"].append(entry)
        state["history"] = state["history"][-50:]
    save_state()


def valid_bridge(line):
    line = line.strip()
    if not line:
        return False
    if line.startswith("Bridge "):
        line = line[7:].strip()
    match = TRANSPORT_RE.match(line)
    return bool(match)


def clean_line(line):
    line = line.strip()
    if line.startswith("Bridge "):
        line = line[7:].strip()
    return line


def current_bridges():
    lines = []
    try:
        with open(BRIDGES_FILE, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = clean_line(raw)
                if valid_bridge(line):
                    lines.append(line)
    except OSError:
        pass
    if lines:
        return lines
    try:
        with open(TORRC, "r", encoding="utf-8") as fh:
            for raw in fh:
                if raw.startswith("Bridge "):
                    line = clean_line(raw)
                    if valid_bridge(line):
                        lines.append(line)
    except OSError:
        pass
    return lines


def ensure_bridges_file():
    if os.path.exists(BRIDGES_FILE):
        return
    found = current_bridges()
    if found:
        try:
            write_bridges(found, backup=False)
        except (ValueError, OSError) as exc:
            log("cannot adopt current bridges: %s" % exc)


def write_bridges(lines, backup=True):
    cleaned = []
    for line in lines:
        line = clean_line(line)
        if valid_bridge(line) and line not in cleaned:
            cleaned.append(line)
    if not cleaned:
        raise ValueError("no valid bridge lines")
    ensure_data_dir()
    if backup and os.path.exists(BRIDGES_FILE):
        try:
            shutil.copyfile(BRIDGES_FILE, BRIDGES_BACKUP)
        except OSError:
            pass
    tmp = BRIDGES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for line in cleaned:
            fh.write("Bridge %s\n" % line)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, BRIDGES_FILE)
    try:
        os.chmod(BRIDGES_FILE, 0o644)
    except OSError:
        pass
    return cleaned


def read_cached_ip():
    try:
        with open(IP_CACHE_FILE, "r", encoding="utf-8") as fh:
            value = fh.read().strip()
            return value if re.match(r"^\d+\.\d+\.\d+\.\d+$", value) else ""
    except OSError:
        return ""


def cache_ip(ip):
    try:
        with open(IP_CACHE_FILE, "w", encoding="utf-8") as fh:
            fh.write(ip)
    except OSError:
        pass


def doh_resolve():
    query = urllib.parse.urlencode({"name": MOAT_HOST, "type": "A"})
    for base in DOH_URLS:
        code, out, _ = run(
            ["curl", "-sS", "-m", "8", "-H", "accept: application/dns-json",
             "%s?%s" % (base, query)],
            timeout=12,
        )
        if code != 0 or not out:
            continue
        try:
            data = json.loads(out)
        except ValueError:
            continue
        for answer in data.get("Answer", []):
            value = str(answer.get("data", ""))
            if answer.get("type") == 1 and re.match(r"^\d+\.\d+\.\d+\.\d+$", value):
                cache_ip(value)
                return value
    return read_cached_ip()


def moat_request(path, payload=None, timeout=25):
    ip = doh_resolve()
    body = json.dumps(payload) if payload is not None else None
    args = ["curl", "-sS", "-m", str(timeout), "-H",
            "Content-Type: application/vnd.api+json", "-H", "Accept: application/json"]
    if body is not None:
        args += ["-X", "POST", "-d", body]

    if MOAT_DIRECT and ip:
        cmd = args + ["--resolve", "%s:443:%s" % (MOAT_HOST, ip),
                      "https://%s%s" % (MOAT_HOST, path)]
        code, out, err = run(cmd, timeout=timeout + 8)
        if code == 0 and out.strip():
            try:
                return json.loads(out)
            except ValueError:
                log("moat: invalid json from direct request")
        elif err:
            log("moat: direct request failed: %s" % err.strip()[:300])

    # Fallback: go out through the running tor/privoxy.
    cmd = args + ["-x", PROXY_URL, "https://%s%s" % (MOAT_HOST, path)]
    code, out, err = run(cmd, timeout=timeout + 8)
    if code == 0 and out.strip():
        try:
            return json.loads(out)
        except ValueError:
            log("moat: invalid json from proxied request")
    else:
        log("moat: proxied request failed: %s" % (err or "no output").strip()[:300])
    return None


def fetch_candidates():
    found = []
    sources = {}

    payload = {"transports": ["obfs4"]}
    if MOAT_COUNTRY:
        payload["country"] = MOAT_COUNTRY
    settings = moat_request("/moat/circumvention/settings", payload)
    if settings:
        for item in settings.get("settings", []):
            bridges = item.get("bridges", {})
            if bridges.get("type") != "obfs4":
                continue
            for line in bridges.get("bridge_strings", []):
                line = clean_line(line)
                if valid_bridge(line) and line not in found:
                    found.append(line)
                    sources[line] = "moat/%s" % bridges.get("source", "settings")

    builtin = moat_request("/moat/circumvention/builtin", timeout=25)
    if builtin:
        for line in builtin.get("obfs4", []):
            line = clean_line(line)
            if valid_bridge(line) and line not in found:
                found.append(line)
                sources[line] = "moat/builtin"

    for line in current_bridges():
        line = clean_line(line)
        if valid_bridge(line) and line not in found:
            found.append(line)
            sources[line] = "current"

    return found, sources


def tail(path, limit=400):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
    except OSError:
        return ""
    return data[-limit:].replace("\n", " ")


def test_bridge(line, timeout=TEST_TIMEOUT):
    workdir = tempfile.mkdtemp(prefix="bridge-test-")
    logfile = os.path.join(workdir, "tor.log")
    config = os.path.join(workdir, "torrc")
    try:
        with open(config, "w", encoding="utf-8") as fh:
            fh.write("SocksPort 0\n")
            fh.write("DataDirectory %s\n" % workdir)
            fh.write("Log notice file %s\n" % logfile)
            fh.write("ClientTransportPlugin obfs4 exec %s\n" % OBFS4_BIN)
            fh.write("UseBridges 1\n")
            fh.write("Bridge %s\n" % line)

        started = time.time()
        proc = subprocess.Popen(
            [TOR_BIN, "-f", config],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        offset = 0
        result = None
        while time.time() - started < timeout:
            if proc.poll() is not None:
                result = (False, time.time() - started,
                          "tor exited: %s" % (tail(logfile) or "no log"))
                break
            try:
                with open(logfile, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                    offset = fh.tell()
            except OSError:
                chunk = ""
            if "Bootstrapped 100%" in chunk:
                result = (True, time.time() - started, "bootstrapped")
                break
            time.sleep(0.4)
        if result is None:
            result = (False, time.time() - started,
                      "timeout: %s" % (tail(logfile) or "no log"))
        return result
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, UnboundLocalError):
            pass
        time.sleep(0.5)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, UnboundLocalError):
            pass
        shutil.rmtree(workdir, ignore_errors=True)


def sv_status():
    for sv in ("/usr/sbin/sv", "/sbin/sv", "/usr/bin/sv", shutil.which("sv")):
        if not sv:
            continue
        code, out, err = run([sv, "status", "tor"], timeout=8)
        if code == 0 and (out or err):
            return (out or err).strip()
    code, out, _ = run(["pidof", "tor"], timeout=5)
    if code == 0 and out.strip():
        return "running (pid %s)" % out.strip().split()[0]
    return "not running"


def tor_running():
    status = sv_status()
    return status.startswith("run:") or status.startswith("running")


def reload_tor():
    code, out, err = run([RELOAD_SCRIPT], timeout=30)
    if code != 0:
        return False, (err or out or "reload failed").strip()
    return True, "tor restarted"


def health_check(timeout=HEALTH_TIMEOUT):
    url = "https://check.torproject.org/api/ip"
    attempts = (
        ["curl", "-sS", "-m", str(timeout), "-x", PROXY_URL, url],
        ["curl", "-sS", "-m", str(timeout), "--socks5-hostname",
         "127.0.0.1:9050", url],
    )
    error = ""
    for cmd in attempts:
        code, out, err = run(cmd, timeout=timeout + 5)
        if code != 0 or not out.strip():
            error = (err or "empty response").strip()[:300]
            continue
        try:
            data = json.loads(out)
        except ValueError:
            error = "invalid health response"
            continue
        result = {
            "ts": now(),
            "ok": bool(data.get("IsTor")),
            "ip": str(data.get("IP", "")),
            "tor": bool(data.get("IsTor")),
            "error": "",
        }
        with _last_health_lock:
            last_health.update(result)
        return result
    result = {"ts": now(), "ok": False, "ip": "", "tor": False, "error": error}
    with _last_health_lock:
        last_health.update(result)
    return result


def job_snapshot():
    with job_lock:
        snapshot = dict(job)
        snapshot["candidates"] = [dict(item) for item in job["candidates"]]
    return snapshot


def start_update(reason):
    with job_lock:
        if job["running"]:
            return False
        job.update({
            "running": True,
            "phase": "fetch",
            "message": "fetching fresh bridges",
            "reason": reason,
            "started": now(),
            "finished": 0,
            "candidates": [],
        })
    threading.Thread(target=update_worker, args=(reason,), daemon=True).start()
    return True


def update_worker(reason):
    log("update started (%s)" % reason)
    try:
        candidates, sources = fetch_candidates()
        with job_lock:
            job["candidates"] = [
                {"line": line, "status": "pending", "seconds": 0,
                 "note": sources.get(line, "")}
                for line in candidates[:MAX_TEST]
            ]
        if not candidates:
            with job_lock:
                job["phase"] = "done"
                job["message"] = "Moat returned no bridges"
            add_history("update", False, "Moat returned no bridges")
            return

        working = []
        with job_lock:
            job["phase"] = "test"
            job["message"] = "testing %d bridges" % min(len(candidates), MAX_TEST)

        for index, line in enumerate(candidates[:MAX_TEST]):
            with job_lock:
                job["candidates"][index]["status"] = "testing"
                job["message"] = "testing %d/%d" % (index + 1, min(len(candidates), MAX_TEST))
            try:
                ok, seconds, note = test_bridge(line)
            except Exception as exc:  # noqa: BLE001 - one bad line must not stop the run
                ok, seconds, note = False, 0.0, "test error: %s" % exc
            with job_lock:
                job["candidates"][index].update({
                    "status": "ok" if ok else "fail",
                    "seconds": round(seconds, 1),
                    "note": note[:300],
                })
            log("bridge %s: %s (%.1fs)" % ("ok" if ok else "fail", line.split()[1], seconds))
            if ok:
                working.append((seconds, line))
            if len(working) >= TARGET_BRIDGES:
                break

        if not working:
            with job_lock:
                job["phase"] = "done"
                job["message"] = "no working bridges found, kept the previous set"
            add_history("update", False, "no working bridges among %d tested" %
                        min(len(candidates), MAX_TEST))
            return

        working.sort(key=lambda item: item[0])
        chosen = [line for _, line in working[:TARGET_BRIDGES]]
        with job_lock:
            job["phase"] = "apply"
            job["message"] = "applying %d bridges" % len(chosen)
        write_bridges(chosen)
        ok, detail = reload_tor()
        if not ok:
            with job_lock:
                job["phase"] = "done"
                job["message"] = "Tor restart failed: %s" % detail
            add_history("update", False, detail)
            return

        deadline = time.time() + 90
        health = {"ok": False, "ip": "", "error": "timeout"}
        while time.time() < deadline:
            time.sleep(5)
            health = health_check()
            if health.get("ok"):
                break

        with state_lock:
            state["last_update"] = now()
            state["last_refresh"] = now()
        save_state()
        _runtime["consecutive_fails"] = 0
        with job_lock:
            job["phase"] = "done"
            job["message"] = ("updated, exit IP %s" % health.get("ip")
                              if health.get("ok") else
                              "bridges applied, but the proxy is not responding yet")
        add_history("update", bool(health.get("ok")),
                    "applied %d bridges, exit %s" % (len(chosen), health.get("ip") or "-"))
    except Exception as exc:  # noqa: BLE001 - keep the panel alive
        log("update failed: %s" % exc)
        with job_lock:
            job["phase"] = "done"
            job["message"] = "error: %s" % exc
        add_history("update", False, str(exc))
    finally:
        with state_lock:
            state["last_refresh"] = now()
        save_state()
        with job_lock:
            job["running"] = False
            job["finished"] = now()


def auto_loop():
    while True:
        try:
            time.sleep(30)
            with state_lock:
                auto = state["auto"]
                interval = int(state["health_interval"])
                refresh_hours = float(state["refresh_hours"])
                threshold = int(state["fail_threshold"])
                last_update = int(state["last_update"])

            if not auto:
                continue

            if now() - _runtime["last_health_ts"] >= interval:
                health = health_check()
                _runtime["last_health_ts"] = now()
                if health.get("ok"):
                    _runtime["consecutive_fails"] = 0
                else:
                    _runtime["consecutive_fails"] += 1
                    log("health check failed (%d/%d)" %
                        (_runtime["consecutive_fails"], threshold))

            if _runtime["consecutive_fails"] >= threshold:
                if start_update("health"):
                    _runtime["consecutive_fails"] = 0

            with state_lock:
                last_refresh = int(state["last_refresh"])
            base = max(last_update, last_refresh, _runtime["started"], 1)
            if refresh_hours > 0 and now() - base >= refresh_hours * 3600:
                start_update("schedule")
        except Exception as exc:  # noqa: BLE001
            log("auto loop error: %s" % exc)


def load_password():
    if PANEL_DISABLE_AUTH:
        return ""
    if PANEL_PASS:
        return PANEL_PASS
    try:
        with open(PASSWORD_FILE, "r", encoding="utf-8") as fh:
            password = fh.read().strip()
            if password:
                return password
    except OSError:
        pass
    password = secrets.token_urlsafe(9)
    try:
        ensure_data_dir()
        with open(PASSWORD_FILE, "w", encoding="utf-8") as fh:
            fh.write(password + "\n")
        os.chmod(PASSWORD_FILE, 0o600)
        log("generated panel password: %s (user %s)" % (password, PANEL_USER))
    except OSError as exc:
        log("cannot store panel password: %s" % exc)
    return password


class Handler(BaseHTTPRequestHandler):
    server_version = "torproxy-panel/" + VERSION
    password = ""

    def log_message(self, fmt, *args):
        log("%s %s" % (self.address_string(), fmt % args))

    def _authorized(self):
        if not self.password:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
            user, _, supplied = decoded.partition(":")
        except Exception:  # noqa: BLE001
            return False
        return (hmac.compare_digest(user.encode("utf-8"), PANEL_USER.encode("utf-8")) and
                hmac.compare_digest(supplied.encode("utf-8"), self.password.encode("utf-8")))

    def _send(self, code, body, content_type="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data, ensure_ascii=False))

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if length <= 0 or length > 65536:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _check_post(self):
        return self.headers.get("X-Requested-With") == "XMLHttpRequest"

    def do_GET(self):
        if not self._authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="torproxy"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(INDEX_FILE, "rb") as fh:
                    self._send(200, fh.read(), "text/html")
            except OSError as exc:
                self._send(500, "index missing: %s" % exc, "text/plain")
            return
        if path == "/api/status":
            self._json(status_payload())
            return
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self._authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="torproxy"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self._check_post():
            self._json({"error": "missing X-Requested-With"}, 403)
            return
        path = urllib.parse.urlparse(self.path).path
        payload = self._read_json()

        if path == "/api/update":
            started = start_update(payload.get("reason", "manual"))
            self._json({"ok": True, "started": started, "job": job_snapshot()})
        elif path == "/api/health":
            self._json({"ok": True, "health": health_check()})
        elif path == "/api/restart":
            ok, detail = reload_tor()
            add_history("restart", ok, detail)
            self._json({"ok": ok, "detail": detail, "tor_status": sv_status()})
        elif path == "/api/apply":
            bridges = payload.get("bridges", [])
            if not isinstance(bridges, list):
                self._json({"error": "bridges must be a list"}, 400)
                return
            try:
                applied = write_bridges(bridges)
            except (ValueError, OSError) as exc:
                self._json({"error": str(exc)}, 400)
                return
            ok, detail = reload_tor()
            add_history("manual apply", ok, "%d bridges; %s" % (len(applied), detail))
            self._json({"ok": ok, "applied": applied, "detail": detail})
        elif path == "/api/auto":
            enabled = bool(payload.get("enabled"))
            with state_lock:
                state["auto"] = enabled
            save_state()
            add_history("auto", True, "enabled" if enabled else "disabled")
            self._json({"ok": True, "auto": enabled})
        elif path == "/api/config":
            changed = {}
            for key, low, high in (("refresh_hours", 1, 8760),
                                   ("health_interval", 30, 86400),
                                   ("fail_threshold", 1, 20)):
                if key in payload:
                    try:
                        value = int(payload[key])
                    except (TypeError, ValueError):
                        continue
                    value = max(low, min(high, value))
                    with state_lock:
                        state[key] = value
                    changed[key] = value
            save_state()
            self._json({"ok": True, "changed": changed})
        else:
            self._json({"error": "not found"}, 404)


def status_payload():
    with _last_health_lock:
        health = dict(last_health)
    with state_lock:
        state_copy = {key: state[key] for key in
                      ("auto", "refresh_hours", "health_interval", "fail_threshold",
                       "last_update", "last_refresh")}
        history = list(state["history"][-12:])
    return {
        "version": VERSION,
        "tor_status": sv_status(),
        "tor_running": tor_running(),
        "health": health,
        "bridges": current_bridges(),
        "job": job_snapshot(),
        "state": state_copy,
        "history": history,
        "country": MOAT_COUNTRY,
        "data_dir": DATA_DIR,
    }


def selftest():
    print("tor:", TOR_BIN, os.path.exists(TOR_BIN))
    print("obfs4proxy:", OBFS4_BIN, os.path.exists(OBFS4_BIN))
    print("doh ip:", doh_resolve())
    candidates, sources = fetch_candidates()
    print("candidates:", len(candidates))
    for line in candidates:
        print(" ", sources.get(line, "?"), line[:100])


def main():
    ensure_data_dir()
    load_state()
    _runtime["started"] = now()
    ensure_bridges_file()
    Handler.password = load_password()
    if not Handler.password:
        log("authentication disabled")
    else:
        log("authentication enabled for user %s" % PANEL_USER)
    threading.Thread(target=auto_loop, daemon=True).start()
    server = ThreadingHTTPServer((PANEL_BIND, PANEL_PORT), Handler)
    server.daemon_threads = True
    log("listening on %s:%d, data=%s, country=%s" %
        (PANEL_BIND, PANEL_PORT, DATA_DIR, MOAT_COUNTRY))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        selftest()
    else:
        main()
