import asyncio
import base64
import json
import io
import os
import logging
from typing import Any

import aiohttp
from PIL import Image, ImageEnhance
import pytesseract

from school_bot.bot.services.logger_service import get_logger

logger = get_logger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")


def preprocess_image(img: Image.Image) -> Image.Image:
    """Preprocess image for better OCR: grayscale, contrast, resize."""
    # Convert to grayscale
    img = img.convert("L")
    
    # Enhance contrast (2x)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    
    # Resize if too small (min 1000px width)
    if img.width < 1000:
        ratio = 1000 / img.width
        new_size = (1000, int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
    
    return img


def parse_ocr_text(text: str, students: list[dict[str, Any]]) -> dict[int, str]:
    """
    Parse text for student names + status markers.
    present: +, ✓, v, keldi, bor, 1
    absent: -, x, kelmadi, yoq, 0
    late: !, kech, kechikdi
    """
    marks = {}
    lines = text.lower().split("\n")
    
    for student in students:
        student_id = student["id"]
        name = student["name"].lower()
        
        # Simple fuzzy match: check if name is in any line
        for line in lines:
            if name in line:
                # Found student name, look for markers in the same line
                if any(m in line for m in ["+", "✓", "v", "keldi", "bor", "1"]):
                    marks[student_id] = "present"
                    break
                elif any(m in line for m in ["-", "x", "kelmadi", "yoq", "0"]):
                    marks[student_id] = "absent"
                    break
                elif any(m in line for m in ["!", "kech", "kechikdi"]):
                    marks[student_id] = "late"
                    break
    
    return marks


async def run_ocr_pipeline(bot: Any, photo_file_id: str, students: list[dict[str, Any]]) -> dict:
    """
    3-layer vision pipeline:
    1. Tesseract OCR
    2. Gemini 2.0 via OpenRouter
    3. Fallback to empty
    """
    try:
        # 1. Download photo bytes
        file = await bot.get_file(photo_file_id)
        photo_bytes = await bot.download_file(file.file_path)
        img_data = photo_bytes.read()
        
        # 2. Tesseract OCR (Layer 1)
        loop = asyncio.get_event_loop()
        
        def blocking_ocr():
            try:
                img = Image.open(io.BytesIO(img_data))
                img = preprocess_image(img)
                # Tesseract binary path in Docker: /usr/bin/tesseract (default usually works)
                text = pytesseract.image_to_string(img, lang="uzb+rus+eng", config="--psm 6")
                return text
            except Exception as e:
                logger.error(f"Tesseract error: {e}")
                return ""

        ocr_text = await loop.run_in_executor(None, blocking_ocr)
        if ocr_text:
            marks = parse_ocr_text(ocr_text, students)
            confidence = len(marks) / len(students) if students else 0
            if confidence >= 0.6:
                return {"marks": marks, "source": "ocr"}
        
        # 3. OpenRouter Gemini Flash (Layer 2)
        if OPENROUTER_API_KEY:
            try:
                b64_image = base64.b64encode(img_data).decode("utf-8")
                names_list = "\n".join([f"{s['id']}: {s['name']}" for s in students])
                
                payload = {
                    "model": "google/gemini-2.0-flash-exp:free",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
                                },
                                {
                                    "type": "text",
                                    "text": (
                                        f"Student list (id: name):\n{names_list}\n\n"
                                        "Analyze the attendance sheet photo. Match names and detect status.\n"
                                        "Return JSON only: {\"results\": [{\"id\": N, \"status\": \"present|absent|late|unknown\"}]}"
                                    )
                                }
                            ]
                        }
                    ],
                    "response_format": {"type": "json_object"}
                }
                
                headers = {
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://alochi.org"
                }
                
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=payload
                    ) as resp:
                        if resp.status == 200:
                            result_data = await resp.json()
                            content = result_data["choices"][0]["message"]["content"]
                            # Some models might return markdown json block
                            if "```json" in content:
                                content = content.split("```json")[1].split("```")[0].strip()
                            
                            ai_results = json.loads(content).get("results", [])
                            ai_marks = {}
                            for res in ai_results:
                                if res["status"] in ["present", "absent", "late"]:
                                    ai_marks[res["id"]] = res["status"]
                            
                            if ai_marks:
                                return {"marks": ai_marks, "source": "ai"}
                        else:
                            logger.error(f"OpenRouter error: {resp.status} - {await resp.text()}")
            except Exception as e:
                logger.error(f"AI Vision error: {e}")

    except Exception as e:
        logger.error(f"OCR Pipeline error: {e}")
    
    # 4. Fallback (Layer 3)
    return {"marks": {}, "source": None}
