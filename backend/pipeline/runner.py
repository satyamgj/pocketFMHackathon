"""
pipeline/runner.py — wires all stages into one pipeline run per episode.

DESIGN, AND WHY IT'S "PRODUCTION-ALIKE" WITHOUT KAFKA:
- Episodes run SEQUENTIALLY, in order. Episode N's recap depends on
  episode N-1's bible entry existing, so this ordering is a correctness
  requirement, not just simplicity — don't parallelize across episodes.
- Within ONE episode, the multiple characters' recap+judge generation
  run CONCURRENTLY via a thread pool. This is safe because:
    (a) recap/judge stages for different characters write to different
        files (recap_dir is character-scoped — see storage.py)
    (b) the only shared file touched during recap/judge is status.json,
        which is lock-protected in storage.py
    (c) the work is I/O-bound (waiting on the Anthropic API), so
        threading gives a real wall-clock speedup with no GIL penalty
  This is a deliberate, cheap concurrency win — no queue, no workers,
  no message broker — appropriate for a 24h hackathon while still being
  a genuine engineering decision worth explaining to judges.
- Idempotent: re-running after a crash/interrupt SKIPS stages already
  'done' in status.json. This is what makes live-demo recovery just
  "run it again" instead of a full restart.
- render.py (Tech B's) is imported lazily and skipped gracefully if
  not yet available, so Tech A's side is fully testable standalone.

NEW — AUTO CHARACTER DISCOVERY:
Right after extraction writes an episode's bible entry, the runner now
calls config.sync_characters_from_bible() before deciding who gets a
recap. This means a character introduced in episode 3 automatically
gets their own perspective-recap starting episode 3, once they've
appeared enough times to clear the min_appearances noise filter (see
config.py). No manual edit to series_config/{series_id}.json required.

NEW — SINGLE-CHARACTER TARGETING (--character):
Pass --character NAME to skip every other character entirely and only
generate/judge/render that one character's perspective. Also runs
outside the thread pool so a real exception (with full traceback)
propagates instead of being flattened to str(e) — much easier to debug
a single stage failure with than re-running all N characters at once.
"""

import re
import sys
import glob
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append(str(Path(__file__).resolve().parent))

from storage import stage_done, update_status, recap_dir, transcript_path, save_json
from stages.asr import run_asr
from stages.extraction import extract_bible_entry
from stages.judge import run_with_retries as generate_and_judge_recap
from stages.personality import generate_personality
from config import characters_for, start_episode_for, sync_characters_from_bible

# NOTE: series-specific settings (which characters, which episode a
# series starts at) are NOT hardcoded here — they live in
# data/series_config/{series_id}.json, loaded via config.py. This is
# what makes the pipeline work for ANY Pocket FM series, not just one
# hackathon demo story: onboarding a new series is "add a JSON file",
# not "edit runner.py". See pipeline/config.py for the file shape, and
# for how the character list now keeps itself up to date automatically.


def first_episode_in_series(series_id: str) -> int:
    return start_episode_for(series_id)


def _run_recap_and_judge_for_character(series_id: str, ep_num: int, character):
    """
    One unit of concurrent work per character: generate/evolve their
    personality snapshot for this episode FIRST (recap needs it to
    voice the retelling), then generate + judge the retelling itself.
    character=None (omniscient) skips personality entirely — there's no
    single voice to evolve for an all-perspectives recap.
    """
    if character is not None:
        generate_personality(series_id, ep_num, character)
    script = generate_and_judge_recap(series_id, ep_num, character=character)
    return character, script


def _run_render_for_character(series_id: str, ep_num: int, character):
    """
    Calls Tech B's render_recap on the latest script version for one
    character, if render.py exists yet. Returns True if rendered,
    False if skipped (no script yet or render.py unavailable).
    """
    try:
        from render import render_recap  # Tech B's function
    except ImportError:
        return False

    existing = sorted(glob.glob(str(recap_dir(series_id, ep_num, character) / "script_v*.json")))
    if not existing:
        return False

    with open(existing[-1]) as f:
        latest = json.load(f)

    update_status(series_id, ep_num, stage="render", state="running", character=character)
    render_recap(latest["text"], series_id, ep_num, latest["version"], character=character)
    update_status(series_id, ep_num, stage="render", state="done", character=character)
    return True


_TIMESTAMP_RE = re.compile(r"^—\s*(\d+):(\d{2})\s*—$")
_DIALOGUE_LINE_RE = re.compile(r"^([A-Z][A-Z0-9 ]*?)\s*\(([^)]*)\):\s*(.+)$")
_WORDS_PER_MINUTE = 150  # rough spoken-narration pace, used only to estimate segment durations


