"""FastAPI server — SSE streaming, audio serving, and static files."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from .tracer import init_sentry, trace_stage

logger = logging.getLogger(__name__)

app = FastAPI(title="Browser History Horror", version="1.0.0")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

# In-memory session store: session_id -> session data
_sessions: dict[str, dict] = {}

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    init_sentry()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the frontend."""
    html_path = FRONTEND_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="Frontend not found")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    """Health check — verify Ollama and history availability."""
    from .narrator import check_ollama
    from .history import find_history_file, HistoryNotFoundError

    ollama_ok = await check_ollama()
    history_found = True
    try:
        find_history_file()
    except HistoryNotFoundError:
        history_found = False

    return {
        "status": "ok" if (ollama_ok and history_found) else "degraded",
        "ollama": ollama_ok,
        "history_found": history_found,
    }


@app.post("/seance")
async def start_seance():
    """Start the séance pipeline. Returns a session_id for SSE streaming."""
    session_id = uuid.uuid4().hex[:12]
    _sessions[session_id] = {
        "status": "pending",
        "chapters": [],
        "chapter_texts": {},
        "error": None,
    }
    return {"session_id": session_id}


@app.get("/seance/{session_id}/stream")
async def stream_seance(session_id: str, request: Request):
    """SSE stream of the full pipeline: extract → detect → narrate."""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session_data = _sessions[session_id]

    async def event_generator():
        try:
            # ----- Stage 1: History extraction -----
            yield _sse("progress", {"stage": "summoning", "pct": 0})

            with trace_stage("history.extract") as span:
                from .history import (
                    read_history,
                    build_features,
                    HistoryNotFoundError,
                    HistoryLockedError,
                    HistoryEmptyError,
                )

                try:
                    raw = read_history()
                except (HistoryNotFoundError, HistoryLockedError, HistoryEmptyError) as exc:
                    yield _sse("error", {"message": str(exc), "stage": "summoning"})
                    return

                if span:
                    span.set_data("rows_extracted", len(raw))

            yield _sse("progress", {"stage": "summoning", "pct": 30})

            with trace_stage("history.feature_engineer") as span:
                df = build_features(raw)
                if span:
                    span.set_data("rows_output", len(df))
                    span.set_data(
                        "haunted_seed_count", int(df["heuristic_haunted"].sum())
                    )

            yield _sse("progress", {"stage": "summoning", "pct": 50})

            # ----- Stage 2: Anomaly detection -----
            yield _sse("progress", {"stage": "detecting", "pct": 50})

            with trace_stage("tabpfn.score") as span:
                from .detector import score_sessions, extract_haunted_sessions

                df = score_sessions(df)
                if span:
                    span.set_data("rows_scored", len(df))

            yield _sse("progress", {"stage": "detecting", "pct": 70})

            with trace_stage("tabpfn.session_extract") as span:
                sessions = extract_haunted_sessions(df)
                if span:
                    span.set_data("sessions_found", len(sessions))

            if not sessions:
                yield _sse(
                    "error",
                    {
                        "message": "The spirits found nothing to haunt. "
                        "Your browsing is eerily clean.",
                        "stage": "detecting",
                    },
                )
                return

            yield _sse("progress", {"stage": "detecting", "pct": 90})

            # ----- Stage 3: Narration -----
            yield _sse("progress", {"stage": "narrating", "pct": 90})

            from .narrator import narrate_chapter, generate_chapter_title, sanitize_narration
            from .tts import generate_audio

            for idx, session in enumerate(sessions):
                # Check if client disconnected
                if await request.is_disconnected():
                    return

                # Generate title
                with trace_stage(f"gemma.title.{idx}", model="gemma3:4b"):
                    title = await generate_chapter_title(session, idx + 1)
                    session.chapter_title = title

                yield _sse(
                    "chapter",
                    {
                        "id": idx,
                        "day": session.day,
                        "title": title,
                        "text_chunk": "",
                        "anomaly_score": round(session.anomaly_score, 2),
                        "domains": session.domains,
                        "hour_range": session.hour_range,
                        "intensity": session.intensity_label,
                    },
                )

                # Stream narration
                full_text = ""
                with trace_stage(
                    f"gemma.chapter.{idx}",
                    model="gemma3:4b",
                    day=session.day,
                ):
                    async for chunk in narrate_chapter(session, idx + 1):
                        full_text += chunk
                        yield _sse(
                            "chapter_text",
                            {"id": idx, "text_chunk": chunk},
                        )
                        await asyncio.sleep(0)  # yield control

                # Sanitize and store
                full_text = sanitize_narration(full_text, session)
                session_data["chapter_texts"][idx] = full_text

                # TTS (async, non-blocking for the stream)
                with trace_stage(
                    f"elevenlabs.tts.{idx}",
                    chapter_id=idx,
                    text_chars=len(full_text),
                ):
                    audio = await generate_audio(session_id, idx, full_text)
                    if audio:
                        yield _sse("chapter_audio_ready", {"chapter_id": idx})

                session_data["chapters"].append(
                    {
                        "title": title,
                        "day": session.day,
                        "text": full_text,
                        "anomaly_score": session.anomaly_score,
                    }
                )

            # ----- Done -----
            session_data["status"] = "complete"
            yield _sse(
                "done",
                {
                    "chapters": len(sessions),
                    "grimoire_url": f"/grimoire/{session_id}",
                },
            )

        except Exception as exc:
            logger.exception("Pipeline failed")
            yield _sse("error", {"message": str(exc), "stage": "unknown"})

    return EventSourceResponse(event_generator())


