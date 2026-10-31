"""Gemma narrator — generate gothic horror chapters via Ollama."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator

import httpx

from .detector import HauntedSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
PRIMARY_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
TEMPERATURE = 0.85
TOP_P = 0.92
MAX_TOKENS = 500
TIMEOUT_SECONDS = 120

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are The Archivist — a gothic narrator who observes and records the browsing habits of \
the living. You write in second person ("the developer", "they") with the dry, melancholic \
gravity of Victorian horror literature.

Rules you must follow:
1. Never reveal the user's actual search queries verbatim — paraphrase intentions only.
2. Never use full URLs. Refer to sites by their character: "the orange forum", "the eternal \
repository of unanswered questions", "the video labyrinth."
3. Each chapter should have exactly one darkly comic observation — the moment that would \
make the reader laugh and wince simultaneously.
4. End each chapter with a single ominous sentence that foreshadows the next day.
5. Write 200–350 words. Do not exceed this.
6. Do not use chapter headers — those are added by the UI.
7. Never break the gothic frame. You are recording, not advising."""

# ---------------------------------------------------------------------------
# Domain aliases
# ---------------------------------------------------------------------------

DOMAIN_ALIASES: dict[str, str] = {
    "stackoverflow.com": "the eternal repository of unanswered questions",
    "reddit.com": "the orange forum",
    "youtube.com": "the video labyrinth",
    "github.com": "the great repository",
    "twitter.com": "the cacophony",
    "x.com": "the cacophony",
    "linkedin.com": "the professional purgatory",
    "amazon.com": "the infinite marketplace",
    "wikipedia.org": "the encyclopedia of everything and nothing",
    "medium.com": "the blog of blogs",
    "notion.so": "the endless document",
    "chatgpt.com": "the other oracle",
    "google.com": "the all-seeing oracle",
    "docs.google.com": "the collaborative parchment",
}


def _domain_alias(domain: str) -> str:
    """Return gothic alias for a domain, or a generic description."""
    domain_lower = domain.lower()
    for key, alias in DOMAIN_ALIASES.items():
        if key in domain_lower:
            return alias
    return "an unfamiliar domain"


def _categorize(domain: str) -> str:
    """Return human-readable category for context snippet."""
    from .history import CATEGORIES
    domain_lower = domain.lower()
    for cat, keywords in CATEGORIES.items():
        for kw in keywords:
            if kw in domain_lower:
                return cat
    return "unknown"


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _build_context_snippet(session: HauntedSession) -> str:
    """Build concise summary without exposing raw URLs."""
    lines: list[str] = []
    domain_counts = session.raw_rows.groupby("domain").size()
    for domain, count in domain_counts.nlargest(4).items():
        category = _categorize(domain)
        lines.append(f"- {count} visits to a {category} site")

    if session.raw_rows["is_after_midnight"].any():
        lines.append("- session crossed midnight")

    rapid = session.raw_rows[session.raw_rows["domain_visits_in_session"] >= 4]
    if not rapid.empty:
        lines.append(
            f"- returned to the same site {int(rapid['domain_visits_in_session'].max())} "
            "times within 30 minutes"
        )

    return "\n".join(lines) if lines else "- a quiet but unsettling session"


def build_prompt(session: HauntedSession, chapter_number: int) -> str:
    """Build the user prompt for Gemma."""
    domains_desc = ", ".join(session.domains[:5])
    context = _build_context_snippet(session)
    return (
        f"Write Chapter {chapter_number} of a gothic horror account of someone's browsing sins.\n\n"
        f"Context (DO NOT repeat this as-is — weave it into prose):\n"
        f"- Day: {session.day}\n"
        f"- Time span: {session.hour_range}\n"
        f"- Sites visited (in order of obsession): {domains_desc}\n"
        f"- Total visits: {session.visit_count}\n"
        f"- Haunting intensity: {session.intensity_label} ({session.anomaly_score:.2f})\n"
        f"{context}\n\n"
        f"Begin the narration immediately (no preamble, no repeating the context above). "
        f"200-350 words, second person (\"the developer\", \"they\"), "
        f"one darkly comic moment, one ominous closing line."
    )


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------

def sanitize_narration(text: str, session: HauntedSession) -> str:
    """Strip leaked URLs and replace domains with gothic aliases."""
    text = re.sub(r"https?://\S+", "[a site]", text)
    for domain in session.domains:
        text = text.replace(domain, _domain_alias(domain))
    return text.strip()


# ---------------------------------------------------------------------------
# Ollama interaction
# ---------------------------------------------------------------------------

async def check_ollama() -> bool:
    """Check if Ollama is running and the model is available."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code != 200:
                return False
            models = resp.json().get("models", [])
            return any(m["name"].startswith(PRIMARY_MODEL.split(":")[0]) for m in models)
    except Exception:
        return False


async def generate_chapter_title(
    session: HauntedSession, chapter_number: int
) -> str:
    """Generate a short gothic chapter title."""
    prompt = (
        f"Generate a gothic chapter title for Chapter {chapter_number} of a horror "
        f"story about someone's browsing habits.\n"
        f"Day: {session.day}. Sites: {', '.join(session.domains[:3])}. "
        f"Intensity: {session.intensity_label}.\n"
        f'Format: "Chapter {chapter_number} — [4-6 word gothic title]"\n'
        f'Examples: "Chapter II — The Tuesday Reckoning", '
        f'"Chapter IV — What The Algorithm Wanted"\n'
        f"Return only the title, nothing else."
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": PRIMARY_MODEL,
                    "prompt": prompt,
                    "system": "You generate short gothic chapter titles. Return only the title.",
                    "stream": False,
                    "options": {"temperature": 0.7, "num_predict": 40},
                },
            )
            result = resp.json().get("response", "").strip().strip('"')
            if result:
                return result
    except Exception as exc:
        logger.warning("Title generation failed: %s", exc)

    # Fallback
    return f"Chapter {_roman(chapter_number)} — The {session.day.split(',')[0]} Reckoning"


def _roman(n: int) -> str:
    """Convert int to Roman numeral."""
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
            (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
            (5, "V"), (4, "IV"), (1, "I")]
    result = ""
    for val, numeral in vals:
        while n >= val:
            result += numeral
            n -= val
    return result


async def narrate_chapter(
    session: HauntedSession,
    chapter_number: int,
) -> AsyncIterator[str]:
    """Stream gothic narration for one chapter from Ollama."""
    payload = {
        "model": PRIMARY_MODEL,
        "prompt": build_prompt(session, chapter_number),
        "system": SYSTEM_PROMPT,
        "stream": True,
        "options": {
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "num_predict": MAX_TOKENS,
        },
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS)) as client:
        async with client.stream(
            "POST", f"{OLLAMA_BASE_URL}/api/generate", json=payload
        ) as resp:
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("response"):
                    yield chunk["response"]
                if chunk.get("done"):
                    return
