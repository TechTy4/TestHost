#!/usr/bin/env bash
set -euo pipefail

# Quick installer/runner for the Live Status monitor.
# - Clones or updates the repo
# - Launches the server with nohup and writes live_status.pid
#
# Config via env vars (override as needed):
#   REPO_URL       - Git URL to clone (defaults to this repo)
#   TARGET_PARENT  - Parent dir for checkout (default: $HOME)
#   PORT           - HTTP port (default: 8080)
#   HEARTBEAT_PATH - Heartbeat file path (optional)

REPO_URL="${REPO_URL:-https://github.com/TechTy4/TestHost.git}"
TARGET_PARENT="${TARGET_PARENT:-$HOME}"
PORT="${PORT:-8080}"
HEARTBEAT_PATH="${HEARTBEAT_PATH:-}"
EVENTS_LOG_PATH="${EVENTS_LOG_PATH:-}"

repo_name="${REPO_URL##*/}"        # e.g. TestHost.git
repo_dir="${repo_name%.git}"       # e.g. TestHost
TARGET_DIR="${TARGET_PARENT%/}/${repo_dir}"

echo "[live-status] Repo:    ${REPO_URL}"
echo "[live-status] Checkout: ${TARGET_DIR}"
echo "[live-status] Port:    ${PORT}"
if [[ -n "$HEARTBEAT_PATH" ]]; then
  echo "[live-status] Heartbeat: ${HEARTBEAT_PATH}"
fi
if [[ -n "$EVENTS_LOG_PATH" ]]; then
  echo "[live-status] Events log: ${EVENTS_LOG_PATH}"
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Error: git is required" >&2
  exit 1
fi

# Clone or update
if [[ ! -d "${TARGET_DIR}/.git" ]]; then
  mkdir -p "${TARGET_PARENT}"
  git clone --depth 1 "${REPO_URL}" "${TARGET_DIR}"
else
  echo "[live-status] Updating existing checkout..."
  # Force update to remote to avoid conflicts with generated files like heartbeat.txt
  git -C "${TARGET_DIR}" fetch --all --prune
  # Reset working tree to origin/main (destructive to local changes)
  if git -C "${TARGET_DIR}" show-ref --verify --quiet refs/remotes/origin/main; then
    git -C "${TARGET_DIR}" reset --hard origin/main
  else
    # Fallback: reset to fetched HEAD
    git -C "${TARGET_DIR}" reset --hard FETCH_HEAD
  fi
  git -C "${TARGET_DIR}" clean -fd
fi

# Pick Python
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Error: Python 3 is required (python3 not found)" >&2
  exit 1
fi

if ! command -v ping >/dev/null 2>&1; then
  echo "[live-status] Warning: 'ping' not found; ping checks may show FAIL." >&2
fi

cd "${TARGET_DIR}"

# Advisory for privileged ports on Linux
if [[ "${PORT}" -lt 1024 ]]; then
  if [[ "${EUID}" -ne 0 ]]; then
    if [[ "$(uname -s)" == "Linux" ]]; then
      echo "[live-status] Note: Port ${PORT} requires root or CAP_NET_BIND_SERVICE."
      echo "              Options:"
      echo "              - Run with sudo: sudo PORT=${PORT} ./install_and_run.sh"
      echo "              - Or grant capability: sudo setcap 'cap_net_bind_service=+ep' \"$(command -v ${PYTHON_BIN})\""
    fi
  fi
fi

# Stop any previous instance
if [[ -f live_status.pid ]]; then
  OLD_PID=$(cat live_status.pid || true)
  if [[ -n "${OLD_PID:-}" ]] && ps -p "${OLD_PID}" >/dev/null 2>&1; then
    echo "[live-status] Stopping previous instance (pid ${OLD_PID})"
    kill "${OLD_PID}" || true
    sleep 0.5 || true
  fi
fi

if [[ -n "$HEARTBEAT_PATH" ]]; then
  export HEARTBEAT_PATH
fi
if [[ -n "$EVENTS_LOG_PATH" ]]; then
  export EVENTS_LOG_PATH
fi
echo "[live-status] Starting server..."
nohup "${PYTHON_BIN}" live_status.py "${PORT}" > live_status.log 2>&1 &
NEW_PID=$!
echo "${NEW_PID}" > live_status.pid
sleep 1

# Quick health check
URL="http://127.0.0.1:${PORT}/health"
if command -v curl >/dev/null 2>&1; then
  curl -fsS --max-time 2 "$URL" || true
elif command -v wget >/dev/null 2>&1; then
  wget -qO- "$URL" || true
fi

# Ensure it stayed up
if ! ps -p "${NEW_PID}" >/dev/null 2>&1; then
  echo "[live-status] Server failed to start. Recent log:" >&2
  tail -n 60 live_status.log >&2 || true
  exit 1
fi

echo "[live-status] Running (pid ${NEW_PID}). Logs: ${TARGET_DIR}/live_status.log"
echo "[live-status] Visit: http://$(hostname -f 2>/dev/null || hostname):${PORT}/"
echo "[live-status] To stop: kill \$(cat ${TARGET_DIR}/live_status.pid)"
