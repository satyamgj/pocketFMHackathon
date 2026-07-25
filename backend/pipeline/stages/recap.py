"""
pipeline/stages/recap.py — ALGORITHM.md Phase E (Context Assembly) +
Phase F (Composition).

PHASE E RULE THAT MATTERS MOST: "Filter before composition, never
after." Everything the model sees is assembled in plain Python from
the fact/beat store BEFORE any LLM call — a model cannot unlearn a
fact placed in its context, so leakage has to be prevented at
assembly time, not caught afterward by a judge.

Skipped by design (see ALGORITHM.md Phase D / peer_gists): sibling-POV
divergence framing. This file generates ONE character's monologue at a
time for an on-demand API — there's no batch of siblings to diverge
from. If you later add "generate 3 POVs for this scene at once", Phase
D's cast selection and peer_gists come back into play.
"""

import sys
import json
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from schemas import BibleEntry
from storage import load_bible_entry, load_latest_personality, recap_dir, save_json

TARGET_WORD_RANGE = (150, 250)  # per algorithm: 150-250 words per minute of audio


# ---------------------------------------------------------------------
# Phase E — Context Assembly (pure Python, no LLM, no source text read
# beyond the already-extracted fact/beat store)
# ---------------------------------------------------------------------

def _format_character_context(entry: dict, character: str):
    """
    Filters the episode's facts/beats down to exactly what `character`
    could know, per algorithm steps E1-E5. Returns (context_text, state)
    — kept as a 2-tuple for backward compatibility with personality.py,
    which calls this same function to build its own prompt.

    position_index is implicitly `entry["episode_num"]` here — this
    function only ever sees ONE episode's entry, so "facts established
    at or before this episode" is automatically satisfied by only
    reading from this entry. (A multi-episode version would take
    position_index explicitly and filter across the accumulated
    series bible — noted as a natural extension, not built here to
    keep this minimal.)
    """
    facts = entry.get("facts", [])
    beats = entry.get("beats", [])
    position_index = entry.get("episode_num")

    # E1 — knows
    knows = [f for f in facts if character in f.get("observers", [])
              and f.get("established_at", 0) <= position_index]

    # E2 — believes_falsely
    believes_falsely = []
    for f in facts:
        for mp in f.get("misperceivers", []):
            if mp.get("char") == character:
                believes_falsely.append({"claim": f["claim"], "believes": mp["believes"]})

    # E3 — does_not_know (scene beats where this character perceived nothing)
    does_not_know_beats = [b["summary"] for b in beats
                             if b.get("perceived_by", {}).get(character) == "none"]

    # E4 — visible_beats, rendered at the resolution the character perceived them
    visible_beats = [b for b in beats if b.get("perceived_by", {}).get(character, "none") != "none"]

    if not knows and not visible_beats:
        raise ValueError(f"'{character}' has no knows or visible_beats this episode")

    # E5 — writable = silences in visible beats (what the text didn't record
    # for this character — the space the monologue is allowed to fill in)
    writable = []
    for b in visible_beats:
        if character in b.get("present", []):
            writable.extend(b.get("silences", []))

    lines = ["WHAT THEY KNOW (true):"]
    lines += [f"- {f['claim']}" for f in knows] or ["- (nothing directly confirmed)"]
    if believes_falsely:
        lines.append("\nWHAT THEY BELIEVE (false — render as true to them, do not correct it):")
        lines += [f"- {b['claim']} -> they believe: {b['believes']}" for b in believes_falsely]
    lines.append("\nBEATS THEY WITNESSED (at their perception level):")
    for b in visible_beats:
        level = b.get("perceived_by", {}).get(character, "full")
        lines.append(f"- ({level}) {b['summary']}")
    if does_not_know_beats:
        lines.append("\nHAPPENED BUT THEY DID NOT PERCEIVE (never reference these):")
        lines += [f"- {s}" for s in does_not_know_beats]
    if writable:
        lines.append("\nUNRECORDED MOMENTS YOU MAY FILL IN (their private reaction, not new events):")
        lines += [f"- {w}" for w in writable]

    context_text = "\n".join(lines)
    state = f"present in {len(visible_beats)} beat(s), knows {len(knows)} fact(s)"
    return context_text, state


# ---------------------------------------------------------------------
# Phase F — Composition
# ---------------------------------------------------------------------

