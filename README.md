# METRIQA Platform

METRIQA is a financial market intelligence platform focused on Uzbekistan's capital markets.

## Components

### METRIQA RADAR
Telegram-based market analysis, quantitative scoring, AI-assisted analysis and signal tracking.

### Watchdog
Independent monitoring and diagnostic tools for the RADAR service.

## Repository Structure

- `radar/` — RADAR source code
- `watchdog/` — monitoring and audit source code
- `requirements-radar.txt` — RADAR Python dependencies
- `requirements-watchdog.txt` — Watchdog Python dependencies

## Configuration

Runtime secrets must be supplied through environment variables.

Required RADAR variables:
- `TG_BOT_TOKEN`
- `GEMINI_API_KEY`

Never commit `.env`, tokens, API keys, database files or runtime logs.

## Important

This repository is a source-code backup and development repository.

It is NOT an automated production deployment configuration.

Existing production services, databases and server settings must be managed separately.

The primary Google AI integration uses `google.genai`.

Playwright browser binaries may require separate installation.
