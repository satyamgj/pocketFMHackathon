"""
pipeline/stages/extraction.py — ALGORITHM.md Phase A (Ingest).

Runs once per episode. Segments the transcript into beats, pulls out
atomic facts, and records who observed / misperceived each one. This
replaces the old loose "characters + events" bible shape with the
algorithm's Fact/Beat model — the thing every later phase filters by.

ONE LLM CALL, not a multi-step pipeline: given the whole transcript, ask
for facts[] and beats[] in one shot. Simpler to run and debug than
segmenting first and extracting per-segment; the transcript for one
episode is short enough that context isn't a constraint.

APPEND-ONLY (algorithm invariant #2): this file never edits an existing
fact. A later contradiction becomes a NEW fact with `supersedes` set.
"""

import sys
import json
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError
from schemas import BibleEntry
from storage import transcript_path, save_json, update_status, DATA_DIR

PROMPT = """You are building a knowledge-and-perception ledger for an episodic story,
so that later a reader can be shown ONLY what one character personally knows.

Episode number: {episode_num}

Transcript (speaker, tone note, line):
{transcript_text}

Segment this into BEATS (distinct narrative moments/action boundaries) and extract
atomic FACTS from them. Output ONLY valid JSON, no preamble, no markdown fences:

{{
  "facts": [
    {{
      "id": "f_001",
      "claim": "<single verifiable proposition, one sentence>",
      "type": "event | state | relationship | attribute",
      "established_at": {episode_num},
      "observers": ["<characters who directly witnessed/learned this>"],
      "misperceivers": [{{"char": "<name>", "believes": "<the false version they hold>"}}],
      "confidence": "explicit | implied"
    }}
  ],
  "beats": [
    {{
      "id": "b_{episode_num}_01",
      "unit": {episode_num},
      "order": 1,
      "summary": "<what observably happens, one sentence>",
      "present": ["<characters present for this beat>"],
      "perceived_by": {{"<character>": "full | partial | none"}},
      "establishes": ["<fact ids this beat establishes>"],
      "silences": ["<what a present character notably does NOT say or react to>"]
    }}
  ]
}}

Rules:
- observers = who is ACTUALLY AWARE of the fact (present + paying attention, or told directly).
  Being merely in the room during unrelated dialogue does not make someone an observer.
- misperceivers = ONLY characters whose recorded behavior implies a specific false belief.
  Most facts have zero misperceivers — do not force one.
- A beat's `present` list should be conservative: only characters clearly in the scene.
- Order beats sequentially as they occur in the transcript.
- Output ONLY the JSON object.
"""


def _call_llm(prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _bible_entry_path(series_id: str, ep_num: int) -> Path:
    # Same directory as transcript.json for this episode.
    return transcript_path(series_id, ep_num).parent / "bible_entry.json"


def _series_bible_path(series_id: str) -> Path:
    return DATA_DIR / "series" / series_id / "bible.json"


def _append_to_series_bible(series_id: str, entry: dict) -> None:
    """Append-only accumulation across episodes (algorithm invariant #2:
    the fact store never edits or deletes; new episodes just append)."""
    path = _series_bible_path(series_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except FileNotFoundError:
        entries = []
    entries = [e for e in entries if e.get("episode_num") != entry["episode_num"]]
    entries.append(entry)
    save_json(path, entries)


def extract_bible_entry(series_id: str, ep_num: int, max_retries: int = 1) -> BibleEntry:
    """
    Main entry point. Reads transcript.json, produces a BibleEntry
    (facts + beats), writes it to this episode's bible_entry.json, and
    appends it into the series-wide accumulated bible.json.
    """
    update_status(series_id, ep_num, stage="extraction", state="running")

    try:
        with open(transcript_path(series_id, ep_num), "r", encoding="utf-8") as f:
            transcript = json.load(f)

        lines = []
        for seg in transcript.get("segments", []):
            note = f" ({seg['note']})" if seg.get("note") else ""
            lines.append(f"{seg['speaker']}{note}: {seg['text']}")
        transcript_text = "\n".join(lines)

        prompt = PROMPT.format(episode_num=ep_num, transcript_text=transcript_text)

        last_error = None
        entry = None
        for attempt in range(max_retries + 1):
            p = prompt if attempt == 0 else prompt + f"\n\nPrevious output failed: {last_error}"
            raw = _call_llm(p)
            cleaned = raw.strip().strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            try:
                data = json.loads(cleaned)
                data["series_id"] = series_id
                data["episode_num"] = ep_num
                entry = BibleEntry(**data)
                break
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = str(e)
                entry = None

        if entry is None:
            raise ValueError(f"Extraction failed for ep {ep_num} after {max_retries + 1} attempts: {last_error}")

        entry_dict = entry.dict()
        save_json(_bible_entry_path(series_id, ep_num), entry_dict)
        _append_to_series_bible(series_id, entry_dict)

    except Exception as e:
        update_status(series_id, ep_num, stage="extraction", state="failed", error=str(e))
        raise

    update_status(series_id, ep_num, stage="extraction", state="done")
    return entry