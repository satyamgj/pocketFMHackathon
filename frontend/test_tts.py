"""
test_tts.py — PRE-HACKATHON TASK #1

Generates the same Hindi paragraph in 5 different voices so you can pick
a favorite by just listening — no judgment needed beyond your ears.

Usage:
    python test_tts.py

Then: open render.py and set TTS_VOICE = "your_choice"
"""
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI()

# Swap this for 2-3 real sentences from your chosen series if you have them —
# testing on real dialogue/narration style beats a generic sentence.
SAMPLE_TEXT = (
    "Kal raat jo hua, usne sab kuch badal diya. "
    "Ek raaz jo saalon se chhupa tha, ab bahar aane wala hai. "
    "Kya woh iske liye taiyaar hai?"
)

VOICES_TO_TRY = ["onyx", "ash", "ballad", "nova", "verse"]

OUT_DIR = Path("voice_samples")
OUT_DIR.mkdir(exist_ok=True)

for voice in VOICES_TO_TRY:
    out_path = OUT_DIR / f"sample_{voice}.mp3"
    print(f"Generating {voice}...")
    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice=voice,
        input=SAMPLE_TEXT,
        instructions="Speak like a dramatic trailer narrator: urgent, warm, building tension.",
    ) as response:
        response.stream_to_file(out_path)

print(f"\nDone. Listen to the files in {OUT_DIR}/ and pick your favorite.")
print('Then set TTS_VOICE in render.py to your winner.')
print("\nIf none of these sound natural/dramatic enough in Hindi, ElevenLabs'")
print("Multilingual v2 model is the strongest fallback — different API key,")
print("check if anyone on the team has access before the clock starts.")
