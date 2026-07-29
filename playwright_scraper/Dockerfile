# Playwright's official image ships Chromium plus every system library it
# needs already installed — same reasoning as whatsapp_forwarder/Dockerfile:
# Railway's default nixpacks Python builder does not, and fighting Playwright
# onto it isn't worth it. Deploy this service with builder = "Dockerfile"
# (root directory playwright_scraper/), same as whatsapp_forwarder.
#
# Version pin history: this sat at v1.47.0 (Chromium 129, released ~Sept
# 2024) unchanged since this service was first built, while
# checkers/apple.py's own User-Agent for Apple's fulfillment-messages
# endpoint had drifted to claiming a MUCH newer browser (Chrome/Edge 150)
# from a real capture — a growing, detectable engine-vs-claimed-identity
# mismatch (TLS/JA3 fingerprint, HTTP/2 behavior, and JS feature surface
# all still reflect the real frozen Chromium 129 engine no matter what the
# User-Agent header claims). Bumped to v1.60.0 (Chromium ~131) as part of
# investigating Apple's fulfillment-messages 541s; see
# playwright_scraper/main.py's _apple_user_agent_for, which now DERIVES
# the claimed Chrome/Edge version from this image's actual bundled
# browser at runtime instead of a hardcoded string — so this pin can be
# bumped again in the future without also having to remember to update a
# separate hardcoded version number to match.
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

WORKDIR /app

# xvfb (2026-07-28, revised): _check_pickup_availability (see main.py's
# APPLE_PICKUP_HEADLESS) needs headless=False — a controlled same-network
# A/B test confirmed headless Chromium fails to render Apple's
# pickUpDetails widget at all, while an otherwise-identical headful run
# succeeds consistently. Railway's container has no physical display,
# so a real (non-headless) Chromium launch has nothing to render into
# without one.
#
# REVISED from an earlier xvfb-run-wrapped CMD: that approach deployed
# "successfully" but Chromium still failed with "Missing X server or
# $DISPLAY" the first time a headful launch actually happened — xvfb-run
# is a bit of a black box (depends on xauth, checks readiness via
# xdpyinfo internally) that was never actually verified working in this
# specific container; its failure is silent from this service's
# perspective, since it doesn't crash the wrapped process. start.sh
# (below) replaces it with an explicit Xvfb start + readiness poll +
# logging, so a future failure is visible in Railway's logs directly
# instead of surfacing only as a downstream browser-launch error.
# x11-utils provides xdpyinfo, used by start.sh's readiness check.
#
# Every OTHER endpoint in this service stays headless by default (see
# HEADLESS) and is unaffected either way — Xvfb is only needed for a
# headful launch, not a requirement for a headless one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb x11-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY start.sh .
RUN chmod +x start.sh

# No volume needed — this service is fully stateless (no login/session to
# persist between requests, unlike whatsapp_forwarder).
CMD ["./start.sh"]
