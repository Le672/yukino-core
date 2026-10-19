#!/bin/sh
set -eu
exec dbus-run-session -- /app/scripts/session.sh
