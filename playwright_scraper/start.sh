#!/bin/bash
# Explicit Xvfb startup, replacing the earlier xvfb-run wrapper (2026-07-28).
#
# xvfb-run turned out to be a black box we'd never actually confirmed
# worked in THIS container: it depends on xauth internally, uses xdpyinfo
# to check readiness before running the wrapped command, and fails
# SILENTLY from this service's point of view — it doesn't crash
# python main.py, it just leaves DISPLAY pointing at a server that isn't
# really there. That went undetected because _capture_pickup_flow (the
# only thing tested right after the xvfb-run deploy) never actually
# launches a headful browser at all (see main.py's _new_browser_and_context
# calls) — it succeeded on headless alone, which needs no display,
# proving nothing about Xvfb. _check_pickup_availability is the ONLY
# code path that launches headful Chromium (headless=APPLE_PICKUP_HEADLESS),
# and it's the one that surfaced "Missing X server or $DISPLAY" — the
# first real test Xvfb had actually been given.
#
# This script starts Xvfb directly, explicitly polls for it to actually
# accept connections (xdpyinfo) before ever handing control to
# python main.py, and logs the outcome either way — so a future failure
# shows up as a clear line in Railway's logs ("Xvfb is up" vs "Xvfb did
# NOT become ready") instead of another silent guess.
set -e

export DISPLAY=:99
Xvfb :99 -screen 0 1280x800x24 -ac -nolisten tcp &

READY=0
for i in $(seq 1 20); do
    if xdpyinfo -display :99 >/dev/null 2>&1; then
        echo "[start.sh] Xvfb is up on display :99 (ready after ${i} check(s))"
        READY=1
        break
    fi
    sleep 0.5
done

if [ "$READY" -ne 1 ]; then
    echo "[start.sh] WARNING: Xvfb did NOT become ready within 10s. Headful" \
         "Chromium launches (e.g. /check-pickup-availability) will likely" \
         "fail with 'Missing X server or \$DISPLAY'. Continuing anyway —" \
         "every other endpoint here runs headless and doesn't need a display."
fi

exec python main.py
