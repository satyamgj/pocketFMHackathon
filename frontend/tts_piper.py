"""
tts_piper.py — open-source, fully offline TTS fallback using Piper.

Why Piper specifically:
- No API key, no internet needed once the voice is downloaded — immune to
  any provider outage, forever, not just today.
- Runs fast on CPU (no GPU needed), which matters on a laptop mid-hackathon.
- Has an official Hindi voice (hi_IN-rohan-medium), which matches your
  content language.

--- One-time setup (Windows PowerShell) ---

1. Install the package (already covers the runtime, no separate ffmpeg-style
   binary needed):
     pip install piper-tts

2. Download the Hindi voice (~60MB) — two files, keep them together:
     https://huggingface.co/rhasspy/piper-voices/resolve/main/hi/hi_IN/rohan/medium/hi_IN-rohan-medium.onnx
     https://huggingface.co/rhasspy/piper-voices/resolve/main/hi/hi_IN/rohan/medium/hi_IN-rohan-medium.onnx.json

   Save both into a `voices/` folder next to this file. If that URL 404s,
   browse https://huggingface.co/rhasspy/piper-voices/tree/main/hi/hi_IN and
   grab whatever Hindi voice is listed there instead — the filename doesn't
   matter, just point VOICE_MODEL_PATH at whatever you download.

   For an English fallback voice instead/as well, same pattern under
   /en/en_US/... (e.g. en_US-lessac-medium).

3. Test it:
     python tts_piper.py

--- Wiring into your existing fallback chain ---

Wherever render.py currently does:
    try:
        audio_bytes = call_openai_tts(text)
    except Exception as e:
        print(f"[warning] OpenAI TTS failed ({e}) — using silent placeholder instead")
        audio_bytes = make_silence(...)

change the except block to try Piper before giving up to silence:
    except Exception as e:
        print(f"[warning] OpenAI TTS failed ({e}) — trying Piper (offline) instead")
        try:
            audio_bytes = synthesize_piper(text)
        except Exception as e2:
            print(f"[warning] Piper also failed ({e2}) — using silent placeholder instead")
            audio_bytes = make_silence(...)

That gets you real narration even with zero internet connection, which is
strictly better insurance for the actual demo than silence.
"""
from __future__ import annotations
import io
import wave
from pathlib import Path

from piper import PiperVoice

VOICE_DIR = Path(__file__).resolve().parent / "voices"
VOICE_MODEL_PATH = VOICE_DIR / "hi_IN-rohan-medium.onnx"

_voice: PiperVoice | None = None


def _get_voice() -> PiperVoice:
    global _voice
    if _voice is None:
        if not VOICE_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Piper voice not found at {VOICE_MODEL_PATH}. "
                f"Download it first — see the setup instructions at the top of this file."
            )
        _voice = PiperVoice.load(str(VOICE_MODEL_PATH))
    return _voice


def synthesize_piper(text: str) -> bytes:
    """Return raw WAV bytes for the given text, synthesized locally."""
    voice = _get_voice()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)
    return buf.getvalue()


if __name__ == "__main__":
    audio = synthesize_piper(
        "यह एक परीक्षण है। पाइपर पूरी तरह ऑफ़लाइन काम करता है।"
    )
    out_path = Path("piper_smoke_test.wav")
    out_path.write_bytes(audio)
    print(f"wrote {len(audio)} bytes to {out_path.resolve()}")
