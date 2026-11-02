#!/usr/bin/env python3
"""
METRIQA read-only data exporter.
- Reads SQLite databases strictly with mode=ro.
- Writes only to a separate Git working copy's market_data/ directory.
- Never imports or calls the production bot.
- Pushes to the dedicated data/hourly-market-data branch.
"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import sqlite3

TZ_NAME = "Asia/Tashkent"
RADAR_DB = Path(os.environ.get("METRIQA_RADAR_DB", "/home/ubuntu/jett_data/jett_radar.db"))
SIGNALS_DB = Path(os.environ.get("METRIQA_SIGNALS_DB", "/home/ubuntu/bot/jett_signals.db"))
CACHE_FILE = Path(os.environ.get("METRIQA_MARKET_CACHE", "/home/ubuntu/bot/market_cache.json"))
REPO_DIR = Path(os.environ.get("METRIQA_EXPORT_REPO", "/home/ubuntu/metriqa-export-repo"))
DATA_DIR = REPO_DIR / "market_data"
LOCK_FILE = Path(os.environ.get("METRIQA_EXPORT_LOCK", "/home/ubuntu/.metriqa-export.lock"))
BRANCH = os.environ.get("METRIQA_EXPORT_BRANCH", "data/hourly-market-data")
LOOKBACK_DAYS = int(os.environ.get("METRIQA_OHLCV_LOOKBACK_DAYS", "90"))
OTC_LOOKBACK_DAYS = int(os.environ.get("METRIQA_OTC_LOOKBACK_DAYS", "30"))

# Explicit allow-list. Sensitive/raw payload tables are intentionally excluded.
RADAR_TABLES = {
    "instruments": None,
    "issuers": None,
    "ohlcv": ("candle_time >= datetime('now', ?)", (f"-{LOOKBACK_DAYS} days",)),
    "market_snapshots": ("observed_at >= datetime('now', '-7 days')", ()),
    "orderbook_snapshots": ("observed_at >= datetime('now', '-7 days')", ()),
    "investor_structure": None,
    "dividends": None,
    "facts": None,
    "financial_indicators": None,
    "balance_sheet": None,
    "financial_results": None,
    "otc_trades": ("trade_date >= date('now', ?)", (f"-{OTC_LOOKBACK_DAYS} days",)),
    "uzse_mapping": None,
}
SIGNAL_TABLES = {
    "ai_signals_history": None,
    "ai_signals_archive": None,
    "high_yield_catalyst_shadow": None,
}
CACHE_FIELDS = {
    "status", "total_stocks", "total_bonds", "total_turnover", "is_stale",
    "active_stocks", "active_bonds", "active_all", "timestamp",
}
INSTRUMENT_FIELDS = {
    "ticker", "close_price", "gross_trade_amount", "trade_amount",
    "volume_today", "volume", "growth_prcnt_today", "percent_change",
    "is_active", "market_type",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def export_table(conn: sqlite3.Connection, table: str, target: Path, filter_spec):
    if not table_exists(conn, table):
        return {"status": "missing_table", "rows": 0}
    columns = [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    if not columns:
        return {"status": "empty_schema", "rows": 0}
    sql = f'SELECT * FROM "{table}"'
    params = ()
    if filter_spec:
        where, params = filter_spec
        sql += " WHERE " + where
    sql += " ORDER BY rowid"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            cur = conn.execute(sql, params)
            while True:
                batch = cur.fetchmany(5000)
                if not batch:
                    break
                for row in batch:
                    writer.writerow({k: row[k] for k in columns})
                    count += 1
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return {"status": "ok", "rows": count, "file": str(target.relative_to(REPO_DIR))}


def export_market_cache(target: Path):
    if not CACHE_FILE.is_file():
        return {"status": "missing_file", "rows": 0}
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        clean = {k: raw[k] for k in CACHE_FIELDS if k in raw}
        for key in ("active_stocks", "active_bonds", "active_all"):
            if isinstance(clean.get(key), list):
                clean[key] = [
                    {k: item[k] for k in INSTRUMENT_FIELDS if k in item}
                    for item in clean[key] if isinstance(item, dict)
                ]
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, target)
        return {"status": "ok", "rows": sum(len(clean.get(k, [])) for k in ("active_stocks", "active_bonds", "active_all"))}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300], "rows": 0}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str):
    return subprocess.run(
        ["git", *args], cwd=REPO_DIR, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=180,
    )


def main():
    if not REPO_DIR.is_dir() or not (REPO_DIR / ".git").exists():
        raise RuntimeError(f"Exporter Git checkout missing: {REPO_DIR}")
    if not (REPO_DIR / ".gitignore").exists():
        raise RuntimeError("Unexpected checkout: .gitignore missing")
    lock = LOCK_FILE.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another METRIQA export is running; exiting.")
        return 0

    started = now_iso()
    manifest = {
        "product": "METRIQA Data Exporter",
        "schema_version": 1,
        "generated_at_utc": started,
        "timezone": TZ_NAME,
        "source_mode": "SQLite read-only (mode=ro; query_only=ON)",
        "lookback_days": {"ohlcv": LOOKBACK_DAYS, "otc_trades": OTC_LOOKBACK_DAYS},
        "sources": {},
        "files": {},
        "notes": [
            "This is an analytical data snapshot, not a live trading feed.",
            "OHLCV and OTC exports are rolling windows; older history requires a separately approved bootstrap.",
            "Raw API payloads, tokens, credentials, user/subscriber data and runtime logs are excluded.",
        ],
    }

    try:
        with connect_readonly(RADAR_DB) as conn:
            for table, filt in RADAR_TABLES.items():
                result = export_table(conn, table, DATA_DIR / "radar" / f"{table}.csv", filt)
                manifest["sources"][f"radar.{table}"] = result
        with connect_readonly(SIGNALS_DB) as conn:
            for table, filt in SIGNAL_TABLES.items():
                result = export_table(conn, table, DATA_DIR / "signals" / f"{table}.csv", filt)
                manifest["sources"][f"signals.{table}"] = result

        manifest["sources"]["market_cache"] = export_market_cache(DATA_DIR / "market" / "market_snapshot.json")

        for path in sorted(DATA_DIR.rglob("*")):
            if path.is_file() and path.name != "manifest.json" and not path.name.endswith(".tmp"):
                manifest["files"][str(path.relative_to(REPO_DIR))] = {
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }

        manifest_path = DATA_DIR / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, manifest_path)

        # Refuse to operate on a branch other than the dedicated data branch.
        current = git("branch", "--show-current").stdout.strip()
        if current != BRANCH:
            raise RuntimeError(f"Wrong Git branch: {current!r}; expected {BRANCH!r}")
        git("add", "market_data")
        changed = git("diff", "--cached", "--quiet")
        # git diff --quiet exits 1 when changes exist; subprocess check=True raises.
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 1 and exc.cmd[1:3] == ["diff", "--cached"]:
            pass
        else:
            raise RuntimeError((exc.stderr or str(exc))[:1000]) from exc

    # Commit only if staged changes exist; no empty commits.
    status = git("status", "--porcelain", "--", "market_data").stdout.strip()
    if not status:
        print("No data changes; no commit created.")
        return 0
    git("config", "user.name", "METRIQA Data Exporter")
    git("config", "user.email", "metriqa-exporter@users.noreply.github.com")
    git("commit", "-m", "data: hourly read-only market snapshot")
    git("push", "origin", BRANCH)
    print(f"Export completed and pushed at {started}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"EXPORT FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
