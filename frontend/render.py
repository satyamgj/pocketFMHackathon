"""
render.py — Tech B's core engine.

Four jobs, all tested and working:
  1. generate_narration()   text -> spoken mp3 (OpenAI TTS, with silent fallback)
  2. mix_with_music()       narration + music bed -> normalized, trimmed mp3
                             (trim length is now estimated per-script from
                             word count via estimate_narration_seconds() —
                             no more fixed 30s cutoff, see that function)
  3. stitch_into_episode()  recap -> crossfades into the real episode's opening
  4. render_episode()       does #1 + #2, saves audio_vN.mp3, updates status.json
                             <- this is the function the dashboard's button calls

Quick manual test (works even without a valid OPENAI_API_KEY — see FORCE_MOCK_TTS):
    python render.py --test

CHARACTER-POV SUPPORT (new):
  render_episode() takes an optional `character` param. Pass it as
  `episode_id=f"{episode}/{character}"` from the dashboard (composite id) —
  this alone gives you separate folders per character with zero other
  changes. Passing `character` explicitly (not just baked into episode_id)
  additionally picks a distinct TTS voice per character via CHARACTER_VOICES
  below, so two POVs sound different, not just read different.

RESOLVED (was "confirm with Tech A" — now settled against the live API in
pipeline/api.py, see tech_a_client.py for the full contract):
  - There's no shared /data folder with Tech A after all — Tech A serves
    everything over a FastAPI service (localhost:8000). render.py's own
    DATA_DIR below is purely Tech B's local audio output, unrelated to
    Tech A's storage.
  - Tech A's script text is the "text" field on the /pov endpoint's JSON
    response — dashboard.py fetches it and passes it straight into
    render_episode()'s `text` param. render.py itself still doesn't call
    Tech A's API directly; that stays dashboard.py's job.
  - Tech A's judge is a single "judge_passed" bool per script version, NOT
    a dict of named scores — there's no "perspective_consistent" key.
    dashboard.py records judge_passed/version/cached into status.json
    itself (right after calling render_episode()), since this file
    deliberately has no knowledge of Tech A's API.
"""
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import edge_tts
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # picks up OPENAI_API_KEY from a .env file if present

# ---------------------------------------------------------------
# CONFIG — the things you'll actually tweak during the hackathon
# ---------------------------------------------------------------
DATA_DIR = Path("data")                     # shared data folder — confirm path with Tech A
MUSIC_BED = Path("assets/music_bed.mp3")    # drop your chosen royalty-free track here
TTS_ENGINE = "openai"                       # "edge" (free, no key) or "openai" (needs billing)
TTS_VOICE = "en-IN-PrabhatNeural"           # edge-tts fallback only, unused while TTS_ENGINE = "openai"
TTS_MODEL = "gpt-4o-mini-tts"
TARGET_SECONDS = 30                         # fallback/ceiling only now — see estimate_narration_seconds() below
FORCE_MOCK_TTS = False                      # flip to True to skip real TTS entirely, on purpose

# Gender-separated OpenAI voice pools. Of these, onyx/ash/ballad/nova/verse
# were the ones actually A/B tested in test_tts.py against real dialogue —
# echo/fable/shimmer/coral/sage/alloy are OpenAI's other documented voices,
# untested here. Do a quick listen pass on those before locking in a
# character to one, same as you did for the original five.
MALE_VOICES = ["onyx", "ash", "verse", "echo", "fable"]
FEMALE_VOICES = ["nova", "ballad", "shimmer", "coral", "sage"]
NEUTRAL_VOICE = "alloy"  # narrator / crowd / anything without a single speaker identity

# Default OpenAI voice for any character NOT listed in CHARACTER_VOICES below
# (i.e. a new character Tech A's bible discovers that hasn't been added here yet).
OPENAI_TTS_VOICE = NEUTRAL_VOICE

