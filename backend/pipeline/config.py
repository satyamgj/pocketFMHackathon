"""
pipeline/config.py — per-series configuration, loaded from data files
instead of hardcoded in runner.py.

WHY THIS EXISTS: the pipeline itself (storage, extraction, recap, judge)
already takes series_id as a plain argument everywhere — it was never
tied to one story. The one place series-specific info DID live in code
was runner.py's DEFAULT_CHARACTERS / SERIES_START_EPISODE dicts. Moving
that into data/series_config/{series_id}.json means onboarding a new
Pocket FM series is a content operation (add a JSON file), not a code
change — which matters once this is a real product with many series,
not a single hackathon demo.

NEW IN THIS VERSION — auto-discovery of characters:
Previously, the character list in series_config/{series_id}.json was
fully manual — if a new character showed up in episode 3, someone had
to notice and hand-edit the JSON before that character would get a
perspective recap. discover_characters_from_bible() and
sync_characters_from_bible() close that gap: after extraction writes
each episode's bible entry, the runner calls sync_characters_from_bible()
to scan the accumulated bible and auto-add any character who has shown
up at least `min_appearances` times. See the docstring on
discover_characters_from_bible() for why that threshold exists.
"""

import sys
import json
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

from storage import DATA_DIR

SERIES_CONFIG_DIR = DATA_DIR / "series_config"


def load_series_config(series_id: str) -> dict:
    """
    Returns the config dict for a series, with sensible defaults if no
    config file exists yet — so the pipeline never hard-fails just
    because a series hasn't been formally onboarded.

    Expected shape (data/series_config/{series_id}.json):
    {
      "series_id": "mahabharata",
      "display_name": "Mahabharata",
      "start_episode": 1,
      "characters": ["Karna", "Arjuna"],
      "language": "en",
      "default_tone": "epic, mythic"
    }
    """
    path = SERIES_CONFIG_DIR / f"{series_id}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # Safe fallback: pipeline still runs (omniscient-only recap,
        # episode numbering starts at 1) even before a series has a
        # config file — useful the first time you're testing a brand
        # new series' content before deciding on POV characters.
        return {
            "series_id": series_id,
            "display_name": series_id,
            "start_episode": 1,
            "characters": [],
            "language": "en",
            "default_tone": None,
        }


def save_series_config(series_id: str, config: dict) -> None:
    """
    Writes/updates a series' config file. Called once when a new series
    is onboarded (e.g. from a small setup script or the dashboard, once
    Tech B builds a "new series" admin form) — and now ALSO called
    automatically by sync_characters_from_bible() whenever a new
    character qualifies for a POV recap.
    """
    path = SERIES_CONFIG_DIR / f"{series_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def characters_for(series_id: str) -> list:
    """
    Characters to generate perspective-recaps for, plus None
    (omniscient) as a baseline every series gets automatically.

    This function itself never changed for auto-discovery — it just
    reads whatever is currently in the config file. The magic is that
    sync_characters_from_bible() (below) keeps that file up to date, so
    by the time this runs for episode N, any character discovered in
    episodes 1..N-1 (or earlier in the same run) is already present.
    """
    config = load_series_config(series_id)
    return [None] + list(config.get("characters", []))


def start_episode_for(series_id: str) -> int:
    config = load_series_config(series_id)
    return config.get("start_episode", 1)


def discover_characters_from_bible(series_id: str, min_appearances: int = 2) -> set:
    """
    Scans every bible_entry.json written so far for this series (via
    the accumulated data/series/{series_id}/bible.json) and returns the
    set of character names whose narrative weight — how many events
    they're actually tied to, summed across every episode processed so
    far — meets or exceeds `min_appearances`.

    IMPORTANT — appearance counting is EVENT-level, not episode-level:
    A character tagged in 6 events within a single episode counts as 6,
    not 1. This matters a lot for testing on one episode at a time: a
    character who is central to episode 1 (many lines, many events)
    should be able to qualify for a POV recap immediately, without
    waiting for them to also show up in episode 2. Counting only
    "present in this episode: yes/no" would cap every character at +1
    per episode no matter how prominent they were within it — which
    silently makes min_appearances >= 2 impossible to satisfy from a
    single episode's bible, a confusing trap when testing one episode
    at a time.

    THE min_appearances NOISE TRADEOFF:
    Without a threshold, any character mentioned even once — a servant,
    a messenger, a background name dropped in one line of dialogue —
    would immediately qualify for their own perspective-recap. That
    means an LLM call + a TTS render generated for someone the audience
    will never care about, and a dashboard cluttered with recaps nobody
    asked for. Requiring 2+ event-level appearances (the default) is a
    cheap filter: minor one-off characters are still correctly captured
    inside the bible by extraction.py (nothing is lost), they just
    don't get "promoted" to a full POV character until they've been
    tied to enough of the story to plausibly deserve a voice of their
    own — whether that's several moments in one big episode, or a
    couple of episodes each with a single moment.

    Tuning guide:
      min_appearances=1  -> instant promotion, first tagged event. Flashier
                            for a live demo ("new character shows up,
                            immediately gets a recap voice") but noisier.
      min_appearances=2  -> default. A character needs to be tied to at
                            least 2 events (in one episode or spread
                            across several) before they're promoted.
      min_appearances=4+ -> conservative, for a long-running series where
                            you only want clearly central characters to
                            get their own recap track.

    This function is read-only — it never writes anything. Persisting
    the result is sync_characters_from_bible()'s job, below.

    UPDATED FOR THE FACT/BEAT SCHEMA (ALGORITHM.md Phase A): counts each
    time a character is an observer or misperceiver of a fact, or
    present in a beat — replacing the old characters/events shape.
    """
    bible_path = DATA_DIR / "series" / series_id / "bible.json"
    try:
        with open(bible_path, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except FileNotFoundError:
        return set()

    counts: dict[str, int] = {}

    for entry in entries:
        for fact in entry.get("facts", []) or []:
            for name in fact.get("observers", []) or []:
                counts[name] = counts.get(name, 0) + 1
            for mp in fact.get("misperceivers", []) or []:
                name = mp.get("char")
                if name:
                    counts[name] = counts.get(name, 0) + 1

        for beat in entry.get("beats", []) or []:
            for name in beat.get("present", []) or []:
                counts[name] = counts.get(name, 0) + 1

    return {name for name, count in counts.items() if count >= min_appearances}


def sync_characters_from_bible(series_id: str, min_appearances: int = 2) -> list:
    """
    Merges newly-qualified characters into the series config and
    persists it, so characters_for() picks them up on the very next
    call — no manual JSON editing required.

    WHEN TO CALL THIS: once per episode, right after extraction.py
    writes that episode's bible_entry.json, and BEFORE characters_for()
    is used to decide who gets a recap+judge run for that episode. See
    runner.py's run_episode() for the exact placement — it has to sit
    between the extraction stage and the recap/judge stage.

    Returns the full, current character list (existing + newly added)
    and prints what was newly added, so it's visible in the runner's
    console output rather than being a silent config mutation.
    """
    config = load_series_config(series_id)
    existing = set(config.get("characters", []))
    qualified = discover_characters_from_bible(series_id, min_appearances=min_appearances)

    newly_added = qualified - existing
    if newly_added:
        config["characters"] = sorted(existing | qualified)
        save_series_config(series_id, config)
        print(
            f"[config] {series_id}: auto-added {len(newly_added)} "
            f"character(s) after reaching {min_appearances}+ appearances: "
            f"{sorted(newly_added)}"
        )
        return config["characters"]

    return sorted(existing | qualified)