# METRIQA Data Exporter (read-only)

This component exports analytical snapshots from the existing METRIQA production SQLite databases to the private GitHub repository. It is intentionally isolated from the production bot.

## Safety invariants

- SQLite connections use `mode=ro` and `PRAGMA query_only=ON`.
- No imports from `radar/`; no calls to external market APIs; no production bot callbacks.
- No writes to source databases, bot cache, production code, or systemd units.
- Writes are limited to `market_data/` inside a separate Git checkout.
- Pushes only to `data/hourly-market-data`; never to `main`.
- Explicit table allow-list. Raw API responses, document OCR text, credentials, tokens, subscriber data and logs are excluded.
- A lock prevents overlapping runs; files are replaced atomically; unchanged snapshots do not create commits.

## Data layout

- `market_data/radar/*.csv`: instruments, issuers, recent OHLCV, snapshots, order book, investor structure, dividends, facts and financial statements.
- `market_data/signals/*.csv`: current signals, archive and high-yield catalyst shadow records.
- `market_data/market/market_snapshot.json`: sanitized market cache (allow-listed fields only).
- `market_data/manifest.json`: UTC generation time, source status, row counts, file sizes and SHA-256 hashes.

OHLCV is a rolling 90-day export and OTC trades are rolling 30-day. Historical bootstrap should be handled as a separate, explicitly reviewed export to avoid pushing a large historical database on every hourly run.

## Deployment topology

```text
METRIQA production bot + SQLite DBs (unchanged)
             |
             | SQLite mode=ro / query_only
             v
Independent exporter (hourly systemd timer)
             |
             | writes only market_data/ in isolated checkout
             v
Private GitHub branch: data/hourly-market-data
```

## Configuration

Use a separate clone of this repository at `/home/ubuntu/metriqa-export-repo`, checked out on `data/hourly-market-data`. Do not use the production bot directory as the Git working copy.

The database filenames and source directories below retain their existing server-side names for compatibility. This does not mean the current product or bot is named JETT.\n\nEnvironment variables (defaults shown):
- `METRIQA_RADAR_DB=/home/ubuntu/jett_data/jett_radar.db`
- `METRIQA_SIGNALS_DB=/home/ubuntu/bot/jett_signals.db`
- `METRIQA_MARKET_CACHE=/home/ubuntu/bot/market_cache.json`
- `METRIQA_EXPORT_REPO=/home/ubuntu/metriqa-export-repo`
- `METRIQA_EXPORT_BRANCH=data/hourly-market-data`
- `METRIQA_OHLCV_LOOKBACK_DAYS=90`
- `METRIQA_OTC_LOOKBACK_DAYS=30`

Git authentication must be configured on the VPS using a dedicated, least-privilege deploy key or machine identity with write access to this private repository. Keep the private key outside the repository with restrictive permissions. Never put a token in this file, command line, or Git remote URL.

## Hourly scheduling

The timer template in `deploy/systemd/` is a proposal only. It is not installed or enabled by this repository change. Deploy it only after reviewing the database paths, Git identity, SSH authentication, and a manual dry-run.

## First-run checklist

1. Confirm database paths and file permissions with read-only shell commands.
2. Clone the private repository into the isolated export checkout.
3. Create/check out the dedicated data branch.
4. Configure Git SSH authentication without exposing secrets.
5. Run `python3 exporter.py` manually and inspect the generated CSV/JSON and manifest.
6. Confirm `main`, production source files, SQLite DBs and systemd services remain unchanged.
7. Only then install and enable the hourly timer as a separately approved operational step.
