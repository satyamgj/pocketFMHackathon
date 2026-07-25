"""
pipeline/stages/asr.py — first stage in the pipeline.

Transcripts are generated BEFORE the hackathon (transcription is slow,
has zero live-demo value) — this stage loads and validates what's
already on disk rather than transcribing anything itself. Kept as a
full stage (not skipped entirely) so runner.py can treat all stages
uniformly instead of special-casing this one.
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))  # allow `from storage import ...`

from storage import transcript_path, load_json, update_status


class TranscriptNotFoundError(Exception):
    """Missing transcript.json for an episode — a clear, specific error
    instead of a confusing generic FileNotFoundError deep in the stack."""
    pass


class TranscriptEmptyError(Exception):
    """transcript.json exists but has no usable text — caught here
    instead of failing silently, later, inside extraction.py."""
    pass


def run_asr(series_id: str, ep_num: int) -> dict:
    """
    Load and validate the pre-generated transcript for one episode.

    Expected transcript.json shape:
    {
        "segments": [
            {"start": 0.0, "end": 4.2, "text": "...", "speaker": "SPEAKER_00"},
            ...
        ]
    }
    """
    update_status(series_id, ep_num, stage="asr", state="running")

    try:
        path = transcript_path(series_id, ep_num)
        try:
            transcript = load_json(path)
        except FileNotFoundError:
            raise TranscriptNotFoundError(
                f"No transcript.json for {series_id} ep {ep_num} at {path}. "
                f"Did you run ASR on this episode before the hackathon?"
            )

        segments = transcript.get("segments", [])
        if not segments:
            raise TranscriptEmptyError(
                f"transcript.json for {series_id} ep {ep_num} has no segments."
            )

        total_text_length = sum(len(seg.get("text", "")) for seg in segments)
        if total_text_length < 50:
            raise TranscriptEmptyError(
                f"transcript.json for {series_id} ep {ep_num} looks too short "
                f"({total_text_length} chars) — likely truncated or bad ASR output."
            )

    except (TranscriptNotFoundError, TranscriptEmptyError) as e:
        update_status(series_id, ep_num, stage="asr", state="failed", error=str(e))
        raise

    update_status(series_id, ep_num, stage="asr", state="done")
    return transcript


def get_transcript_slice(series_id: str, ep_num: int, end_seconds: float) -> str:
    """
    Return the opening N seconds of an episode's transcript as plain text.
    Kept for any feature that needs a time-bounded slice; the episode-POV
    retelling feature primarily uses get_character_lines() below instead.
    """
    transcript = load_json(transcript_path(series_id, ep_num))
    segments = transcript.get("segments", [])
    opening = [seg for seg in segments if seg.get("start", 0) <= end_seconds]
    return " ".join(seg.get("text", "").strip() for seg in opening).strip()


def get_character_lines(series_id: str, ep_num: int, character: str) -> str:
    """
    Return everything a character actually SAID in this episode, in
    order, as plain text.

    Purpose: personality-driven retelling shouldn't just paraphrase
    bible events — it should sound grounded in how this character
    actually talks. Their real transcript lines are the ground truth
    for voice; the bible's known_to-filtered events are the ground
    truth for what they know. recap.py's prompt uses both together.

    Matches on transcript speaker labels equal to the character name
    (case-insensitive) — this depends on transcripts having real
    character names as speakers (see scripts/convert_transcript.py),
    not generic SPEAKER_00 IDs.
    """
    transcript = load_json(transcript_path(series_id, ep_num))
    segments = transcript.get("segments", [])
    lines = [
        seg.get("text", "").strip()
        for seg in segments
        if seg.get("speaker", "").strip().lower() == character.strip().lower()
    ]
    return " ".join(lines).strip()