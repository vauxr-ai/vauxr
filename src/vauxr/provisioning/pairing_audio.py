"""Configurable, bounded speech attached to the device's one-time proof response."""

import asyncio
import json
import logging
import struct
import time
from contextlib import aclosing
from pathlib import Path
from typing import TypedDict

from aiohttp import web

from vauxr.config import get_config
from vauxr.config_files import invalid, read_json, update_json
from vauxr.speech.wyoming_tts import synthesize

MEDIA_TYPE = "application/vnd.vauxr.pairing-audio"
MAX_INTRO_BYTES = 16000 * 2 * 40
MAX_CODE_BYTES = 16000 * 2 * 20
AUDIO_SLOTS = web.AppKey("pairing_audio_slots", asyncio.Semaphore)
log = logging.getLogger(__name__)
WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


class Prompts(TypedDict):
    intro: str
    code: str


DEFAULT_PROMPTS: Prompts = {
    "intro": "Your Vauxr device is now entering pairing mode. Please go to the Vauxr portal. "
             "Press the action button when you're ready to hear your pairing code.",
    "code": "Your pairing code is {code}. Press the action button to hear the code again.",
}


def validate_prompts(value: object) -> Prompts:
    if not isinstance(value, dict) or value.keys() != {"intro", "code"}:
        raise ValueError("Expected intro and code messages")
    for field, limit in (("intro", 400), ("code", 200)):
        text = value[field]
        if not isinstance(text, str) or not text.strip() or len(text) > limit:
            raise ValueError("Invalid pairing message length")
        if any(ord(char) < 32 and char not in "\n\t" for char in text):
            raise ValueError("Invalid pairing message characters")
    if value["code"].count("{code}") != 1 or "{code}" in value["intro"]:
        raise ValueError("Code message must contain {code} exactly once; intro must not contain it")
    return Prompts(intro=value["intro"].strip(), code=value["code"].strip())


def prompts_path() -> Path:
    return Path(get_config().data_dir) / "config.json"


def load_prompts() -> Prompts:
    path = prompts_path()
    data = read_json(path) if path.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get("pairing", {}), dict):
        raise invalid(path, "pairing", "expected an object")
    try:
        return validate_prompts(data.get("pairing", {}).get("prompts", DEFAULT_PROMPTS.copy()))
    except ValueError:
        raise invalid(path, "pairing.prompts", "expected intro (1–400 characters) and code (1–200 characters), with {code} exactly once in code only") from None


def save_prompts(value: object) -> Prompts:
    prompts = validate_prompts(value)
    path = prompts_path()

    def change(data: dict) -> dict:
        pairing = data.get("pairing", {})
        if not isinstance(pairing, dict):
            raise invalid(path, "pairing", "expected an object")
        return {**data, "pairing": {**pairing, "prompts": prompts}}

    update_json(path, change)
    return prompts


async def render(text: str, limit: int) -> bytes:
    pcm = bytearray()
    async with aclosing(synthesize(text, target_rate=16000)) as stream:
        async for chunk in stream:
            if len(pcm) + len(chunk) > limit:
                raise ValueError("pairing audio too long")
            pcm.extend(chunk)
    if not pcm or len(pcm) % 2:
        raise ValueError("invalid pairing audio")
    return bytes(pcm)


async def proof_response(request: web.Request, result: dict) -> web.Response:
    """VPA1 + LE JSON length + LE intro length + JSON + intro PCM + code PCM.

    Clips are S16LE mono at 16 kHz, retained only for the physical pairing window.
    JSON fallback still requests guided console delivery, never local synthesis.
    Only an already verified proof reaches here; no public audio URL is created.
    """
    if request.headers.get("Accept") != MEDIA_TYPE:
        return web.json_response(result)
    try:
        prompts = load_prompts()
    except (OSError, ValueError):
        log.warning("Pairing messages unavailable; using default messages")
        prompts = DEFAULT_PROMPTS.copy()
    result = {**result, "guided": True, "intro_text": prompts["intro"]}
    fallback = web.json_response(result)
    slots = request.app[AUDIO_SLOTS]
    if slots.locked():
        return fallback
    spoken_code = ", ".join(WORDS[int(digit)] for digit in result["code"])
    try:
        async with slots, asyncio.timeout(35):
            intro = await render(prompts["intro"], MAX_INTRO_BYTES)
            code = await render(prompts["code"].replace("{code}", spoken_code), MAX_CODE_BYTES)
        if time.time() >= result["expires_at"]:
            return fallback
    except Exception:  # noqa: BLE001 — provider errors must use the redacted fallback
        # Provider exceptions may contain text/code; never include them.
        log.warning("Pairing speech unavailable; using device console delivery")
        return fallback
    metadata = json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    envelope = b"VPA1" + struct.pack("<II", len(metadata), len(intro)) + metadata + intro + code
    return web.Response(body=envelope, content_type=MEDIA_TYPE)
