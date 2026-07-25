"""
pipeline/api.py — lightweight, on-demand API for the recap pipeline.

WHY THIS FILE EXISTS (fixes the data-bloat problem):
runner.py's run_episode() eagerly generates a recap for EVERY character
in series_config, every time it runs — even ones nobody will ever look
at. That's the actual source of a bloated data/ folder: N characters x
full pipeline x every run, regardless of demand.

This file replaces that eager loop with two on-demand endpoints:

  GET /series/{series_id}/episodes/{ep_num}/characters
      -> lazily ingests the episode (asr + extraction) ONCE if not
         already done, then returns the character list. No recap or
         personality generation happens here — this endpoint is cheap.

  GET /series/{series_id}/episodes/{ep_num}/characters/{character}/pov
      -> generates (or returns the CACHED version of) exactly ONE
         character's personality + recap + judge. Every other
         character is untouched. Second request for the same
         character is instant (reads the cached file, does not
         regenerate) unless ?force=true.

Nothing here duplicates runner.py's logic — it imports and reuses the
exact same stage functions runner.py uses, just calls them one
character at a time, on request, instead of looping over everyone.

RUN:
    pip install fastapi uvicorn
    uvicorn pipeline.api:app --reload --port 8000

TRY:
    curl http://localhost:8000/series/mahabharata/episodes/170/characters
    curl http://localhost:8000/series/mahabharata/episodes/170/characters/Bhima/pov
"""

import sys
import json
import glob
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from storage import stage_done, recap_dir
from stages.asr import run_asr
from stages.extraction import extract_bible_entry
from stages.judge import run_with_retries as generate_and_judge_recap
from stages.personality import generate_personality
from config import (
    characters_for,
    discover_characters_from_bible,
    load_series_config,
    DATA_DIR,
)

app = FastAPI(title="Pocket FM Recap API", version="0.1")


# ---------------------------------------------------------------------
# Response models — keep responses small and predictable
# ---------------------------------------------------------------------

class CharacterListResponse(BaseModel):
    series_id: str
    episode_num: int
    characters: list[str]          # includes "omniscient" as an option
    already_generated: list[str]   # subset with a cached POV ready instantly


class POVResponse(BaseModel):
    series_id: str
    episode_num: int
    character: str
    version: int
    judge_passed: Optional[bool]
    cached: bool                   # False if this request just generated it
    text: str


# ---------------------------------------------------------------------
# Shared lazy-ingestion helper — runs asr+extraction ONCE per episode,
# on first request that needs them. Every endpoint routes through this
# instead of re-implementing the "is this already done" check.
# ---------------------------------------------------------------------

def _ensure_episode_ingested(series_id: str, ep_num: int) -> None:
    """
    Guarantees transcript.json has been transcribed and a bible entry
    exists for this episode. Idempotent — checks stage_done() first, so
    calling this on every request costs nothing after the first time.

    Does NOT touch recap/judge/personality for any character — this is
    the character-agnostic minimum every endpoint below needs.
    """
    from storage import transcript_path
    if not transcript_path(series_id, ep_num).exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No transcript.json for {series_id} episode {ep_num}. "
                f"Convert the raw script first, e.g. via "
                f"runner.generate_transcript_from_txt(), before calling this API."
            ),
        )

    if not stage_done(series_id, ep_num, "asr"):
        run_asr(series_id, ep_num)

    if not stage_done(series_id, ep_num, "extraction"):
        extract_bible_entry(series_id, ep_num)


def _resolve_character_list(series_id: str, ep_num: int) -> list[str]:
    """
    Returns every character worth offering for this episode: whatever's
    already in series_config, UNION whatever the bible supports at the
    default appearance threshold — computed fresh, NOT written to disk.
    This is deliberately read-only; unlike runner.py's
    sync_characters_from_bible(), browsing the character list should
    never mutate series_config.json as a side effect of a GET request.
    """
    configured = set(load_series_config(series_id).get("characters", []))
    discovered = discover_characters_from_bible(series_id, min_appearances=2)
    return ["omniscient"] + sorted(configured | discovered)


def _latest_script_version(series_id: str, ep_num: int, character: Optional[str]) -> Optional[dict]:
    """Loads the highest script_v*.json for a character, if one exists."""
    existing = sorted(glob.glob(str(recap_dir(series_id, ep_num, character) / "script_v*.json")))
    if not existing:
        return None
    with open(existing[-1]) as f:
        return json.load(f)


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------

@app.get("/series/{series_id}/episodes/{ep_num}/characters", response_model=CharacterListResponse)
def list_characters(series_id: str, ep_num: int):
    """
    Cheap endpoint: ingests the episode if needed (asr+extraction only —
    no recap generation happens here), then returns who's available to
    pick a POV for, plus which of them already have a cached recap
    ready (so the UI can show "instant" vs "will take a moment").
    """
    _ensure_episode_ingested(series_id, ep_num)

    characters = _resolve_character_list(series_id, ep_num)
    already_generated = [
        c for c in characters
        if stage_done(series_id, ep_num, "judge", character=(None if c == "omniscient" else c))
    ]

    return CharacterListResponse(
        series_id=series_id,
        episode_num=ep_num,
        characters=characters,
        already_generated=already_generated,
    )


@app.get(
    "/series/{series_id}/episodes/{ep_num}/characters/{character}/pov",
    response_model=POVResponse,
)
def get_character_pov(
    series_id: str,
    ep_num: int,
    character: str,
    force: bool = Query(False, description="regenerate even if a cached version exists"),
):
    """
    The core on-demand endpoint. Generates personality + recap + judge
    for EXACTLY this one character — nobody else is touched, nothing
    else is written to disk. If this character's recap was already
    generated by an earlier request, returns the cached file instantly
    instead of calling the LLM again.

    character: exact name from the /characters list, or "omniscient"
    for the all-perspectives baseline.
    """
    _ensure_episode_ingested(series_id, ep_num)

    valid_characters = _resolve_character_list(series_id, ep_num)
    if character not in valid_characters:
        raise HTTPException(
            status_code=404,
            detail=f"'{character}' not available for this episode. Options: {valid_characters}",
        )

    internal_character = None if character == "omniscient" else character

    cached = not force and stage_done(series_id, ep_num, "judge", character=internal_character)

    if not cached:
        if internal_character is not None:
            generate_personality(series_id, ep_num, internal_character)
        script = generate_and_judge_recap(series_id, ep_num, character=internal_character)
        script_dict = script.dict() if hasattr(script, "dict") else script
    else:
        script_dict = _latest_script_version(series_id, ep_num, internal_character)
        if script_dict is None:
            # status.json says done but the file is missing — treat as
            # not cached and generate fresh rather than 500ing.
            if internal_character is not None:
                generate_personality(series_id, ep_num, internal_character)
            script = generate_and_judge_recap(series_id, ep_num, character=internal_character)
            script_dict = script.dict() if hasattr(script, "dict") else script
            cached = False

    judge_info = script_dict.get("judge")
    return POVResponse(
        series_id=series_id,
        episode_num=ep_num,
        character=character,
        version=script_dict.get("version", 1),
        judge_passed=(judge_info.get("faithful") if judge_info else None),
        cached=cached,
        text=script_dict.get("text", ""),
    )


@app.get("/health")
def health():
    return {"status": "ok"}