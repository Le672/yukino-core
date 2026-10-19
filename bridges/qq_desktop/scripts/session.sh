#!/bin/sh
set -eu
export HOME=/data/home
mkdir -p /data/qq-profile /data/bridge "$HOME"
Xvfb :98 -screen 0 1280x800x24 -nolisten tcp &
XVFB_PID=$!
trap 'kill "$XVFB_PID" ${QQ_PID:-} ${VNC_PID:-} ${WEB_PID:-} 2>/dev/null || true' EXIT INT TERM
sleep 1
# UI services share this one D-Bus session. The actual QQ binary is unchanged.
/opt/qq-client/opt/QQ/qq --no-sandbox --disable-gpu --disable-dev-shm-usage \
    --force-renderer-accessibility --user-data-dir=/data/qq-profile >/data/qq-desktop.log 2>&1 &
QQ_PID=$!
x11vnc -display :98 -forever -shared -nopw -listen 127.0.0.1 -rfbport 5900 >/data/vnc.log 2>&1 &
VNC_PID=$!
websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 >/data/novnc.log 2>&1 &
WEB_PID=$!
python3 -m qq_desktop serve --config /data/config.json --data /data/bridge &
BRIDGE_PID=$!
wait "$BRIDGE_PID"
