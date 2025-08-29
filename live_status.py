#!/usr/bin/env python3
import os
import sys
import time
import json
import socket
import shutil
import threading
import subprocess
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from datetime import datetime, timezone


# Configuration
PING_TARGETS = ["10.50.20.1", "1.1.1.1"]
PING_INTERVAL_SECONDS = 1.0
PING_SUBPROCESS_TIMEOUT = 2.0  # safety timeout per ping attempt

# Use env var if provided and non-empty; otherwise default to cwd/heartbeat.txt
_hb_env = os.environ.get("HEARTBEAT_PATH")
HEARTBEAT_PATH = _hb_env if _hb_env else os.path.join(os.getcwd(), "heartbeat.txt")

# Events log (records ping outages and disk errors)
_ev_env = os.environ.get("EVENTS_LOG_PATH")
EVENTS_LOG_PATH = _ev_env if _ev_env else os.path.join(os.getcwd(), "events.log")
EVENTS_FSYNC = os.environ.get("EVENTS_FSYNC", "0") == "1"
HEARTBEAT_INTERVAL_SECONDS = 1.0

DEFAULT_PORT = int(os.environ.get("PORT", "8080"))


def now_utc():
    return datetime.now(timezone.utc)


def format_dt(dt: datetime) -> str:
    # Human friendly with timezone
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z (%z)")


def get_primary_ipv4() -> str:
    try:
        # Determine primary outbound IP by connecting a UDP socket (no packets sent)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        # Fallback to hostname resolution
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "Unknown"


def all_ipv4_addrs() -> list:
    seen = set()
    addrs = []
    # Primary first
    primary = get_primary_ipv4()
    if primary and primary != "Unknown":
        seen.add(primary)
        addrs.append(primary)
    # Add any others resolvable via getaddrinfo
    try:
        hostname = socket.gethostname()
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if ip not in seen and not ip.startswith("127."):
                seen.add(ip)
                addrs.append(ip)
    except Exception:
        pass
    if not addrs:
        addrs.append("Unknown")
    return addrs


class PingState:
    def __init__(self, targets):
        self.lock = threading.Lock()
        # target -> {"ok": bool, "latency_ms": float|None, "ts": datetime, "err": str|None, "misses": int, "checks": int}
        self.latest = {
            t: {"ok": False, "latency_ms": None, "ts": None, "err": None, "misses": 0, "checks": 0}
            for t in targets
        }

    def update(self, target, ok: bool, latency_ms, err: Optional[str]):
        with self.lock:
            prev = self.latest.get(target) or {}
            self.latest[target] = {
                "ok": ok,
                "latency_ms": latency_ms,
                "ts": now_utc(),
                "err": err,
                "misses": prev.get("misses", 0),
                "checks": prev.get("checks", 0),
            }

    def increment(self, target, ok: bool):
        with self.lock:
            st = self.latest.setdefault(target, {"ok": False, "latency_ms": None, "ts": None, "err": None, "misses": 0, "checks": 0})
            st["checks"] = st.get("checks", 0) + 1
            if not ok:
                st["misses"] = st.get("misses", 0) + 1

    def snapshot(self):
        with self.lock:
            # Deep copy not needed for rendering simple values
            return {t: v.copy() for t, v in self.latest.items()}


PING_STATE = PingState(PING_TARGETS)


class EventLogger:
    def __init__(self, path: str, fsync: bool = False):
        self.path = path
        self.fsync = fsync
        self.lock = threading.Lock()
        # Ensure directory exists
        d = os.path.dirname(self.path)
        if d and not os.path.exists(d):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass

    def log(self, event: str, **fields):
        ts = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        parts = [f"{k}={json.dumps(v, separators=(',',':'))}" for k, v in fields.items()]
        line = f"{ts} {event} " + " ".join(parts) + "\n"
        with self.lock:
            try:
                with open(self.path, "ab", buffering=0) as f:
                    f.write(line.encode("utf-8"))
                    if self.fsync:
                        os.fsync(f.fileno())
            except Exception:
                # As a fallback, print to stderr
                try:
                    sys.stderr.write(line)
                except Exception:
                    pass


EVENTS = EventLogger(EVENTS_LOG_PATH, fsync=EVENTS_FSYNC)


