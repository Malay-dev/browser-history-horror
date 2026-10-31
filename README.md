# 🕯️ Browser History Horror

> *"On Tuesday at 2am, the developer returned to Stack Overflow for the seventh time.
> They searched for 'how to center a div.' They knew the answer. They just needed to hear it again."*

A fully local, privacy-first tool that reads your browser history, detects anomalous
browsing patterns, and narrates your week as a gothic horror story — complete with
creepy voice narration and a downloadable "grimoire" of your sins.

Your browsing history **never leaves your machine** during analysis. The only data that
touches an external service is the generated narration text sent to ElevenLabs for
text-to-speech (and that's optional).

Built for the [Hacktoberfest 2026](https://hacktoberfest.com) **Ghost in the Machine** challenge.

---

## How It Works

```
Browser SQLite ──► History Reader ──► Anomaly Detector ──► Gemma Narrator ──► Gothic UI
     (local)         (pandas)          (TabPFN/heuristic)    (Ollama local)    (FastAPI + SSE)
```

1. **Extract** — Copies your Chrome/Chromium/Brave/Edge history from SQLite (bypasses the browser lock)
2. **Detect** — Scores every visit as normal vs. "haunted" using TabPFN or heuristic rules, then groups anomalous visits into sessions
3. **Narrate** — Feeds each session to Gemma running locally via Ollama, which writes gothic horror prose in the voice of "The Archivist"
4. **Speak** — ElevenLabs voices the narration aloud (falls back to browser speech synthesis if no API key)
5. **Present** — A gothic web UI streams chapters in real-time with typewriter animation, flickering candles, and a haunting intensity meter

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **Ollama** installed and running with Gemma pulled
- A Chromium-based browser (Chrome, Brave, Edge, Chromium) with some browsing history

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/your-username/browser-history-horror.git
cd browser-history-horror

# 2. Install dependencies
pip install -r requirements.txt

# 3. Make sure Ollama is running with Gemma
ollama serve              # if not already running
ollama pull gemma3:4b     # ~3.4 GB download (one-time)

# 4. (Optional) Configure external services
cp .env.example .env
# Edit .env with your ElevenLabs API key and/or Sentry DSN

# 5. Run it
python seance.py
```

Your browser opens to `http://localhost:8000`. Click **BEGIN THE SÉANCE** and wait for
the horror to unfold.

---

## What You'll See

The UI walks through four states:

| State | What happens |
|---|---|
| **Idle** | Gothic landing page with animated candles. One button: *Begin the Séance*. |
| **Loading** | Progress bar cycles through: *Summoning records → Detecting anomalies → The Archivist is writing…* |
| **Narrating** | Chapters stream in one at a time with typewriter text, audio playback, and a haunting intensity bar per chapter. |
| **Complete** | All chapters rendered. A *Save Your Grimoire* button downloads the full story as a self-contained HTML file. |

---

## Configuration

All configuration lives in `.env` (copy from `.env.example`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `OLLAMA_BASE_URL` | No | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | No | `gemma3:4b` | Model to use for narration (`gemma3:4b` or `gemma3:8b`) |
| `ELEVENLABS_API_KEY` | No | — | Enables voiced narration. Without it, falls back to browser speech synthesis. |
| `ELEVENLABS_VOICE_ID` | No | `onwK4e9ZLuTAKqWW3xje` | ElevenLabs voice ID (default: Daniel) |
| `SENTRY_DSN` | No | — | Enables pipeline tracing via Sentry |
| `DEMO_MODE` | No | `false` | Uses anonymized fixture data instead of live history |

---

## Supported Browsers

The tool auto-detects the first available history file (picks the one with the most recent entry):

| Browser | macOS | Linux |
|---|---|---|
| Chrome | `~/Library/Application Support/Google/Chrome/Default/History` | `~/.config/google-chrome/Default/History` |
| Chromium | `~/Library/Application Support/Chromium/Default/History` | `~/.config/chromium/Default/History` |
| Brave | `~/Library/Application Support/BraveSoftware/Brave-Browser/Default/History` | `~/.config/BraveSoftware/Brave-Browser/Default/History` |
| Edge | `~/Library/Application Support/Microsoft Edge/Default/History` | `~/.config/microsoft-edge/Default/History` |

> **Note:** Firefox and Safari use different formats and are not supported. Windows is not yet supported.

---

## Project Structure

```
browser-history-horror/
├── seance.py              # Entry point — checks prerequisites, starts server, opens browser
├── requirements.txt       # Python dependencies
├── .env.example           # Configuration template
│
├── src/
│   ├── history.py         # Browser history SQLite reader + feature engineering
│   ├── detector.py        # Anomaly scoring (TabPFN with heuristic fallback) + session grouping
│   ├── narrator.py        # Gemma prompt design, Ollama streaming, output sanitization
│   ├── tts.py             # ElevenLabs TTS with in-memory cache + speechSynthesis fallback
│   ├── api.py             # FastAPI server — SSE streaming, audio serving, grimoire generation
│   └── tracer.py          # Sentry span instrumentation (optional)
│
├── frontend/
│   └── index.html         # Self-contained gothic UI (no build step, no external JS deps)
│
├── fixture/               # Anonymized demo data (for DEMO_MODE)
├── tests/                 # Test suite
└── docs/                  # Internal design specs and component contracts
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Your Machine (all processing happens here)                     │
│                                                                 │
│  Browser SQLite ──► history.py ──► detector.py                  │
│  (copied to tmp)    (pandas)       (TabPFN / heuristic)         │
│                                         │                       │
│                                    haunted sessions              │
│                                         │                       │
│                                    narrator.py                  │
│                                    (Ollama ← gemma3:4b)         │
│                                         │                       │
│                                    chapter text                  │
│                                         │                       │
│                         ┌───────────────┤                       │
│                         │               │                       │
│                     api.py          tracer.py                   │
│                     (FastAPI+SSE)   (Sentry spans)              │
│                         │                                       │
└─────────────────────────┼───────────────────────────────────────┘
                          │
                 ┌────────▼────────────────────────┐
                 │  External (optional)             │
                 │                                  │
                 │  ElevenLabs ← chapter text only  │
                 │  Sentry     ← trace metadata     │
                 └──────────────────────────────────┘
```

---

## Privacy Design

This tool processes deeply personal data. The privacy model is simple:

- **History stays local.** The SQLite file is copied to a temp file, queried, and deleted. No history data is sent anywhere.
- **Gemma runs locally.** All narration is generated on your machine via Ollama. No cloud LLM calls.
- **URLs are stripped.** Only domains are used for analysis. Full URLs and query strings are never stored or passed to the narrator.
- **ElevenLabs receives prose, not history.** The TTS service only sees Gemma's gothic output — text like *"the eternal repository of unanswered questions"*, not `stackoverflow.com`.
- **Sentry receives numbers, not content.** Trace spans contain only latencies, row counts, and model names. `send_default_pii=False` is enforced.

---

## Anomaly Detection

The detector uses a two-stage approach:

**Stage 1 — Heuristic seed labels.** Rows are flagged as "obviously haunted" by simple rules:
- Visits between midnight and 4am
- 5+ visits to the same domain within 30 minutes
- Rapid-fire revisits (< 30 seconds between hits)
- Unknown domains visited after 10pm

**Stage 2 — TabPFN refinement.** [TabPFN](https://github.com/PriorLabs/TabPFN) is a tabular
foundation model that performs in-context classification. It uses the heuristic labels as training
signal and picks up subtler cross-feature patterns that flat thresholds miss.

If TabPFN is not available (requires [HuggingFace gated model access](https://huggingface.co/Prior-Labs/tabpfn_3_5)),
the detector falls back to heuristic scoring only. The pipeline still works — you just get
less nuanced anomaly detection.

### Enabling TabPFN

```bash
# 1. Accept the license at https://huggingface.co/Prior-Labs/tabpfn_3_5
# 2. Authenticate
pip install huggingface_hub
huggingface-cli login
# 3. TabPFN will download weights on first run (~automatic)
```

---

## The Archivist (Narrator)

Gemma writes in the voice of **The Archivist** — a gothic narrator who records browsing
habits with dry, melancholic gravity. Key rules enforced by the system prompt:

- Second person ("the developer", "they") — never "you"
- Domains are replaced with gothic aliases (*"the orange forum"*, *"the video labyrinth"*, *"the eternal repository of unanswered questions"*)
- One darkly comic observation per chapter
- One ominous closing line foreshadowing the next day
- No raw URLs, search queries, or identifying content ever appears in the output

Sample output:

> *It began on Monday at 23:47. A single search — innocent enough on its face. The developer
> already possessed this knowledge. They had possessed it for years. Yet here they were,
> returning to the eternal repository of unanswered questions, seeking the validation of
> strangers who had asked the same thing in 2009.*
>
> *They left. They returned. They left again. Eight times between 23:47 and 00:31, the same
> domain welcomed them back with the patience of a very old spider.*

---

## API Reference

The server exposes these endpoints (all on `localhost:8000` by default):

| Method | Route | Description |
|---|---|---|
| `GET` | `/` | Serves the frontend UI |
| `GET` | `/health` | Returns `{"status", "ollama", "history_found"}` |
| `POST` | `/seance` | Starts a new pipeline run, returns `{"session_id"}` |
| `GET` | `/seance/{id}/stream` | SSE stream of pipeline progress and chapter events |
| `GET` | `/audio/{session_id}/{chapter_id}` | Serves cached TTS audio (MP3) |
| `GET` | `/grimoire/{session_id}` | Downloads the story as a self-contained HTML file |

### SSE Event Types

| Event | Payload | When |
|---|---|---|
| `progress` | `{"stage", "pct"}` | During summoning / detecting / narrating |
| `chapter` | `{"id", "day", "title", "anomaly_score", "domains", "intensity"}` | New chapter starts |
| `chapter_text` | `{"id", "text_chunk"}` | Streamed narration text (character by character from Gemma) |
| `chapter_audio_ready` | `{"chapter_id"}` | TTS audio is cached and ready to play |
| `done` | `{"chapters", "grimoire_url"}` | Pipeline complete |
| `error` | `{"message", "stage"}` | Something went wrong |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Ollama is not running` | Run `ollama serve` in a separate terminal |
| `Model not found` | Run `ollama pull gemma3:4b` (or `gemma3:8b` for higher quality) |
| `No browser history found` | Make sure you have Chrome/Brave/Edge installed with browsing history. Only the Default profile is checked. |
| `TabPFN unavailable` | Not critical — heuristic scoring kicks in. To enable: accept the license at HuggingFace and run `huggingface-cli login`. |
| `ElevenLabs failed` | Check your API key in `.env`. The UI automatically falls back to browser speech synthesis. |
| Port 8000 in use | Kill the existing process or edit `HOST`/`PORT` in `seance.py` |

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.11+ | TabPFN compatibility |
| Web framework | FastAPI + SSE | Async streaming with minimal boilerplate |
| LLM runtime | Ollama | Local Gemma inference, no cloud dependency |
| LLM model | Gemma 3 (4B/8B) | Open-source, runs well on laptop hardware |
| Anomaly detection | TabPFN | Tabular foundation model — ideal for one-shot, per-user classification |
| Text-to-speech | ElevenLabs | High-quality voice narration |
| Observability | Sentry SDK | Pipeline tracing with latency and token metrics |
| Frontend | Vanilla HTML/CSS/JS | Zero build step, self-contained single file |

---

## Prize Categories

This project targets the following Hacktoberfest 2026 prize categories:

| Prize | How |
|---|---|
| Best Use of Gemma | Core narrative engine, runs locally via Ollama |
| Best Use of TabPFN | Anomaly detection on browsing session features |
| Best Use of Render | Gothic web UI hosted as a static site demo |
| Best Use of ElevenLabs | Chapter narration voiced in-browser |
| Best Use of Sentry Agent Tracing | Full pipeline instrumented with spans |

---

## License

MIT

---

<p align="center">
<em>"The browser was closed, and the darkness of the room was left to do what darkness does."</em>
</p>
