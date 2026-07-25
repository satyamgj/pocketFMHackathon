# Infinite Story Universe — Mahabharat Multi-POV Recap Engine

Built for Pocket FM's hackathon. Given an episode of the Mahabharata, this
generates a short narrated recap from any character's point of view — their
own voice, their own knowledge of events, their own blind spots — and
renders it as playable audio with a waveform.

## What it does

Pick a character who appeared in an episode. The system:
1. Works out what that character actually witnessed (not the full omniscient
   story — just their slice of it, including misperceptions).
2. Derives their personality/voice from what they did that episode.
3. Writes a first-person recap monologue in their voice.
4. Validates it against a faithfulness judge (did it leak something they
   couldn't know? does it read as a private thought, not a report?).
5. Synthesizes narration (OpenAI TTS, a distinct voice per character) and
   mixes it with a music bed.
6. Plays it back in a browser dashboard with a real waveform.

## Architecture

```
backend/                    Recap/judge API (FastAPI)
  pipeline/
    stages/                 ASR, extraction, personality, recap, judge
    api.py                  Two endpoints: list characters, get a POV
    runner.py                One-time episode ingestion

frontend/                   Dashboard (Streamlit) + audio engine
  dashboard.py               Browse -> perspectives -> waveform player
  render.py                  TTS (OpenAI) + ffmpeg mixing, per-character voices
  tech_a_client.py            Thin client for the backend API
```

The two run as separate processes that talk over HTTP:

```
backend (FastAPI, :8000)  --script text-->  frontend/render.py (TTS+ffmpeg)  -->  dashboard (Streamlit, :8501)
```

## Running it locally

You need two terminals.

**Terminal 1 — backend:**
```
cd backend
pip install -r requirements.txt
python pipeline/runner.py --series mahabharata --ep 170 --from-txt eps.txt
python -m uvicorn pipeline.api:app --reload --port 8000
```

**Terminal 2 — frontend:**
```
cd frontend
pip install -r requirements.txt
streamlit run dashboard.py
```
Opens at `http://localhost:8501`.

### Environment variables
Both sides need `OPENAI_API_KEY` (backend uses it for recap/judge
generation; frontend uses it for TTS) — set it as a real environment
variable or in a local `.env` file in the relevant folder. An OpenAI
account with billing enabled is required; TTS is a paid endpoint.

`frontend/render.py` also shells out to `ffmpeg` directly — make sure
it's installed and on your PATH.

## Notes for contributors

- `data/` folders (generated scripts, personalities, rendered audio) are
  git-ignored on purpose — they're regenerated at runtime, not checked in.
  Never commit `.env` files; both `backend/` and `frontend/` read secrets
  from the environment, not from anything tracked in git.
- Character voices are assigned per character with distinct male/female
  OpenAI voice pools — see `CHARACTER_VOICES` in `frontend/render.py`.
- See `frontend/DEPLOY.md` for deploying the dashboard + API separately
  (e.g. Railway).

## Team

- **Backend** (recap generation, faithfulness judge, story bible): satyamgj
- **Frontend** (dashboard, TTS/audio pipeline): menonsagar