def generate_transcript_from_txt(series_id: str, ep_num: int, txt_path: str,
                                  force: bool = False) -> Path:
    """
    Converts a raw episode script (.txt) into transcript.json, in the
    exact shape and location every other stage expects (via storage.py's
    transcript_path()/save_json() — the same functions asr.py itself
    reads through, so there's no risk of this writing to a different
    path than the rest of the pipeline looks for).

    EXPECTED .txt FORMAT — dialogue-only script with optional timestamp
    headers and stage directions in parentheses:

        — 0:00 —
        ARJUNA (shouting over noise): Fall back to the second line.
        BHIMA (heavy, breathing hard): Brother. Look at the ground.

        — 1:30 —
        KRISHNA (calm): He cannot be beaten while he is holding that bow.

    Parsing rules:
      - "— MM:SS —" lines set the current timestamp for every line that
        follows, until the next timestamp marker. Optional — no headers
        means everything is treated as one block starting at 0:00.
      - "SPEAKER (stage direction): text" lines become one segment each.
        The parenthetical becomes a `note` field.
      - Consecutive lines are spaced ~2s apart within a timestamp block
        so no two segments collide on an identical start time.
      - Lines that don't match either pattern are skipped, not errored.

    Returns the path the transcript was written to. Skips re-parsing if
    transcript.json already exists for this episode, unless force=True.
    """
    out_path = transcript_path(series_id, ep_num)
    if out_path.exists() and not force:
        print(f"[transcript] {out_path} already exists, skipping re-parse "
              f"(pass force=True / --force to overwrite)")
        return out_path

    raw_text = Path(txt_path).read_text(encoding="utf-8")

    segments = []
    current_time = 0.0
    last_speaker = None
    speaker_offset = 0.0

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue

        ts_match = _TIMESTAMP_RE.match(line)
        if ts_match:
            minutes, seconds = int(ts_match.group(1)), int(ts_match.group(2))
            current_time = minutes * 60 + seconds
            last_speaker = None
            speaker_offset = 0.0
            continue

        line_match = _DIALOGUE_LINE_RE.match(line)
        if not line_match:
            continue  # not a recognizable dialogue line — skip, don't error

        speaker, note, text = line_match.groups()
        speaker, note, text = speaker.strip(), note.strip(), text.strip()
        if not text:
            continue

        speaker_offset += 2.0 if last_speaker is not None else 0.0
        start = round(current_time + speaker_offset, 1)

        word_count = len(text.split())
        duration = max(1.5, (word_count / _WORDS_PER_MINUTE) * 60)

        segments.append({
            "start": start,
            "end": round(start + duration, 1),
            "text": text,
            "speaker": speaker,
            "note": note,
        })
        last_speaker = speaker

    if not segments:
        raise ValueError(
            f"No dialogue lines parsed from {txt_path} — check the file matches "
            f"the expected 'SPEAKER (note): text' format."
        )

    save_json(out_path, {
        "series_id": series_id,
        "episode_num": ep_num,
        "segments": segments,
    })

    speakers = sorted(set(s["speaker"] for s in segments))
    print(f"[transcript] parsed {len(segments)} segments, {len(speakers)} "
          f"speaker(s) {speakers} -> {out_path}")
    return out_path


