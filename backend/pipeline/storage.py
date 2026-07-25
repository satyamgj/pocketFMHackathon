"""
storage.py — every stage reads and writes through these functions.
Nothing in the pipeline should open() a file directly.

CONCURRENCY NOTE (important for this version):
runner.py generates recaps for multiple characters IN PARALLEL using a
thread pool (recap generation is I/O-bound — waiting on the LLM API —
so threading gives a real speedup without any real complexity cost).
That means MULTIPLE THREADS may call update_status() and, more
dangerously, append_to_bible() at close to the same time.

- Per-character script/audio files never collide — each character
  writes to its own path, so no locking needed there.
- status.json and bible.json ARE shared across threads for the same
  episode, so both are protected with a lock below. This is the
  smallest amount of concurrency-safety that makes "generate 2
  characters' recaps at once" safe without needing a real database.
"""

import json
import threading
from pathlib import Path
from datetime import datetime, timezone

# One lock, used for all shared-file read-modify-write operations.
# A single global lock is intentionally simple for a 24h hackathon scope:
# it serializes shared-file writes across the whole pipeline, which costs
# nothing meaningful at this scale (writes are milliseconds; LLM calls,
# which dominate runtime, are NOT held under this lock).
_FILE_LOCK = threading.Lock()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------
# Generic JSON read/write
# ---------------------------------------------------------------------

def load_json(path: Path) -> dict:
    """Read a JSON file. Raises FileNotFoundError if missing — deliberately
    not swallowed, since a stage silently getting {} instead of real data
    is a worse bug than a loud crash."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    """Write a JSON file, creating parent folders if needed.
    ensure_ascii=False matters because content is likely Hindi/Hinglish —
    without it, non-ASCII text gets escaped into unreadable \\uXXXX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------
# Path helpers — the ONLY place file layout is defined
# ---------------------------------------------------------------------

def episode_dir(series_id: str, ep_num: int) -> Path:
    return DATA_DIR / "episodes" / series_id / str(ep_num)

def transcript_path(series_id: str, ep_num: int) -> Path:
    return episode_dir(series_id, ep_num) / "transcript.json"

def bible_entry_path(series_id: str, ep_num: int) -> Path:
    return episode_dir(series_id, ep_num) / "bible_entry.json"

def status_path(series_id: str, ep_num: int) -> Path:
    return episode_dir(series_id, ep_num) / "status.json"

def series_bible_path(series_id: str) -> Path:
    return DATA_DIR / "series" / series_id / "bible.json"

def recap_dir(series_id: str, ep_num: int, character: str = None) -> Path:
    """
    Character-aware recap directory. character=None keeps the original
    (omniscient) path for backward compatibility; a named character gets
    its own subfolder so parallel generation across characters never
    touches the same files.
    """
    base = DATA_DIR / "recaps" / series_id / str(ep_num)
    return base / character if character else base / "_omniscient"

def personality_dir(series_id: str, character: str) -> Path:
    """
    Where a character's evolving personality snapshots live — one file
    per episode, e.g. personality_v3.json for their state as of episode 3.
    Kept separate from recap_dir since personality isn't episode-scoped
    output, it's an evolving character-scoped record that recap
    generation for MULTIPLE episodes reads from.
    """
    return DATA_DIR / "characters" / series_id / character


# ---------------------------------------------------------------------
# Status tracking
# ---------------------------------------------------------------------

def _stage_key(stage: str, character: str = None) -> str:
    """
    Purpose: lets character-specific stages (recap, judge, render) be
    tracked independently in the same status.json, e.g.
    "recap:julie" and "recap:medstudent" as separate keys, while
    character-agnostic stages (asr, extraction) stay as plain "asr".
    """
    return f"{stage}:{character}" if character else stage


