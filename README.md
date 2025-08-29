Live Status HTTP Site
======================

Tiny, dependency‑free Python server that serves a live status page:

- Current date and time (local, with TZ)
- Hostname and IPv4(s)
- Continuous ping results (default target: `1.1.1.1`)
- Disk activity proof via a heartbeat file written every second
- No caching; page auto‑refreshes every second
- Page updates in-place via a tiny JS fetch every second (no full reload).
- Optional: enable meta refresh instead with `META_REFRESH=1`.
- Events log records ping outages and heartbeat write errors/recoveries
 - UI shows per-target missed ping count and tails the events log at the bottom


Quick Start
-----------

Requirements: Python 3.7+ and the system `ping` binary available in PATH.

Run:

```
python3 live_status.py              # listens on 0.0.0.0:8080
# or choose a port
python3 live_status.py 9090
# or via env var
PORT=9000 python3 live_status.py
```

Open the page:

```
http://<server-ip>:8080/
```


Notes
-----

- The server runs two lightweight background workers:
  - Pings `10.50.20.1` and `1.1.1.1` roughly every second.
  - Appends a JSON line to `heartbeat.txt` once per second and fsyncs it.
- Headers and meta tags disable caching; the page reloads every second.
- The primary IPv4 is derived from the system’s default route; additional local IPv4s are shown if resolvable.
- A JSON health endpoint is available at `/health` for simple checks.

- If you switch to port 80 on Linux, it requires root or capability `CAP_NET_BIND_SERVICE`.
  - Run with sudo: `sudo PORT=80 python3 live_status.py`
  - Or grant capability once: `sudo setcap 'cap_net_bind_service=+ep' $(command -v python3)`


Customization
-------------

- Change heartbeat location:

```
HEARTBEAT_PATH=/var/tmp/heartbeat.txt python3 live_status.py
```

- Change ping targets: edit `PING_TARGETS` in `live_status.py`.
  - Or set via env var (comma-separated):

```
PING_TARGETS="1.1.1.1,10.50.20.1,8.8.8.8" ./install_and_run.sh
```

At runtime, you can also add a target using the small form on the page (survives page reloads for the running process).

- Change events log location:

```
EVENTS_LOG_PATH=/var/log/live-status/events.log python3 live_status.py
```


Systemd (optional)
------------------

Example unit file (`/etc/systemd/system/live-status.service`):

```
[Unit]
Description=Live Status HTTP
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/live-status/live_status.py 8080
WorkingDirectory=/opt/live-status
Restart=always
Environment=HEARTBEAT_PATH=/var/tmp/heartbeat.txt

[Install]
WantedBy=multi-user.target
```

Then:

```
sudo systemctl daemon-reload
sudo systemctl enable --now live-status
```


Troubleshooting
---------------

- If ping shows FAIL, ensure the host is reachable and that the `ping` utility is installed and permitted for non‑root users.
- On macOS/BSD, the script tries alternate `ping` flags automatically.
- If the heartbeat shows an error, check that the directory for `HEARTBEAT_PATH` is writable by the process.
- Event log: tail it to see outages and recoveries

```
tail -f events.log
```


Meta Refresh (optional)
----------------------

By default the page uses JS to update content without reloading. If you explicitly want browser meta refresh instead, set:

```
META_REFRESH=1 ./install_and_run.sh
```
