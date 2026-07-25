"""
pipeline/stages/personality.py — ALGORITHM.md Phase B (Profile).

Run once per character per episode; cached and revalidated as
`valid_through` falls behind (algorithm: "revalidate when valid_through
falls behind by a chosen interval" — kept simple here as "regenerate
every episode using the previous profile as anchor," see B1-B8).
"""

import sys
import json
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError
from schemas import CharacterPersonality
from storage import load_bible_entry, save_personality, load_latest_personality, update_status

try:
    from stages import recap as recap_module
except ModuleNotFoundError:
    import recap as recap_module

PROMPT = """You are deriving a character profile from what they did and witnessed this episode,
per this schema (algorithm Phase B).

BE CONCRETE, NOT GENERIC. A profile that could describe five different characters has failed.
Bad: want="to protect his family". Good: want="to be the one who ends this before Yudhishthira
has to give another order". Bad: forbidden_affect="grief". Good: forbidden_affect="relief that
it's someone else's son and not his own, and hating himself for feeling it".

want            = the external goal they pursue across the most beats — state it as THIS
                   character would state it, specific to what just happened, not a category
need            = the internal lack their choices reveal but they don't state
                   (MUST oppose want — if aligned, reconsider)
wound           = the specific old memory/injury THIS episode's events reopen for them —
                   name the actual thing, not a category ("his father's death" not "loss")
lens            = their recurring interpretive distortion, stated as a bias, not a trait
preoccupation   = the concern that recurs where it isn't required
voice           = tempo/diction as concrete direction ("clips his sentences when lying"
                   not "terse"), tells = actual verbal habits/phrases they'd reach for
forbidden_affect = the emotion a reader would PREDICT for them in their defining situation
                    this episode — their monologue must never name this emotion directly

Character: {character}

PREVIOUS profile on record (if any) — keep want/lens/voice STABLE unless this
episode clearly justifies a shift; forbidden_affect, preoccupation, and wound SHOULD update
to whatever just happened:
{previous}

WHAT THIS CHARACTER KNOWS/WITNESSED THIS EPISODE:
{context}

Output ONLY valid JSON:
{{
  "character": "{character}",
  "episode_num": {episode_num},
  "want": "...",
  "need": "...",
  "wound": "...",
  "lens": "...",
  "preoccupation": "...",
  "voice": {{"tempo": "...", "diction": "...", "tells": ["an actual phrase or verbal habit, not a description"]}},
  "forbidden_affect": "...",
  "valid_through": {episode_num}
}}
"""


def _call_llm(prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _parse(raw: str, character: str, ep_num: int) -> CharacterPersonality:
    cleaned = raw.strip().strip("`")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    data = json.loads(cleaned)
    data["character"] = character
    data["episode_num"] = ep_num
    return CharacterPersonality(**data)


def generate_personality(series_id: str, ep_num: int, character: str, max_retries: int = 1) -> CharacterPersonality:
    update_status(series_id, ep_num, stage="personality", state="running", character=character)

    try:
        entry = load_bible_entry(series_id, ep_num)
        try:
            context, _state = recap_module._format_character_context(entry, character)
        except ValueError:
            context = "(not present or not tagged in any beat/fact this episode)"

        previous = load_latest_personality(series_id, character, at_or_before_ep=ep_num - 1)
        previous_text = json.dumps(previous, indent=2) if previous else "(none — first appearance)"

        base_prompt = PROMPT.format(
            character=character, previous=previous_text, context=context, episode_num=ep_num,
        )

        last_error = None
        personality = None
        for attempt in range(max_retries + 1):
            p = base_prompt if attempt == 0 else base_prompt + f"\n\nPrevious output failed: {last_error}"
            raw = _call_llm(p)
            try:
                personality = _parse(raw, character, ep_num)
                break
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = str(e)
                personality = None

        if personality is None:
            raise ValueError(f"Personality generation failed for {character} ep {ep_num}: {last_error}")

        save_personality(series_id, character, personality)

    except Exception as e:
        update_status(series_id, ep_num, stage="personality", state="failed", character=character, error=str(e))
        raise

    update_status(series_id, ep_num, stage="personality", state="done", character=character)
    return personality