@app.get("/audio/{session_id}/{chapter_id}")
async def get_audio(session_id: str, chapter_id: int):
    """Serve cached TTS audio for a chapter."""
    from .tts import get_cached_audio

    audio = get_cached_audio(session_id, chapter_id)
    if audio is None:
        raise HTTPException(status_code=404, detail="Audio not ready")
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/grimoire/{session_id}", response_class=HTMLResponse)
async def get_grimoire(session_id: str):
    """Generate a self-contained HTML grimoire for download."""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session_data = _sessions[session_id]
    chapters = session_data.get("chapters", [])
    if not chapters:
        raise HTTPException(status_code=404, detail="No chapters generated yet")

    chapters_html = ""
    for ch in chapters:
        chapters_html += f"""
        <div class="chapter">
            <h2>{ch['title']}</h2>
            <p class="meta">{ch['day']}</p>
            <div class="text">{_nl2br(ch['text'])}</div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>My Haunted History</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Cinzel+Decorative:wght@400;700&family=IM+Fell+English&display=swap');
  body {{ background: #0a0a0f; color: #e8d5b0; font-family: 'IM Fell English', Georgia, serif;
         max-width: 720px; margin: 0 auto; padding: 2rem; line-height: 1.9; }}
  h1 {{ font-family: 'Cinzel Decorative', serif; color: #c17f3a; text-align: center;
       letter-spacing: 0.15em; margin-bottom: 0.5em; }}
  h2 {{ font-family: 'Cinzel Decorative', serif; color: #c17f3a; font-size: 1.3rem;
       letter-spacing: 0.1em; }}
  .meta {{ color: #9a8a6a; font-size: 0.85rem; letter-spacing: 0.05em; }}
  .chapter {{ border: 1px solid #2a2a3a; padding: 1.5rem; margin: 2rem 0;
             background: #12121a; border-radius: 4px; }}
  .text {{ font-size: 1.05rem; }}
  footer {{ text-align: center; color: #9a8a6a; font-size: 0.8rem; margin-top: 3rem;
           border-top: 1px solid #2a2a3a; padding-top: 1rem; }}
</style>
</head>
<body>
  <h1>The Séance of Browsing Past</h1>
  <p style="text-align:center;color:#9a8a6a;">A gothic reckoning with your recent tabs</p>
  {chapters_html}
  <footer>Generated by Browser History Horror · github.com/browser-history-horror</footer>
</body>
</html>"""

    return HTMLResponse(
        content=html,
        headers={
            "Content-Disposition": f'attachment; filename="my_haunted_history.html"'
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> dict:
    """Format an SSE message for sse-starlette."""
    return {"event": event, "data": json.dumps(data)}


def _nl2br(text: str) -> str:
    """Convert newlines to <br> for HTML rendering."""
    import html as html_mod
    return html_mod.escape(text).replace("\n", "<br>")
