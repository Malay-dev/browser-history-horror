"""TabPFN anomaly detector — score browsing rows and extract haunted sessions."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class HauntedSession:
    day: str                          # "Monday, October 21"
    chapter_title: str = ""           # filled by narrator
    hour_range: str = ""              # "23:00–01:30"
    domains: list[str] = field(default_factory=list)
    visit_count: int = 0
    anomaly_score: float = 0.0
    intensity_label: str = "LOW"
    raw_rows: pd.DataFrame = field(default_factory=pd.DataFrame)


# ---------------------------------------------------------------------------
# Feature columns used by TabPFN
# ---------------------------------------------------------------------------

FEATURES = [
    "hour",
    "day_of_week",
    "is_after_midnight",
    "domain_visits_in_session",
    "time_since_last_visit_min",
    "topic_category",
    "visit_count_cumulative",
]
LABEL = "heuristic_haunted"


def _intensity(score: float) -> str:
    if score >= 0.90:
        return "MALEVOLENT"
    if score >= 0.75:
        return "HIGH"
    if score >= 0.50:
        return "MODERATE"
    return "LOW"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_sessions(df: pd.DataFrame) -> pd.DataFrame:
    """Add anomaly_score column (0.0–1.0) via TabPFN or heuristic fallback."""
    X = df[FEATURES].fillna(0).astype(float)
    y = df[LABEL]

    # Guard: if all labels are the same, TabPFN can't learn anything
    if y.nunique() < 2:
        logger.warning("All heuristic labels identical — using raw heuristic as score.")
        df["anomaly_score"] = y.astype(float)
        return df

    try:
        from tabpfn import TabPFNClassifier

        clf = TabPFNClassifier(device="cpu", n_estimators=8)
        clf.fit(X, y)
        probas = clf.predict_proba(X)
        df["anomaly_score"] = probas[:, 1]  # P(haunted)
        logger.info("TabPFN scoring complete — %d rows scored.", len(df))
    except Exception as exc:
        logger.warning(
            "TabPFN unavailable (%s) — using heuristic scoring only.", exc
        )
        df["anomaly_score"] = y.astype(float)

    return df


# ---------------------------------------------------------------------------
# Session extraction
# ---------------------------------------------------------------------------

def extract_haunted_sessions(
    df: pd.DataFrame,
    threshold: float = 0.65,
    max_sessions: int = 10,
) -> list[HauntedSession]:
    """Group high-scoring rows into sessions, merge by calendar day for chapters."""
    if "anomaly_score" not in df.columns:
        df = score_sessions(df)

    haunted = df[df["anomaly_score"] > threshold].copy()
    if haunted.empty:
        # Lower threshold to get at least something
        threshold = 0.3
        haunted = df[df["anomaly_score"] > threshold].copy()
    if haunted.empty:
        # Use top 20% if still nothing
        cutoff = df["anomaly_score"].quantile(0.80)
        haunted = df[df["anomaly_score"] > cutoff].copy()

    haunted = haunted.sort_values("visit_time")

    # Group rows within 45-minute windows
    sessions: list[list] = []
    current: list = []
    for _, row in haunted.iterrows():
        if current:
            gap = (row["visit_time"] - current[-1]["visit_time"]).total_seconds()
            if gap > 2700:  # 45 min
                sessions.append(current)
                current = []
        current.append(row.to_dict())
    if current:
        sessions.append(current)

    # Build HauntedSession objects
    result: list[HauntedSession] = []
    for session_rows in sessions:
        sdf = pd.DataFrame(session_rows)
        domain_counts = sdf.groupby("domain").size().sort_values(ascending=False)
        avg_score = float(sdf["anomaly_score"].mean())
        result.append(
            HauntedSession(
                day=sdf.iloc[0]["visit_time"].strftime("%A, %B %d"),
                hour_range=(
                    f"{sdf['visit_time'].min():%H:%M}–{sdf['visit_time'].max():%H:%M}"
                ),
                domains=domain_counts.index.tolist()[:5],
                visit_count=len(sdf),
                anomaly_score=avg_score,
                intensity_label=_intensity(avg_score),
                raw_rows=sdf,
            )
        )

    result.sort(key=lambda s: s.anomaly_score, reverse=True)

    # Merge sessions from the same calendar day
    day_map: dict[str, HauntedSession] = {}
    for s in result[:max_sessions]:
        if s.day in day_map:
            existing = day_map[s.day]
            existing.domains = list(dict.fromkeys(existing.domains + s.domains))[:5]
            existing.visit_count += s.visit_count
            existing.anomaly_score = max(existing.anomaly_score, s.anomaly_score)
            existing.intensity_label = _intensity(existing.anomaly_score)
            existing.raw_rows = pd.concat([existing.raw_rows, s.raw_rows])
            # Expand hour range
            all_times = existing.raw_rows["visit_time"]
            existing.hour_range = f"{all_times.min():%H:%M}–{all_times.max():%H:%M}"
        else:
            day_map[s.day] = s

    merged = list(day_map.values())
    merged.sort(
        key=lambda s: s.raw_rows["visit_time"].min()
    )
    return merged
