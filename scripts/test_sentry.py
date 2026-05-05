"""One-shot script to verify SENTRY_DSN works.

Usage (on server):
  cd ~/school-bot && docker compose exec bot python scripts/test_sentry.py

Expected: a 'sentry smoke test' exception appears in
alochi.sentry.io within 5 seconds.
"""
import os
import sentry_sdk

sentry_sdk.init(
    dsn=os.environ["SENTRY_DSN"],
    environment="smoke-test",
)
try:
    raise RuntimeError("sentry smoke test from school-bot")
except RuntimeError:
    event_id = sentry_sdk.capture_exception()
    print(f"Sent to Sentry, event_id={event_id}")
sentry_sdk.flush(timeout=5)