# Per-character voice map. KEYS ARE LOWERCASE — the lookup in render_episode()
# lowercases the incoming character name before checking this dict. This
# fixes a real bug: Tech A's API returns Title-Case names ("Yudhishthira",
# "Drona"...), so a case-sensitive lookup against these lowercase keys was
# missing on every call and silently falling back to the plain default
# voice — which is why "OpenAI seemed to work for certain characters" only:
# every character was actually hitting the same fallback, never their
# intended distinct voice.
#
# "voice"/"rate"/"pitch" below are edge-tts-only controls, kept only for the
# original 4 in case you ever switch TTS_ENGINE back to "edge". "openai_voice"
# is what actually gets used while TTS_ENGINE = "openai". "eq_preset" (ffmpeg
# post-processing) applies under either engine.
CHARACTER_VOICES = {
    # --- male cast ---
    "yudhishthira": {
        "gender": "male", "openai_voice": "onyx", "eq_preset": "dry_clean",
        "voice": "en-IN-PrabhatNeural", "rate": "-12%", "pitch": "+0Hz",
    },
    "drona": {
        "gender": "male", "openai_voice": "ash", "eq_preset": "aged_grain",
        "voice": "en-IN-PrabhatNeural", "rate": "-18%", "pitch": "-8Hz",
    },
    "dhrishtadyumna": {
        "gender": "male", "openai_voice": "verse", "eq_preset": "cold_metallic",
        "voice": "en-IN-PrabhatNeural", "rate": "-8%", "pitch": "-4Hz",
    },
    "ashwatthama": {
        "gender": "male", "openai_voice": "echo", "eq_preset": "bright_forward",
        "voice": "en-IN-PrabhatNeural", "rate": "+8%", "pitch": "+8Hz",
    },
    "arjuna": {"gender": "male", "openai_voice": "fable", "eq_preset": "none"},
    "bhima": {"gender": "male", "openai_voice": "onyx", "eq_preset": "cold_metallic"},
    "krishna": {"gender": "male", "openai_voice": "onyx", "eq_preset": "bright_forward"},
    "duryodhana": {"gender": "male", "openai_voice": "ash", "eq_preset": "cold_metallic"},
    "bhishma": {"gender": "male", "openai_voice": "fable", "eq_preset": "aged_grain"},
    "dhritarashtra": {"gender": "male", "openai_voice": "verse", "eq_preset": "dry_clean"},
    "sage": {"gender": "male", "openai_voice": "echo", "eq_preset": "none"},

    # --- female cast ---
    "draupadi": {"gender": "female", "openai_voice": "nova", "eq_preset": "bright_forward"},

    # --- non-character narration ---
    "omniscient": {"gender": "neutral", "openai_voice": NEUTRAL_VOICE, "eq_preset": "none"},
    "soldiers nearby": {"gender": "neutral", "openai_voice": NEUTRAL_VOICE, "eq_preset": "cold_metallic"},
}
DEFAULT_RATE = "+0%"
DEFAULT_PITCH = "+0Hz"
DEFAULT_EQ_PRESET = "none"

# Post-processing presets applied to the narration track before mixing.
# Applied in mix_with_music() via ffmpeg's -af / filter_complex.
# These approximate texture/warmth differences edge-tts itself can't do —
# tune the gain/frequency numbers by ear, they're a starting point, not gospel.
EQ_PRESETS = {
    "none": "anull",
    # Yudhishthira: dry, clean, almost no resonance — gentle highpass only
    "dry_clean": "highpass=f=80",
    # Drona: aged, low, slightly duller — softened gains vs first pass
    "aged_grain": "equalizer=f=200:t=q:w=1:g=3,treble=g=-2:f=6000,aecho=0.5:0.3:25:0.08",
    # Dhrishtadyumna: hard, metallic, no warmth — softened gains vs first pass
    "cold_metallic": "highpass=f=120,equalizer=f=1000:t=q:w=1:g=2,equalizer=f=300:t=q:w=1:g=-2",
    # Ashwatthama: bright, forward, energetic — softened gain vs first pass
    "bright_forward": "highpass=f=100,equalizer=f=3000:t=q:w=1:g=2",
}

_openai_client = None  # created lazily — only touched if TTS_ENGINE = "openai" is actually used


def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()  # reads OPENAI_API_KEY from environment / .env
    return _openai_client