def try_ping_once(target: str):
    # Try a couple of common ping variants to be cross-platform
    variants = [
        ["ping", "-c", "1", "-W", "1", target],  # Linux style timeout seconds
        ["ping", "-c", "1", "-t", "1", target],  # macOS/BSD TTL as crude timeout
    ]

    last_error = None
    for args in variants:
        try:
            start = time.perf_counter()
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=PING_SUBPROCESS_TIMEOUT,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if proc.returncode == 0:
                # Attempt to parse latency from stdout, fallback to elapsed time
                m = re.search(r"time[=<]([0-9.]+)\s*ms", proc.stdout)
                if m:
                    latency_ms = float(m.group(1))
                else:
                    latency_ms = round(elapsed_ms, 2)
                return True, latency_ms, None
            else:
                last_error = proc.stderr or proc.stdout
        except FileNotFoundError as e:
            last_error = str(e)
            break
        except subprocess.TimeoutExpired as e:
            last_error = f"timeout ({e})"
        except Exception as e:
            last_error = str(e)

    # If we got here, ping failed
    return False, None, last_error or "unknown"


def ping_worker():
    prev_ok = {t: None for t in PING_TARGETS}
    while True:
        cycle_start = time.monotonic()
        for target in PING_TARGETS:
            ok, latency, err = try_ping_once(target)
            PING_STATE.increment(target, ok)
            PING_STATE.update(target, ok, latency, err)
            if prev_ok[target] is None:
                # First observation: log it so we have a baseline
                if ok:
                    EVENTS.log("PING_UP", target=target, latency_ms=latency)
                else:
                    EVENTS.log("PING_DOWN", target=target, error=err)
            elif prev_ok[target] is not ok:
                # Transition
                if ok:
                    EVENTS.log("PING_UP", target=target, latency_ms=latency)
                else:
                    EVENTS.log("PING_DOWN", target=target, error=err)
            prev_ok[target] = ok
        # Pace the loop roughly to the interval
        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, PING_INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_for)


class HeartbeatState:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.last_write_ts = None
        self.last_error = None
        self.bytes_written = 0

    def snapshot(self):
        with self.lock:
            return {
                "path": self.path,
                "last_write_ts": self.last_write_ts,
                "last_error": self.last_error,
                "bytes_written": self.bytes_written,
            }

    def record_success(self, nbytes):
        with self.lock:
            self.last_write_ts = now_utc()
            self.last_error = None
            self.bytes_written = nbytes

    def record_error(self, err):
        with self.lock:
            self.last_error = str(err)


HEARTBEAT = HeartbeatState(HEARTBEAT_PATH)


def heartbeat_worker():
    # Ensure directory exists
    hb_dir = os.path.dirname(HEARTBEAT.path)
    if hb_dir and not os.path.exists(hb_dir):
        try:
            os.makedirs(hb_dir, exist_ok=True)
        except Exception as e:
            HEARTBEAT.record_error(e)

    had_error = False
    while True:
        try:
            payload = {
                "ts": now_utc().isoformat(),
                "host": socket.gethostname(),
            }
            data = (json.dumps(payload) + "\n").encode("utf-8")
            with open(HEARTBEAT.path, "ab", buffering=0) as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            HEARTBEAT.record_success(len(data))
            if had_error:
                EVENTS.log("HEARTBEAT_RECOVERED", path=HEARTBEAT.path, bytes=len(data))
                had_error = False
        except Exception as e:
            HEARTBEAT.record_error(e)
            if not had_error:
                EVENTS.log("HEARTBEAT_ERROR", path=HEARTBEAT.path, error=str(e))
                had_error = True
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


def human_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:.1f} {u}"
        x /= 1024.0


def disk_usage_summary(path: str) -> dict:
    try:
        usage = shutil.disk_usage(path)
        return {
            "path": path,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        }
    except Exception:
        # Fallback to root
        try:
            usage = shutil.disk_usage("/")
            return {
                "path": "/",
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
            }
        except Exception:
            return {"path": path, "total": 0, "used": 0, "free": 0}