def update_status(series_id: str, ep_num: int, stage: str, state: str,
                   character: str = None, error: str = None) -> None:
    """
    Update one stage's status. Called at the start ('running') and end
    ('done'/'failed') of every stage function. LOCKED — see module
    docstring: multiple character threads may call this concurrently
    for the same episode's status.json.
    """
    key = _stage_key(stage, character)
    path = status_path(series_id, ep_num)
    with _FILE_LOCK:
        try:
            current = load_json(path)
        except FileNotFoundError:
            current = {"series_id": series_id, "episode_num": ep_num, "stages": {}}

        current["stages"][key] = {
            "stage": key,
            "state": state,
            "error": error,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_json(path, current)


def get_status(series_id: str, ep_num: int) -> dict:
    """Read current status. Returns an empty-but-valid shape if nothing
    has run yet, so callers never special-case 'file doesn't exist'."""
    try:
        return load_json(status_path(series_id, ep_num))
    except FileNotFoundError:
        return {"series_id": series_id, "episode_num": ep_num, "stages": {}}


def stage_done(series_id: str, ep_num: int, stage: str, character: str = None) -> bool:
    """
    Convenience check used by runner.py to decide whether to skip a
    stage. This is what makes the pipeline idempotent — re-run after a
    crash or mid-demo hiccup and it resumes instead of restarting.
    """
    key = _stage_key(stage, character)
    status = get_status(series_id, ep_num)
    return status["stages"].get(key, {}).get("state") == "done"


# ---------------------------------------------------------------------
# Story bible — the rolling per-series memory
# ---------------------------------------------------------------------

def load_bible(series_id: str) -> list[dict]:
    """
    Load the full accumulated bible for a series, in episode order.
    Used where multiple episodes' worth of context is needed. Not
    written to concurrently (extraction runs one episode at a time),
    so no lock needed on read.
    """
    try:
        return load_json(series_bible_path(series_id))["entries"]
    except FileNotFoundError:
        return []


def load_bible_entry(series_id: str, ep_num: int) -> dict:
    """
    Load ONE episode's bible entry directly, without pulling the whole
    series list. Purpose: the episode-POV recap feature only ever needs
    the target episode's own entry (not prior episodes), so this is a
    cheaper, more direct read than filtering load_bible(). Raises
    FileNotFoundError if extraction hasn't run for this episode yet —
    deliberately not swallowed, same reasoning as load_json().
    """
    return load_json(bible_entry_path(series_id, ep_num))


def append_to_bible(series_id: str, entry) -> None:
    """
    Add one episode's BibleEntry to the series' rolling bible. Called
    once per episode, from extraction.py. LOCKED, and safe even if
    extraction were ever parallelized across episodes later — the
    read-modify-write is now atomic w.r.t. other threads.
    Replaces rather than duplicates if the same episode is re-run
    (e.g. while iterating the extraction prompt).
    """
    path = series_bible_path(series_id)
    with _FILE_LOCK:
        try:
            data = load_json(path)
        except FileNotFoundError:
            data = {"series_id": series_id, "entries": []}

        data["entries"] = [
            e for e in data["entries"] if e["episode_num"] != entry.episode_num
        ]
        data["entries"].append(entry.model_dump())
        data["entries"].sort(key=lambda e: e["episode_num"])

        save_json(path, data)


def save_bible_entry(series_id: str, ep_num: int, entry) -> None:
    """
    Also write the entry to its own per-episode file. Purpose: lets the
    dashboard fetch a single episode's bible without loading and
    filtering the whole series list.
    """
    save_json(bible_entry_path(series_id, ep_num), entry.model_dump())


# ---------------------------------------------------------------------
# Character personality — evolving, per character, per episode
# ---------------------------------------------------------------------

def save_personality(series_id: str, character: str, personality) -> None:
    """Save a character's personality snapshot for the episode it reflects.

    Accepts either a Pydantic model or a plain dict (callers have used both).
    """
    if isinstance(personality, dict):
        data = personality
        ep = data["episode_num"]
    else:
        data = personality.model_dump() if hasattr(personality, "model_dump") else personality.dict()
        ep = personality.episode_num
    path = personality_dir(series_id, character) / f"personality_v{ep}.json"
    save_json(path, data)


def load_latest_personality(series_id: str, character: str, at_or_before_ep: int) -> dict | None:
    """
    Load the most recent personality snapshot for `character` at or
    before `at_or_before_ep`. Purpose: recap.py for episode 5 should use
    the character's personality AS OF episode 5 if it exists, or fall
    back to the latest one generated so far — never a FUTURE snapshot
    (that would leak how the character changes later). Returns None if
    no snapshot exists yet (e.g. this character's first appearance).
    """
    d = personality_dir(series_id, character)
    if not d.exists():
        return None
    candidates = []
    for p in d.glob("personality_v*.json"):
        ep = int(p.stem.replace("personality_v", ""))
        if ep <= at_or_before_ep:
            candidates.append(ep)
    if not candidates:
        return None
    latest_ep = max(candidates)
    return load_json(d / f"personality_v{latest_ep}.json")