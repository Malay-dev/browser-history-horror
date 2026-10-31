#!/usr/bin/env python3
"""
Browser History Horror — entry point.

Run:  python seance.py
Opens your browser to a gothic UI that narrates your browsing week as horror.
"""

from __future__ import annotations

import logging
import os
import sys
import webbrowser

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

console = Console()
load_dotenv()

HOST = "127.0.0.1"
PORT = 8000


def _check_prerequisites() -> bool:
    """Verify Ollama and browser history are available before starting."""
    import asyncio
    from src.narrator import check_ollama
    from src.history import find_history_file, HistoryNotFoundError

    ok = True

    # Check Ollama
    loop = asyncio.new_event_loop()
    ollama_ok = loop.run_until_complete(check_ollama())
    loop.close()

    if not ollama_ok:
        console.print(
            Panel(
                "[bold red]Ollama is not running or model not found.[/]\n\n"
                "Start it with:\n"
                "  [cyan]ollama serve[/]\n"
                "  [cyan]ollama pull gemma3:4b[/]",
                title="⚠️  Ollama Required",
                border_style="red",
            )
        )
        ok = False

    # Check history
    try:
        path = find_history_file()
        console.print(f"  📖 History found: [dim]{path}[/]")
    except HistoryNotFoundError as exc:
        console.print(
            Panel(
                f"[bold red]{exc}[/]",
                title="⚠️  No Browser History",
                border_style="red",
            )
        )
        ok = False

    # Check optional services
    if os.getenv("ELEVENLABS_API_KEY"):
        console.print("  🔊 ElevenLabs: [green]configured[/]")
    else:
        console.print("  🔊 ElevenLabs: [yellow]not configured — using browser speech[/]")

    if os.getenv("SENTRY_DSN"):
        console.print("  📊 Sentry: [green]configured[/]")
    else:
        console.print("  📊 Sentry: [yellow]not configured — tracing disabled[/]")

    return ok


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    console.print(
        Panel(
            "[bold]The Séance of Browsing Past[/]\n"
            "[dim]A gothic reckoning with your recent tabs[/]",
            title="🕯️  Browser History Horror  🕯️",
            border_style="bright_yellow",
        )
    )
    console.print()

    console.print("[bold]Checking prerequisites...[/]")
    if not _check_prerequisites():
        console.print(
            "\n[bold red]Cannot start — fix the issues above and try again.[/]"
        )
        sys.exit(1)

    console.print()
    url = f"http://{HOST}:{PORT}"
    console.print(f"[bold green]Starting server at {url}[/]")
    console.print("[dim]Press Ctrl+C to stop.[/]\n")

    # Open browser after a short delay (server needs to start first)
    import threading
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    import uvicorn
    uvicorn.run("src.api:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
