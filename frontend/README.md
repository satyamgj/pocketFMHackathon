# Tech B starter kit — audio + dashboard

Everything here is tested and runs. Your job during the hackathon is mostly
*wiring* — connecting these pieces to what Tech A produces — not writing
code from scratch.

## What each file does

| File | What it's for | When you touch it |
|---|---|---|
| `render.py` | The engine: text → spoken audio → mixed with music → saved file | Hour 2-8, mostly once |
| `test_tts.py` | Generates 5 voice samples so you can pick one by ear | Pre-hackathon |
| `test_mix.py` | Proves ffmpeg mixing works on your machine | Pre-hackathon |
| `dashboard.py` | The team's demo screen — episode list, script, audio player, re-render button | Hour 2-14 |
| `.env` (you create this) | Holds your OpenAI API key privately | Pre-hackathon |

## Setup (do this before hour 0)

1. **Install ffmpeg** if you don't have it:
   - Mac: `brew install ffmpeg`
   - Windows: `choco install ffmpeg` (or download from ffmpeg.org and add to PATH)
   - Linux: `sudo apt install ffmpeg`
   - Check it worked: `ffmpeg -version` should print a version, not "command not found"

2. **Install Python packages:**
   ```
   pip install -r requirements.txt
   ```

3. **Set your API key:**
   - Copy `.env.example` to a new file named `.env`
   - Replace the placeholder with your real OpenAI key (get this from whoever
     is managing the hackathon's OpenAI credits)

4. **Get a royalty-free music bed** — a ~30-45 second instrumental track with
   rising tension, no lyrics. Good sources: Pixabay Music, YouTube Audio
   Library, Uppbeat (free tier). Save it as `assets/music_bed.mp3`.

## Pre-hackathon checklist (your two tasks from the plan)

- [ ] Run `python test_tts.py` — listen to the 5 samples in `voice_samples/`,
      pick your favorite, set `TTS_VOICE` in `render.py` to that name
- [ ] Run `python test_mix.py <any_mp3> <any_mp3>` — confirms ffmpeg works
      on your machine before you're relying on it live
- [ ] Drop your music bed at `assets/music_bed.mp3`

## Hour-by-hour

**Hour 0-2 (tracer bullet):** Run `python render.py --test`. If it produces
a real mp3 in `data/test_ep/`, your whole chain works end to end — TTS, mix,
normalize, trim, versioning, status.json. This is your safety net for the
rest of the day.

**Hour 2-8:** `render_episode()` in `render.py` is already built — you
mostly don't need to touch it. Spend this block starting `dashboard.py`:
run `streamlit run dashboard.py` and confirm it loads (it'll show a warning
until Tech A's `data/` folder has episodes in it — that's expected).

**At the hour-2 sync, ask Tech A:**
- Exact path of the shared `data/` folder (update `DATA_DIR` in `render.py`
  if it's not just `data/`)
- What field name holds the recap script text in their script JSON (so
  whoever calls `render_episode()` passes the right string)
- What key name they'll use for judge scores in `status.json` (dashboard
  currently expects `status["judge_scores"]` as a dict, e.g.
  `{"faithfulness": 8, "spoiler_risk": "low"}`)

**Hour 8-14:** Finish wiring the dashboard to real data. Priority order per
the plan: series view → script + judge scores → audio player →
re-render button. All four are already in `dashboard.py`; this block is
about testing it against Tech A's *real* output and fixing any field-name
mismatches.

**Hour 14-18:** Work with Tech A on the recap-into-episode handoff. Use
`stitch_into_episode()` in `render.py` — it crossfades the recap directly
into the real episode's opening line instead of a hard cut. Rehearse this
transition many times; per the plan it's ~80% of pitch impact.

**Hour 18-20 (rehearsals):** You drive the live product. Practice clicking
through the dashboard start to finish at least twice, timed.

**Hour 20-22 (buffer):**
- Pre-load every browser tab you'll need for the demo
- Make sure the audio files you'll actually play are already rendered and
  sitting on disk — don't depend on live API calls or wifi during the demo
- If venue wifi is shaky, have `streamlit run dashboard.py` running fully
  locally with all audio pre-rendered as your offline fallback

## If something breaks

- **`ffmpeg: command not found`** → it's not installed or not on PATH, see Setup step 1
- **OpenAI auth error** → check `.env` has the right key and `python-dotenv`
  is installed; `render.py` calls `load_dotenv()` at the top
- **Dashboard shows "No episodes yet"** → `DATA_DIR` in `render.py` isn't
  pointing at the same folder Tech A is writing to — fix the path, not the code
- **Mixed audio sounds like music drowns the voice** → lower `volume=0.22`
  to `0.15` or so in `mix_with_music()`

## Cut-list reminder (from the plan)

If you're behind at any gate, cut in this order: multi-episode support →
second series → nothing on the audio/dashboard side gets cut easily, since
"audio production quality" and "the recap-into-episode transition" are on
the team's *never-cut* list. If you're the bottleneck, say so early rather
than silently falling behind — that's what the buffer hours are for.