COMPOSITION_PROMPT = """You are {character}, alone, thinking to yourself right after this episode's
events — not narrating them to an audience. Nobody is being told a story. This is what's actually
looping in your head.

PROFILE:
Want: {want}
Need (opposes want — let it show through what you fixate on, don't state it): {need}
Wound (the old injury this reopens — let it surface, don't explain it): {wound}
Lens (your distortion — you don't know you have it, so don't name it, just filter through it): {lens}
Preoccupation (may intrude where it shouldn't): {preoccupation}
Voice: {voice_tempo}. {voice_diction}. Verbal tells you actually use: {voice_tells}
Forbidden affect: {forbidden_affect} — you may be FEELING this. You must never NAME it, describe
it as an emotion, or use a synonym for it. It should be visible only in what you fixate on, avoid,
or circle back to.

WHAT YOU KNOW AND WITNESSED THIS EPISODE (your only source material):
{context}

HOW TO WRITE THIS — hard rules:
1. PICK ONE THING. Do not cover every beat/fact above in order — that produces a report, not a
   mind. Choose the ONE moment from the context that you cannot stop circling back to. Spend most
   of the monologue there. Mention the rest only if it intrudes on that one thing, in passing,
   out of order, the way a real thought would surface it.
2. DO NOT SUMMARIZE OR RECAP. Never write phrases like "today", "this episode", "what happened
   was", "I watched as", "in that moment". You already know what happened — you're not explaining
   it to anyone, you're stuck inside it.
3. START MID-THOUGHT. Open on a physical sensation, a specific image, or a half-finished
   reaction — not a scene-setting sentence. Someone reading the first line should feel dropped
   into a mind already in motion.
4. USE YOUR VOICE TELLS. If you have verbal tells, at least one must actually appear, not be
   described.
5. LET WANT AND NEED PULL AGAINST EACH OTHER without resolving it — the monologue should feel
   like it's arguing with itself, not arriving at a conclusion.
6. Nothing outside "WHAT YOU KNOW AND WITNESSED" exists to you. Render any false belief listed
   above as simply true — do not hedge, doubt, or correct it.

FORM: {min_words}-{max_words} words. First person, present tense, spoken register — this gets
read aloud. End mid-feeling, unresolved — not a wrap-up, not a moral, not a final realization.

Output ONLY the monologue. No preamble, no headers, no quotation marks.
"""


def _call_llm(prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0.7,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def _next_version(series_id: str, ep_num: int, character) -> int:
    import glob
    existing = glob.glob(str(recap_dir(series_id, ep_num, character) / "script_v*.json"))
    return len(existing) + 1


def generate_recap(series_id: str, ep_num: int, character) -> dict:
    """
    character: a name string, or None for omniscient (renders every
    beat/fact with no filtering — the all-perspectives baseline).
    Returns a plain dict matching schemas.RecapScript (unsaved — judge.py
    saves the final, judged version).
    """
    entry = load_bible_entry(series_id, ep_num)

    if character is None:
        facts = entry.get("facts", [])
        beats = entry.get("beats", [])
        context = "ALL FACTS:\n" + "\n".join(f"- {f['claim']}" for f in facts)
        context += "\n\nALL BEATS:\n" + "\n".join(f"- {b['summary']}" for b in beats)
        prompt = COMPOSITION_PROMPT.format(
            character="the narrator", context=context,
            want="give a complete, unbiased account", need="n/a",
            wound="n/a", lens="omniscient — no distortion",
            preoccupation="n/a", voice_tempo="clear, unhurried",
            voice_diction="neutral, narrative", voice_tells="none",
            forbidden_affect="n/a",
            min_words=TARGET_WORD_RANGE[0], max_words=TARGET_WORD_RANGE[1],
        )
        # omniscient has no single perspective to distort — swap the
        # single-focal-point rule for full, ordered coverage instead.
        prompt = prompt.replace(
            "1. PICK ONE THING. Do not cover every beat/fact above in order — that produces a "
            "report, not a mind. Choose the ONE moment from the context that you cannot stop "
            "circling back to. Spend most of the monologue there. Mention the rest only if it "
            "intrudes on that one thing, in passing, out of order, the way a real thought would "
            "surface it.",
            "1. Cover the episode's key beats in order, clearly and without bias toward any one character.",
        )
    else:
        context, _state = _format_character_context(entry, character)
        profile = load_latest_personality(series_id, character, at_or_before_ep=ep_num) or {}
        voice = profile.get("voice", {})
        prompt = COMPOSITION_PROMPT.format(
            character=character,
            want=profile.get("want", "unclear"),
            need=profile.get("need", "unclear"),
            wound=profile.get("wound", "none noted"),
            lens=profile.get("lens", "no strong bias"),
            preoccupation=profile.get("preoccupation", "none noted"),
            voice_tempo=voice.get("tempo", "measured"),
            voice_diction=voice.get("diction", "plain"),
            voice_tells=", ".join(voice.get("tells", [])) or "none noted",
            forbidden_affect=profile.get("forbidden_affect", "none noted"),
            context=context,
            min_words=TARGET_WORD_RANGE[0], max_words=TARGET_WORD_RANGE[1],
        )

    text = _call_llm(prompt)
    return {
        "series_id": series_id,
        "episode_num": ep_num,
        "character": character,
        "version": _next_version(series_id, ep_num, character),
        "text": text,
        "word_count": len(text.split()),
    }