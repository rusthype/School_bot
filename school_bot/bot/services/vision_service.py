"""
Attendance photo → marks dictionary.

3-layer OCR pipeline:
  Layer 1 — Tesseract (local, zero cost, fast)
  Layer 2 — OpenRouter / Gemini 2.0 Flash (cloud, free tier)
  Layer 3 — Google Gemini direct API (fallback, bepul 15 RPM / 1M token/day)
  Layer 4 — Empty dict (graceful degradation)

Providers are tried in order; the first successful result is returned.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from typing import Any

import aiohttp
from PIL import Image, ImageEnhance
import pytesseract

from school_bot.bot.services.logger_service import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_GEMINI_URL     = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)
_TIMEOUT        = aiohttp.ClientTimeout(total=30)

_PROMPT_TEMPLATE = (
    "Student list (id: name):\n{names}\n\n"
    "Analyze the attendance sheet photo. Match each name and detect status.\n"
    "Return JSON ONLY — no markdown, no explanation:\n"
    '{"results": [{"id": <int>, "status": "present|absent|late|unknown"}]}'
)

VALID_STATUSES = {"present", "absent", "late"}


# ---------------------------------------------------------------------------
# Image pre-processing
# ---------------------------------------------------------------------------
def preprocess_image(img: Image.Image) -> Image.Image:
    """Grayscale + contrast boost + min 1000px width."""
    img = img.convert("L")
    img = ImageEnhance.Contrast(img).enhance(2.0)
    if img.width < 1000:
        ratio = 1000 / img.width
        img = img.resize((1000, int(img.height * ratio)), Image.Resampling.LANCZOS)
    return img


# ---------------------------------------------------------------------------
# OCR text parser
# ---------------------------------------------------------------------------
def parse_ocr_text(text: str, students: list[dict[str, Any]]) -> dict[int, str]:
    """Fuzzy-match student names in OCR output → status dict."""
    marks: dict[int, str] = {}
    lines = text.lower().split("\n")
    for student in students:
        sid  = student["id"]
        name = student["name"].lower()
        for line in lines:
            if name in line:
                if any(m in line for m in ["+", "✓", "v", "keldi", "bor", "1"]):
                    marks[sid] = "present"
                elif any(m in line for m in ["-", "x", "kelmadi", "yoq", "0"]):
                    marks[sid] = "absent"
                elif any(m in line for m in ["!", "kech", "kechikdi"]):
                    marks[sid] = "late"
                break
    return marks


# ---------------------------------------------------------------------------
# AI provider helpers
# ---------------------------------------------------------------------------
def _build_prompt(students: list[dict[str, Any]]) -> str:
    names = "\n".join(f"{s['id']}: {s['name']}" for s in students)
    return _PROMPT_TEMPLATE.format(names=names)


def _parse_ai_response(raw: str) -> dict[int, str]:
    """Extract marks dict from AI JSON response."""
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    results = json.loads(raw).get("results", [])
    return {
        r["id"]: r["status"]
        for r in results
        if r.get("status") in VALID_STATUSES
    }


async def _call_openrouter(
    session: aiohttp.ClientSession,
    b64_image: str,
    prompt: str,
    api_key: str,
) -> dict[int, str] | None:
    """Layer 2: OpenRouter — Gemini 2.0 Flash (free model)."""
    payload = {
        "model": "google/gemini-2.0-flash-exp:free",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}},
                {"type": "text", "text": prompt},
            ],
        }],
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://alochi.org",
    }
    async with session.post(_OPENROUTER_URL, headers=headers, json=payload) as resp:
        if resp.status != 200:
            logger.warning("OpenRouter %d: %s", resp.status, await resp.text())
            return None
        data    = await resp.json()
        content = data["choices"][0]["message"]["content"]
        marks   = _parse_ai_response(content)
        logger.info("OpenRouter → %d marks", len(marks))
        return marks or None


async def _call_gemini_direct(
    session: aiohttp.ClientSession,
    b64_image: str,
    prompt: str,
    api_key: str,
) -> dict[int, str] | None:
    """Layer 3: Google Gemini direct API (bepul 15 RPM, 1M token/day)."""
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": b64_image}},
                {"text": prompt},
            ],
        }],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    url = f"{_GEMINI_URL}?key={api_key}"
    async with session.post(url, json=payload) as resp:
        if resp.status != 200:
            logger.warning("Gemini direct %d: %s", resp.status, await resp.text())
            return None
        data    = await resp.json()
        content = data["candidates"][0]["content"]["parts"][0]["text"]
        marks   = _parse_ai_response(content)
        logger.info("Gemini direct → %d marks", len(marks))
        return marks or None


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
async def run_ocr_pipeline(
    bot: Any,
    photo_file_id: str,
    students: list[dict[str, Any]],
) -> dict:
    """
    Run the 3-layer OCR pipeline.

    Returns:
        {"marks": {student_id: status, ...}, "source": "ocr"|"openrouter"|"gemini"|None}
    """
    # ---- Download photo ----
    try:
        file      = await bot.get_file(photo_file_id)
        raw_bytes = await bot.download_file(file.file_path)
        img_data  = raw_bytes.read()
    except Exception as exc:
        logger.error("Photo download failed: %s", exc)
        return {"marks": {}, "source": None}

    # ---- Layer 1: Tesseract ----
    try:
        loop = asyncio.get_event_loop()

        def _blocking_ocr() -> str:
            img  = Image.open(io.BytesIO(img_data))
            img  = preprocess_image(img)
            return pytesseract.image_to_string(img, lang="uzb+rus+eng", config="--psm 6")

        ocr_text = await loop.run_in_executor(None, _blocking_ocr)
        if ocr_text:
            marks      = parse_ocr_text(ocr_text, students)
            confidence = len(marks) / len(students) if students else 0
            if confidence >= 0.6:
                logger.info("Tesseract → %d marks (conf=%.0f%%)", len(marks), confidence * 100)
                return {"marks": marks, "source": "ocr"}
    except Exception as exc:
        logger.error("Tesseract error: %s", exc)

    # ---- Layers 2 & 3: Cloud AI ----
    b64_image = base64.b64encode(img_data).decode()
    prompt    = _build_prompt(students)

    # Import settings lazily to avoid circular imports at module load time
    try:
        from school_bot.bot.config import Settings
        cfg = Settings()  # type: ignore[call-arg]
        openrouter_key = cfg.openrouter_api_key
        gemini_key     = cfg.gemini_api_key
    except Exception:
        openrouter_key = ""
        gemini_key     = ""

    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:

        # Layer 2: OpenRouter
        if openrouter_key:
            try:
                marks = await _call_openrouter(session, b64_image, prompt, openrouter_key)
                if marks:
                    return {"marks": marks, "source": "openrouter"}
            except Exception as exc:
                logger.error("OpenRouter exception: %s", exc)

        # Layer 3: Gemini direct
        if gemini_key:
            try:
                marks = await _call_gemini_direct(session, b64_image, prompt, gemini_key)
                if marks:
                    return {"marks": marks, "source": "gemini"}
            except Exception as exc:
                logger.error("Gemini direct exception: %s", exc)

    # Layer 4: Empty fallback
    logger.warning("All OCR layers failed for photo %s", photo_file_id)
    return {"marks": {}, "source": None}
