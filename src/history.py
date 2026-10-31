"""Browser history reader — locate, copy, query Chrome-family SQLite history."""

from __future__ import annotations

import platform
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class HistoryNotFoundError(Exception):
    """No compatible browser history file was found."""


class HistoryLockedError(Exception):
    """Could not copy the history file (browser may be locking it)."""


class HistoryEmptyError(Exception):
    """History file exists but contains no rows in the requested window."""


# ---------------------------------------------------------------------------
# Browser path detection
# ---------------------------------------------------------------------------

HISTORY_PATHS: dict[str, list[str]] = {
    "darwin": [
        "~/Library/Application Support/Google/Chrome/Default/History",
        "~/Library/Application Support/Chromium/Default/History",
        "~/Library/Application Support/BraveSoftware/Brave-Browser/Default/History",
        "~/Library/Application Support/Microsoft Edge/Default/History",
    ],
    "linux": [
        "~/.config/google-chrome/Default/History",
        "~/.config/chromium/Default/History",
        "~/.config/BraveSoftware/Brave-Browser/Default/History",
        "~/.config/microsoft-edge/Default/History",
    ],
}

# Chrome epoch: microseconds since 1601-01-01
_CHROME_EPOCH_OFFSET = 11644473600000000


def _chrome_ts_to_datetime(chrome_us: int) -> datetime:
    """Convert Chrome's microsecond timestamp to a Python datetime (UTC)."""
    unix_us = chrome_us - _CHROME_EPOCH_OFFSET
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=unix_us)


def _datetime_to_chrome_ts(dt: datetime) -> int:
    """Convert a Python datetime to Chrome's microsecond timestamp."""
    unix_us = int(dt.timestamp() * 1_000_000)
    return unix_us + _CHROME_EPOCH_OFFSET


def find_history_file() -> Path:
    """Try all known browser paths. Return the one with the most recent entry."""
    system = platform.system().lower()
    candidates = HISTORY_PATHS.get(system, [])
    found: list[tuple[Path, int]] = []

    for raw_path in candidates:
        p = Path(raw_path).expanduser()
        if p.exists():
            try:
                tmp = tempfile.mktemp(suffix=".db", prefix="bhh_probe_")
                shutil.copy2(str(p), tmp)
                conn = sqlite3.connect(tmp)
                cur = conn.execute("SELECT MAX(last_visit_time) FROM urls")
                row = cur.fetchone()
                conn.close()
                Path(tmp).unlink(missing_ok=True)
                if row and row[0]:
                    found.append((p, row[0]))
            except Exception:
                continue

    if not found:
        tried = "\n  ".join(
            str(Path(p).expanduser()) for p in candidates
        )
        raise HistoryNotFoundError(
            f"No compatible browser history found. Paths tried:\n  {tried}"
        )

    # Return the path with the most recent entry
    found.sort(key=lambda x: x[1], reverse=True)
    return found[0][0]


# ---------------------------------------------------------------------------
# History extraction
# ---------------------------------------------------------------------------

def read_history(days: int = 7) -> pd.DataFrame:
    """Copy history to temp, query SQLite, return raw DataFrame."""
    src = find_history_file()
    tmp = tempfile.mktemp(suffix=".db", prefix="bhh_history_")
    try:
        try:
            shutil.copy2(str(src), tmp)
        except OSError as exc:
            raise HistoryLockedError(
                "Could not copy history file. Try closing your browser and retry."
            ) from exc

        cutoff = _datetime_to_chrome_ts(
            datetime.now(timezone.utc) - timedelta(days=days)
        )

        conn = sqlite3.connect(tmp)
        query = """
            SELECT u.url, u.title, u.visit_count, v.visit_time
            FROM urls u
            JOIN visits v ON u.id = v.url
            WHERE v.visit_time > ?
            ORDER BY v.visit_time ASC
        """
        df = pd.read_sql_query(query, conn, params=(cutoff,))
        conn.close()

        if df.empty:
            raise HistoryEmptyError(
                f"No browsing history found in the last {days} days."
            )

        # Convert Chrome timestamps to Python datetime
        # Use epoch-seconds approach to avoid pd.to_datetime compat issues
        epoch_seconds = (df["visit_time"] - _CHROME_EPOCH_OFFSET) / 1_000_000.0
        df = df.assign(
            visit_time=[
                datetime.fromtimestamp(s, tz=timezone.utc)
                for s in epoch_seconds
            ]
        )
        return df

    finally:
        Path(tmp).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

CATEGORIES: dict[str, list[str]] = {
    "work": [
        "github", "gitlab", "jira", "confluence", "notion", "linear",
        "slack", "zoom", "meet.google", "docs.google",
    ],
    "social": [
        "twitter", "x.com", "reddit", "linkedin", "facebook",
        "instagram", "tiktok", "threads",
    ],
    "entertainment": ["youtube", "twitch", "netflix", "spotify", "hulu"],
    "shopping": ["amazon", "ebay", "etsy", "shopify"],
    "reference": [
        "stackoverflow", "wikipedia", "mdn", "docs.", "man7",
        "arxiv", "medium", "dev.to",
    ],
}

_CATEGORY_MAP: dict[str, int] = {
    "work": 0, "social": 1, "entertainment": 2,
    "shopping": 3, "reference": 4, "other": 5,
}


def _categorize_domain(domain: str) -> int:
    """Return integer topic category for a domain."""
    domain_lower = domain.lower()
    for cat, keywords in CATEGORIES.items():
        for kw in keywords:
            if kw in domain_lower:
                return _CATEGORY_MAP[cat]
    return _CATEGORY_MAP["other"]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Derive features used by TabPFN. Returns enriched DataFrame."""
    df = df.copy()

    # Domain extraction
    df["domain"] = df["url"].apply(
        lambda u: urlparse(u).netloc.removeprefix("www.")
    )

    # Time features
    df["hour"] = df["visit_time"].dt.hour
    df["day_of_week"] = df["visit_time"].dt.dayofweek
    df["is_after_midnight"] = df["hour"].isin([0, 1, 2, 3]).astype(int)

    # Topic category
    df["topic_category"] = df["domain"].apply(_categorize_domain)

    # Time since last visit (any domain)
    df["time_since_last_visit_min"] = (
        df["visit_time"].diff().dt.total_seconds().fillna(9999) / 60.0
    )

    # Visits to same domain within ±30min window (vectorized approach)
    df["_ts_epoch"] = df["visit_time"].apply(lambda t: t.timestamp())
    domain_session_counts: list[int] = []
    grouped = df.groupby("domain")["_ts_epoch"].apply(list).to_dict()
    for _, row in df.iterrows():
        ts = row["_ts_epoch"]
        domain_ts = grouped[row["domain"]]
        count = sum(1 for t in domain_ts if abs(t - ts) <= 1800)
        domain_session_counts.append(count)
    df["domain_visits_in_session"] = domain_session_counts
    df.drop(columns=["_ts_epoch"], inplace=True)

    # Cumulative visit count (from Chrome)
    df["visit_count_cumulative"] = df["visit_count"]

    # Heuristic seed labels for TabPFN
    df["heuristic_haunted"] = (
        (df["is_after_midnight"] == 1)
        | (df["domain_visits_in_session"] >= 5)
        | ((df["time_since_last_visit_min"] < 0.5) & (df["domain_visits_in_session"] >= 3))
        | ((df["topic_category"] == 5) & (df["hour"] >= 22))
    ).astype(int)

    # Drop raw URL (privacy) — keep domain
    df.drop(columns=["url"], inplace=True)

    return df
