"""
voice_picker.py — generates the same "previously on..." recap line in
several OpenAI TTS voices so you can listen side-by-side and pick one.

Usage:
    python voice_picker.py

Writes one mp3 per voice into voice_samples/, named e.g. voice_samples/onyx.mp3

Curated to 6 voices worth trying for an audio-drama narrator (out of 13
total) to keep this quick and cheap. Add/remove voice names in VOICES_TO_TRY
if you want to hear more.
"""
import os
from pathlib import Path
from openai import OpenAI

# A representative line — swap this for an actual line from your recap
# script once you have one, since delivery can vary with content.
SAMPLE_TEXT = (
    "Previously, on the show... a secret came out, and nothing "
    "would be the same again."
)

# All 13 available: alloy, ash, ballad, coral, echo, fable, onyx, nova,
# sage, shimmer, verse, marin, cedar. OpenAI recommends marin/cedar for
# quality-focused use cases — worth trying those first.
VOICES_TO_TRY = ["marin", "cedar", "onyx", "nova", "fable", "ash"]

NARRATOR_INSTRUCTIONS = (
    "Speak as a warm, slightly urgent audio-drama narrator doing a "
    "'previously on...' recap. Measured pace, building tension toward the "
    "final line."
)

MODEL = "gpt-4o-mini-tts"


def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set — check your .env is loaded.")

    client = OpenAI(api_key=api_key)
    out_dir = Path("voice_samples")
    out_dir.mkdir(exist_ok=True)

    for voice in VOICES_TO_TRY:
        print(f"generating: {voice}...")
        response = client.audio.speech.create(
            model=MODEL,
            voice=voice,
            input=SAMPLE_TEXT,
            instructions=NARRATOR_INSTRUCTIONS,
            response_format="mp3",
        )
        out_path = out_dir / f"{voice}.mp3"
        out_path.write_bytes(response.read())
        print(f"  wrote {out_path}")

    print("\nDone. Files are in voice_samples/ — play each one and pick your favorite.")


if __name__ == "__main__":
    main()