def html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def render_index() -> bytes:
    now_local = format_dt(datetime.now())
    host = socket.gethostname()
    ips = ", ".join(all_ipv4_addrs())
    pings = PING_STATE.snapshot()
    hb = HEARTBEAT.snapshot()
    disk = disk_usage_summary(os.getcwd())

    def ping_row(target, data):
        status = "OK" if data.get("ok") else "FAIL"
        latency = data.get("latency_ms")
        ts = data.get("ts")
        when = format_dt(ts) if ts else "—"
        color = "#22aa22" if status == "OK" else "#cc2222"
        latency_s = f"{latency:.2f} ms" if latency is not None else "—"
        misses = data.get("misses", 0)
        return f"<tr><td>{html_escape(target)}</td><td style='color:{color};font-weight:bold'>{status}</td><td>{latency_s}</td><td>{misses}</td><td>{html_escape(when)}</td></tr>"

    hb_when = format_dt(hb["last_write_ts"]) if hb["last_write_ts"] else "—"
    hb_err = hb["last_error"]
    hb_err_html = f"<div style='color:#cc2222'>Error: {html_escape(hb_err)}</div>" if hb_err else ""
    hb_file = HEARTBEAT.path

    # Tail events log for UI display
    def tail_events(path: str, max_bytes: int = 65536, max_lines: int = 200) -> str:
        try:
            size = os.path.getsize(path)
            with open(path, 'rb') as f:
                if size > max_bytes:
                    f.seek(-max_bytes, os.SEEK_END)
                data = f.read().decode('utf-8', errors='replace')
            lines = data.splitlines()[-max_lines:]
            return "\n".join(lines)
        except Exception:
            return "(no events yet)"

    events_text = tail_events(EVENTS_LOG_PATH)

    # Simple, light HTML. Auto-refresh every 1 second.
    html = f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta http-equiv="cache-control" content="no-store, no-cache, must-revalidate" />
  <meta http-equiv="pragma" content="no-cache" />
  <meta http-equiv="expires" content="0" />
  <meta http-equiv="refresh" content="1" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Live Server Status</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 1.5rem; color: #222; background: #fafafa; }}
    h1 {{ margin: 0 0 0.25rem 0; font-size: 1.4rem; }}
    .muted {{ color: #666; font-size: 0.9rem; }}
    table {{ border-collapse: collapse; margin-top: 0.5rem; }}
    th, td {{ padding: 0.35rem 0.6rem; border-bottom: 1px solid #e5e5e5; text-align: left; }}
    .section {{ margin-top: 1rem; }}
    code {{ background: #f2f2f2; padding: 0.1rem 0.25rem; border-radius: 3px; }}
    pre {{ background: #f8f8f8; border: 1px solid #eee; padding: 0.5rem; border-radius: 4px; max-height: 240px; overflow: auto; }}
  </style>
</head>
<body>
  <h1>Live Server Status</h1>
  <div class="muted">Updated: {html_escape(now_local)}</div>

  <div class="section">
    <strong>Hostname:</strong> {html_escape(host)}<br />
    <strong>IP:</strong> {html_escape(ips)}
  </div>

  <div class="section">
    <strong>Ping</strong>
    <table>
      <thead>
        <tr><th>Target</th><th>Status</th><th>Latency</th><th>Missed</th><th>Last Check</th></tr>
      </thead>
      <tbody>
        {''.join(ping_row(t, pings.get(t, {})) for t in PING_TARGETS)}
      </tbody>
    </table>
  </div>

  <div class="section">
    <strong>Disk Activity</strong>
    <div>Heartbeat file: <code>{html_escape(hb_file)}</code></div>
    <div>Last write: {html_escape(hb_when)} | Chunk: {hb['bytes_written']} bytes</div>
    {hb_err_html}
    <div class="muted">Disk usage ({html_escape(disk['path'])}): total {human_bytes(disk['total'])}, used {human_bytes(disk['used'])}, free {human_bytes(disk['free'])}</div>
  </div>

  <div class="section">
    <strong>Events Log</strong>
    <div class="muted">Path: <code>{html_escape(EVENTS_LOG_PATH)}</code> (last entries)</div>
    <pre>{html_escape(events_text)}</pre>
    <div class="muted">Cache disabled; page refreshes every second.</div>
  </div>
</body>
</html>
"""
    return html.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "LiveStatus/0.1"

    def _no_cache_headers(self, content_type="text/html; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/health"):
            payload = {"ok": True, "time": now_utc().isoformat(), "host": socket.gethostname()}
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(data)
            return

        # Default: index
        body = render_index()
        self._no_cache_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Slim logging with timestamp
        sys.stderr.write("[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fmt % args))


def run(port: int):
    # Start background workers
    t1 = threading.Thread(target=ping_worker, name="ping-worker", daemon=True)
    t1.start()

    t2 = threading.Thread(target=heartbeat_worker, name="heartbeat-worker", daemon=True)
    t2.start()

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[live_status] Listening on 0.0.0.0:{port}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.shutdown()


def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except Exception:
            print(f"Invalid port: {sys.argv[1]}", file=sys.stderr)
            sys.exit(2)
    run(port)


if __name__ == "__main__":
    main()
