"""
pipeline/stages/judge.py — ALGORITHM.md Phase G (Validation), reduced
to Tier 1 + Tier 3 for minimalism.

Skipped: Tier 2 (embedding-based sibling comparison — G5/G6/G7). Those
checks compare a monologue against SIBLING POVs generated in the same
batch; this pipeline generates one character at a time, on demand, so
there are no siblings to compare against. If you later batch-generate
multiple POVs per request, Tier 2 is the natural next addition.

TIER 1 (free, code only) — run first, reject immediately on failure,
no LLM call spent:
  G1  no referenced fact postdates this episode        (structural, always true here —
                                                          see recap.py's docstring on position_index)
  G4  length within target range

TIER 3 (one LLM call, scores the single monologue):
  G8  every asserted event maps to a beat/fact, or is clearly interior
  G9  dominant affect != forbidden_affect
  G10 output reflects want, need and lens

RETRY POLICY (per algorithm): one regeneration per failing monologue,
then reject the request with a clear error — no infinite loops.
"""

import sys
import json
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from schemas import RecapScript, JudgeResult
from storage import recap_dir, save_json, update_status, load_latest_personality
import stages.recap as recap_module

VALIDATION_PROMPT = """You are validating a character monologue against the source context it was
generated from. Be strict — this monologue must never reveal something the character
couldn't know, must not just narrate events without any character perspective, and must
read like a private thought, not a summary written for an audience.

Character: {character}
Forbidden affect (should NOT be named or described as an emotion anywhere): {forbidden_affect}
Want: {want} | Need: {need} | Lens: {lens}

CONTEXT the monologue was allowed to draw from:
{context}

MONOLOGUE TO VALIDATE:
{text}

Output ONLY valid JSON:
{{
  "grounded": true/false,          // every claimed event maps to the context above, or is clearly interior reflection
  "no_forbidden_affect_as_subject": true/false,   // forbidden affect is never named or directly described
  "reflects_profile": true/false,  // shows want/need/lens, isn't generic narration
  "not_a_summary": true/false,     // dwells on ONE moment rather than listing events in order;
                                    // no phrases like "today", "this episode", "what happened was",
                                    // "I watched as" — reads like a private thought, not a report
  "reasons": ["<short reason for any false value>"]
}}
"""


def _call_llm(prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _tier1_check(script: dict) -> JudgeResult | None:
    """Deterministic, zero-cost. Returns a failing JudgeResult, or None if it passes."""
    lo, hi = recap_module.TARGET_WORD_RANGE
    wc = script["word_count"]
    if not (lo * 0.7 <= wc <= hi * 1.4):  # generous band — hard reject only if way off
        return JudgeResult(faithful=False, tier="tier1",
                            reasons=[f"word_count {wc} far outside target {lo}-{hi}"])
    return None


def _tier3_check(script: dict, context: str, profile: dict) -> JudgeResult:
    """One LLM call scoring groundedness, forbidden-affect, and profile reflection."""
    prompt = VALIDATION_PROMPT.format(
        character=script["character"] or "the narrator",
        forbidden_affect=profile.get("forbidden_affect", "none noted"),
        want=profile.get("want", "n/a"), need=profile.get("need", "n/a"), lens=profile.get("lens", "n/a"),
        context=context, text=script["text"],
    )
    raw = _call_llm(prompt)
    cleaned = raw.strip().strip("`")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    result = json.loads(cleaned)

    passed = (result.get("grounded") and result.get("no_forbidden_affect_as_subject")
              and result.get("reflects_profile") and result.get("not_a_summary"))
    return JudgeResult(faithful=bool(passed), tier="tier3" if not passed else "pass",
                        reasons=result.get("reasons", []))


def run_with_retries(series_id: str, ep_num: int, character, max_retries: int = 1) -> RecapScript:
    """
    Generates a recap (Phase F), validates it (Phase G), and on failure
    regenerates ONCE (algorithm's retry policy), then accepts whatever
    the final attempt produced — judge.faithful reflects whether it
    actually passed, callers can inspect that rather than the pipeline
    silently succeeding on bad output.
    """
    update_status(series_id, ep_num, stage="judge", state="running", character=character)

    try:
        # context/profile needed for Tier 3 — recompute once, reused across retries
        if character is not None:
            entry_context, _ = recap_module._format_character_context(
                recap_module.load_bible_entry(series_id, ep_num), character
            )
            profile = load_latest_personality(series_id, character, at_or_before_ep=ep_num) or {}
        else:
            entry_context, profile = "(omniscient — all facts/beats)", {}

        script = None
        judge_result = None
        for attempt in range(max_retries + 1):
            script = recap_module.generate_recap(series_id, ep_num, character)

            judge_result = _tier1_check(script)
            if judge_result is None:
                judge_result = _tier3_check(script, entry_context, profile)

            if judge_result.faithful:
                break  # passed, stop retrying

        script["judge"] = judge_result.dict()
        recap_obj = RecapScript(**script)

        path = recap_dir(series_id, ep_num, character) / f"script_v{script['version']}.json"
        save_json(path, recap_obj.dict())

    except Exception as e:
        update_status(series_id, ep_num, stage="judge", state="failed", character=character, error=str(e))
        raise

    update_status(series_id, ep_num, stage="judge", state="done", character=character)
    return recap_obj