def run_episode(series_id: str, ep_num: int, force: bool = False,
                 max_workers: int = 4, min_appearances: int = 2,
                 from_txt: str = None, only_character: str = "__ALL__") -> dict:
    """
    Runs every stage for one episode. asr and extraction run once
    (character-agnostic). Right after extraction, the character list is
    auto-synced from the bible (see sync_characters_from_bible). Then
    recap+judge (and then render) run once PER CHARACTER, concurrently
    via a thread pool.

    min_appearances: passed straight through to
    config.sync_characters_from_bible() — how many times a character
    must appear in the bible before they're auto-promoted to getting
    their own perspective-recap.

    from_txt: optional path to a raw episode .txt script. If given,
    generate_transcript_from_txt() runs FIRST, before Stage 1 (ASR).

    only_character: if set (not the default "__ALL__" sentinel), skips
    every character except this one for Stage 3+4 (recap+judge) and
    Stage 5 (render) — asr/extraction/sync still run normally. Pass the
    exact name as it appears in series_config (case-sensitive), or
    "omniscient" for the all-perspectives baseline (character=None
    internally).
    """
    summary = {"series_id": series_id, "episode_num": ep_num,
               "stages_run": [], "stages_skipped": [], "characters": {}}

    # --- Stage 0 (optional): raw .txt -> transcript.json ---
    if from_txt:
        transcript_path_written = generate_transcript_from_txt(
            series_id, ep_num, from_txt, force=force
        )
        summary["stages_run"].append(f"transcript_from_txt -> {transcript_path_written}")

    # --- Stage 1: ASR ---
    if not force and stage_done(series_id, ep_num, "asr"):
        summary["stages_skipped"].append("asr")
    else:
        run_asr(series_id, ep_num)
        summary["stages_run"].append("asr")

    # --- Stage 2: Extraction ---
    if not force and stage_done(series_id, ep_num, "extraction"):
        summary["stages_skipped"].append("extraction")
    else:
        extract_bible_entry(series_id, ep_num)
        summary["stages_run"].append("extraction")

    # --- Auto-sync character list from the bible, right after
    # extraction and BEFORE the recap/judge block below builds its
    # character list via characters_for(). ---
    synced_characters = sync_characters_from_bible(series_id, min_appearances=min_appearances)
    summary["characters_in_config"] = synced_characters

    # --- Stage 3+4: Recap + Judge, per character, CONCURRENT (unless
    # only_character narrows this to exactly one) ---
    all_characters = characters_for(series_id)
    if only_character != "__ALL__":
        target = None if only_character == "omniscient" else only_character
        if target not in all_characters:
            raise ValueError(
                f"'{only_character}' is not in series_config characters {all_characters}. "
                f"Check spelling/case, or run without --character to see the full list."
            )
        all_characters = [target]

    chars_to_run = [
        c for c in all_characters
        if force or not stage_done(series_id, ep_num, "judge", character=c)
    ]
    if not chars_to_run:
        summary["stages_skipped"].append("recap+judge (all characters already done)")
    else:
        # When scoped to a single character, skip the thread pool and call
        # directly — this lets a real exception propagate with its full
        # traceback instead of being flattened to str(e), which matters
        # a lot while debugging a single stage failure.
        if len(chars_to_run) == 1 and only_character != "__ALL__":
            character = chars_to_run[0]
            char, script = _run_recap_and_judge_for_character(series_id, ep_num, character)
            summary["characters"][char or "omniscient"] = {
                "version": script.version,
                "judge_passed": script.judge.faithful if script.judge else None,
            }
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_run_recap_and_judge_for_character, series_id, ep_num, c): c
                    for c in chars_to_run
                }
                for future in as_completed(futures):
                    character = futures[future]
                    try:
                        char, script = future.result()
                        summary["characters"][char or "omniscient"] = {
                            "version": script.version,
                            "judge_passed": script.judge.faithful if script.judge else None,
                        }
                    except Exception as e:
                        summary["characters"][character or "omniscient"] = {"error": str(e)}
        summary["stages_run"].append(f"recap+judge x{len(chars_to_run)} characters")

    # --- Stage 5: Render, per character (optional, Tech B's code) ---
    rendered_any = False
    for character in all_characters:
        if not force and stage_done(series_id, ep_num, "render", character=character):
            continue
        if _run_render_for_character(series_id, ep_num, character):
            rendered_any = True
    if rendered_any:
        summary["stages_run"].append("render")
    else:
        summary["stages_skipped"].append("render (no script yet or render.py unavailable)")

    return summary


def run_series(series_id: str, episode_numbers: list, force: bool = False,
               min_appearances: int = 2, from_txt: str = None,
               only_character: str = "__ALL__") -> list:
    """
    Runs multiple episodes IN ORDER (sequential — episode N's recap
    depends on episode N-1's bible entry existing). One bad episode
    doesn't abort the rest.

    from_txt: only really makes sense with a single episode in practice
    — if you pass --eps with multiple episode numbers alongside
    --from-txt, the SAME file gets parsed into every episode number
    given, which is almost certainly not what you want. Prefer --ep
    (singular) + --from-txt for raw-script runs.
    """
    results = []
    for ep_num in sorted(episode_numbers):
        print(f"--- Running {series_id} episode {ep_num} ---")
        try:
            result = run_episode(series_id, ep_num, force=force,
                                  min_appearances=min_appearances, from_txt=from_txt,
                                  only_character=only_character)
            print(f"  ran={result['stages_run']} skipped={result['stages_skipped']} "
                  f"characters={list(result['characters'].keys())}")
            results.append(result)
        except Exception as e:
            print(f"  FAILED at episode {ep_num}: {e}")
            results.append({"series_id": series_id, "episode_num": ep_num, "error": str(e)})
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the recap pipeline for one or more episodes.")
    parser.add_argument("--series", required=True, help="series_id")
    parser.add_argument("--ep", type=int, help="single episode number to run")
    parser.add_argument("--eps", type=int, nargs="+", help="multiple episode numbers to run in order")
    parser.add_argument("--force", action="store_true", help="re-run stages even if already marked done")
    parser.add_argument("--min-appearances", type=int, default=2,
                         help="min bible appearances before a character is auto-added for a POV recap (default: 2)")
    parser.add_argument("--from-txt", type=str, default=None,
                         help="path to a raw episode .txt script — converted to transcript.json "
                              "before Stage 1 runs")
    parser.add_argument("--character", type=str, default="__ALL__",
                         help="generate the recap for just ONE character (exact name as in "
                              "series_config, or 'omniscient' for the all-perspectives baseline). "
                              "Default: all characters in config.")
    args = parser.parse_args()

    if args.ep is not None:
        result = run_episode(args.series, args.ep, force=args.force,
                              min_appearances=args.min_appearances, from_txt=args.from_txt,
                              only_character=args.character)
        print(json.dumps(result, indent=2, default=str))
    elif args.eps:
        run_series(args.series, args.eps, force=args.force,
                   min_appearances=args.min_appearances, from_txt=args.from_txt,
                   only_character=args.character)
    else:
        parser.error("Provide either --ep N or --eps N N N ...")