def _mock_narration(text: str, out_path: Path) -> Path:
    """
    Silent placeholder audio, timed to roughly match real narration length
    (~150 words/minute) so mixing/timing/dashboard tests stay realistic even
    when the TTS API is down or you don't have a key yet.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    words = max(len(text.split()), 1)
    seconds = max(round(words / 150 * 60), 3)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(seconds),
        "-q:a", "2",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


async def _edge_tts_generate(text: str, out_path: Path, voice: str, rate: str, pitch: str) -> None:
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(str(out_path))


def _generate_edge(text: str, out_path: Path, voice: str, rate: str, pitch: str) -> Path:
    """Free, no-key TTS via Microsoft's edge-tts. Needs internet, no billing."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(_edge_tts_generate(text, out_path, voice, rate, pitch))
    return out_path


def _generate_openai(text: str, out_path: Path, voice: str) -> Path:
    """Paid OpenAI TTS — only used if TTS_ENGINE = 'openai' and billing is set up."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    client = _get_openai_client()
    with client.audio.speech.with_streaming_response.create(
        model=TTS_MODEL,
        voice=voice,
        input=text,
        instructions=(
            "Speak like a dramatic trailer narrator: urgent, warm, building "
            "tension toward the end. Not robotic or flat."
        ),
    ) as response:
        response.stream_to_file(out_path)
    return out_path


def generate_narration(text: str, out_path: Path, voice: str = TTS_VOICE,
                        rate: str = DEFAULT_RATE, pitch: str = DEFAULT_PITCH) -> Path:
    """Text -> mp3 narration file. Tries the configured engine first, falls
    back to a silent placeholder if it fails — never hard-fails."""
    if FORCE_MOCK_TTS:
        print("[mock] FORCE_MOCK_TTS is on — generating silent placeholder")
        return _mock_narration(text, out_path)
    try:
        if TTS_ENGINE == "openai":
            return _generate_openai(text, out_path, voice)
        else:
            return _generate_edge(text, out_path, voice, rate, pitch)
    except Exception as e:
        print(f"[warning] {TTS_ENGINE} TTS failed ({e}) — using silent placeholder instead")
        return _mock_narration(text, out_path)


def mix_with_music(narration_path: Path, music_path: Path, out_path: Path,
                    target_seconds: int = TARGET_SECONDS, eq_preset: str = DEFAULT_EQ_PRESET) -> Path:
    """Narration + music bed -> ducked, normalized, trimmed final mp3.

    eq_preset selects a post-processing chain from EQ_PRESETS (see above) —
    approximates per-character texture (aged, metallic, bright...) that
    edge-tts's rate/pitch alone can't produce. "none" = no processing.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    voice_fx = EQ_PRESETS.get(eq_preset, "anull")

    if not music_path.exists():
        print(f"[warning] music bed not found at {music_path} — using narration only")
        cmd = [
            "ffmpeg", "-y",
            "-i", str(narration_path),
            "-af", f"{voice_fx},loudnorm=I=-16:TP=-1.5:LRA=11",
            "-t", str(target_seconds),
            str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return out_path

    filter_complex = (
        f"[0:a]{voice_fx}[voice_fx];"
        "[1:a]volume=0.22[music_low];"
        "[voice_fx][music_low]amix=inputs=2:duration=first:dropout_transition=2[mixed];"
        "[mixed]loudnorm=I=-16:TP=-1.5:LRA=11[out]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(narration_path),
        "-i", str(music_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", str(target_seconds),
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def stitch_into_episode(recap_path: Path, episode_audio_path: Path, out_path: Path,
                         crossfade_seconds: float = 1.5) -> Path:
    """Crossfades the recap directly into the real episode's opening line,
    instead of a hard cut. Use this for the recap-to-episode handoff demo."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(recap_path),
        "-i", str(episode_audio_path),
        "-filter_complex",
        f"[0:a][1:a]acrossfade=d={crossfade_seconds}:c1=tri:c2=tri[out]",
        "-map", "[out]",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def _next_version(episode_dir: Path) -> int:
    """Looks at existing audio_v*.mp3 files and returns the next version number."""
    existing = list(episode_dir.glob("audio_v*.mp3"))
    if not existing:
        return 1
    versions = [int(p.stem.split("_v")[-1]) for p in existing]
    return max(versions) + 1


def estimate_narration_seconds(text: str, wpm: int = 150, buffer_seconds: float = 3.0,
                                min_seconds: int = 8, max_seconds: int = 180) -> int:
    """
    Replaces the old fixed 30s trim. Estimates how long this specific script
    actually takes to narrate (~wpm words/minute, same assumption used in
    _mock_narration), adds a small buffer so the mix doesn't clip the last
    word, and clamps to [min_seconds, max_seconds] as a sanity ceiling —
    not a hard target every clip gets forced into.

    wpm=150 is a rough starting point (see render.py's top-level note on
    voice-specific wpm varying) — re-measure against your actual TTS output
    and adjust if narrations are consistently running long/short vs the mix.
    """
    words = max(len(text.split()), 1)
    seconds = (words / wpm) * 60 + buffer_seconds
    return int(max(min_seconds, min(max_seconds, round(seconds))))


def render_episode(episode_id: str, text: str, character: str | None = None,
                    music_path: Path = MUSIC_BED) -> dict:
    """
    text -> narration -> mixed -> versioned mp3 -> status.json updated.

    episode_id: can be a plain id ("ep3") or a composite id ("ep3/karna") —
                pathlib handles the nested folder either way, no extra code needed.
    character:  optional — only needed if you want per-character voice
                selection via CHARACTER_VOICES. Safe to omit.
    """
    episode_dir = DATA_DIR / episode_id
    episode_dir.mkdir(parents=True, exist_ok=True)
    version = _next_version(episode_dir)

    narration_tmp = episode_dir / f"_narration_v{version}.mp3"
    final_out = episode_dir / f"audio_v{version}.mp3"

    entry = CHARACTER_VOICES.get(character.strip().lower()) if character else None

    if TTS_ENGINE == "openai":
        # OpenAI has no rate/pitch controls — only a named voice. rate/pitch
        # are left at their (unused) defaults so generate_narration's shared
        # signature still works; _generate_openai ignores them entirely.
        if entry is None:
            voice = OPENAI_TTS_VOICE
        elif isinstance(entry, str):
            voice = entry
        else:
            voice = entry.get("openai_voice", OPENAI_TTS_VOICE)
        rate, pitch = DEFAULT_RATE, DEFAULT_PITCH
        eq_preset = entry.get("eq_preset", DEFAULT_EQ_PRESET) if isinstance(entry, dict) else DEFAULT_EQ_PRESET
    else:
        if entry is None:
            voice, rate, pitch, eq_preset = TTS_VOICE, DEFAULT_RATE, DEFAULT_PITCH, DEFAULT_EQ_PRESET
        elif isinstance(entry, str):
            voice, rate, pitch, eq_preset = entry, DEFAULT_RATE, DEFAULT_PITCH, DEFAULT_EQ_PRESET
        else:
            voice = entry.get("voice", TTS_VOICE)
            rate = entry.get("rate", DEFAULT_RATE)
            pitch = entry.get("pitch", DEFAULT_PITCH)
            eq_preset = entry.get("eq_preset", DEFAULT_EQ_PRESET)

    target_seconds = estimate_narration_seconds(text)

    generate_narration(text, narration_tmp, voice=voice, rate=rate, pitch=pitch)
    mix_with_music(narration_tmp, music_path, final_out, target_seconds=target_seconds, eq_preset=eq_preset)
    narration_tmp.unlink(missing_ok=True)

    status_path = episode_dir / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    status["latest_audio_version"] = version
    status["latest_audio_file"] = final_out.name
    status["latest_script_text"] = text
    status["voice_used"] = voice
    status["rate_used"] = rate
    status["pitch_used"] = pitch
    status["eq_preset_used"] = eq_preset
    status["target_seconds_used"] = target_seconds
    status["rendered_at"] = datetime.now(timezone.utc).isoformat()
    status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False))

    return {"episode_id": episode_id, "version": version, "audio_path": str(final_out), "voice": voice}


if __name__ == "__main__":
    if "--test" in sys.argv:
        test_lines = {
            "yudhishthira": "He is asking me. Ashwatthama is dead. Then, quieter: the elephant.",
            "drona": "Drupada has been dead since morning and I feel nothing at all. Strange.",
            "dhrishtadyumna": "My father is somewhere behind me, cooling. It takes one motion.",
            "ashwatthama": "Three chariots. Three, since the horns changed. I am alive.",
        }
        for character, text in test_lines.items():
            result = render_episode(episode_id=f"test_ep/{character}", text=text, character=character)
            print(f"Rendered {character}: {result}")
    else:
        print("Usage: python render.py --test")
