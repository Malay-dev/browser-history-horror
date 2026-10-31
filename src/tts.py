"""ElevenLabs TTS — generate audio for chapters with in-memory cache."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW3xje")  # Daniel
MODEL_ID = "eleven_turbo_v2_5"
OUTPUT_FORMAT = "mp3_44100_96"

_audio_cache: dict[str, bytes] = {}


async def generate_audio(session_id: str, chapter_id: int, text: str) -> bytes | None:
    """Generate audio for a chapter. Returns MP3 bytes or None on failure."""
    if not ELEVENLABS_API_KEY:
        logger.info("No ElevenLabs API key — browser speechSynthesis will be used.")
        return None

    cache_key = f"{session_id}_{chapter_id}"
    if cache_key in _audio_cache:
        return _audio_cache[cache_key]

    try:
        from elevenlabs import ElevenLabs

        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        audio_stream = client.text_to_speech.convert(
            voice_id=VOICE_ID,
            text=text,
            model_id=MODEL_ID,
            output_format=OUTPUT_FORMAT,
            voice_settings={
                "stability": 0.85,
                "similarity_boost": 0.75,
                "style": 0.30,
                "use_speaker_boost": True,
            },
        )
        audio_bytes = b"".join(audio_stream)
        _audio_cache[cache_key] = audio_bytes
        logger.info(
            "TTS generated for chapter %d — %d bytes.", chapter_id, len(audio_bytes)
        )
        return audio_bytes
    except Exception as exc:
        logger.warning("ElevenLabs failed for chapter %d: %s", chapter_id, exc)
        return None


def get_cached_audio(session_id: str, chapter_id: int) -> bytes | None:
    """Return cached audio bytes, or None if not ready."""
    return _audio_cache.get(f"{session_id}_{chapter_id}")
