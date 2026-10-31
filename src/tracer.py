"""Sentry agent tracing — instrument the séance pipeline."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_sentry_enabled = False


def init_sentry() -> None:
    """Initialize Sentry SDK if DSN is configured."""
    global _sentry_enabled
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        logger.info("SENTRY_DSN not set — tracing disabled.")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FastApiIntegration()],
            traces_sample_rate=1.0,
            profiles_sample_rate=0.0,
            send_default_pii=False,
        )
        _sentry_enabled = True
        logger.info("Sentry tracing initialized.")
    except Exception as exc:
        logger.warning("Sentry init failed: %s", exc)


@contextmanager
def trace_stage(name: str, **attributes):
    """Wrap a pipeline stage in a Sentry span. No-op if Sentry is not configured."""
    if not _sentry_enabled:
        yield None
        return

    import sentry_sdk

    with sentry_sdk.start_span(op=name, description=name) as span:
        for k, v in attributes.items():
            span.set_data(k, v)
        try:
            yield span
        except Exception:
            span.set_status("internal_error")
            raise
