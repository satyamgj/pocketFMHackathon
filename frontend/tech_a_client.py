"""
tech_a_client.py — thin client for Tech A's recap/judge API.

Talks to the FastAPI service in pipeline/api.py (satyamgj/pocketFMHackathon).
Start Tech A's server first, from the backend/ folder of that repo:

    pip install -r requirements.txt
    python pipeline/runner.py --series mahabharata --ep 170 --from-txt eps.txt
    python -m uvicorn pipeline.api:app --reload --port 8000

CONFIRMED CONTRACT (read directly from Tech A's api.py + schemas.py — no
more guessing here):

  GET /series/{series_id}/episodes/{ep_num}/characters
      -> {
           "series_id": str,
           "episode_num": int,
           "characters": [str, ...],       # always starts with "omniscient"
           "already_generated": [str, ...] # subset with a cached POV, i.e. instant
         }
      This call can be slow the FIRST time for a given episode (it runs ASR +
      extraction if not already done). Fast on every call after that.

  GET /series/{series_id}/episodes/{ep_num}/characters/{character}/pov?force=bool
      -> {
           "series_id": str,
           "episode_num": int,
           "character": str,
           "version": int,
           "judge_passed": bool | None,    # None if no judge ran
           "cached": bool,                 # False if this call just generated it
           "text": str                     # <- the recap script text to feed TTS
         }
      IMPORTANT MISMATCH vs. the old assumption in render.py/dashboard.py:
      Tech A's judge returns a single "judge_passed" bool, NOT a dict of named
      scores. There is no "judge_scores" (per-category 0..1/0..10 breakdown)
      and no "perspective_consistent" field on this API. dashboard.py's Judge
      tab has been rewritten around what actually exists (judge_passed +
      version + cached), not the old shape.

      First call for an uncached character runs personality + recap + judge
      generation synchronously and can take a while — callers should show a
      loading state, not assume this is instant like the /characters call.

  GET /health -> {"status": "ok"}
"""
import requests

API_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 60  # generous — first POV call for an uncached character runs the LLM pipeline


class TechAAPIError(Exception):
    """Raised whenever Tech A's API can't be reached or returns an error."""


def health_check() -> bool:
    try:
        resp = requests.get(f"{API_BASE_URL}/health", timeout=5)
        return resp.ok
    except requests.exceptions.RequestException:
        return False


def fetch_characters(series_id: str, ep_num: int) -> dict:
    """Returns {"series_id", "episode_num", "characters", "already_generated"}."""
    url = f"{API_BASE_URL}/series/{series_id}/episodes/{ep_num}/characters"
    try:
        resp = requests.get(url, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        raise TechAAPIError(
            f"Couldn't reach Tech A's API at {url} ({e}). "
            f"Is it running? python -m uvicorn pipeline.api:app --reload --port 8000"
        ) from e
    except ValueError as e:  # bad JSON
        raise TechAAPIError(f"Tech A's API returned something that wasn't JSON: {e}") from e


def fetch_pov(series_id: str, ep_num: int, character: str, force: bool = False) -> dict:
    """Returns {"series_id", "episode_num", "character", "version",
    "judge_passed", "cached", "text"}."""
    url = f"{API_BASE_URL}/series/{series_id}/episodes/{ep_num}/characters/{character}/pov"
    try:
        resp = requests.get(url, params={"force": force}, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        raise TechAAPIError(
            f"Couldn't fetch {character}'s POV from Tech A's API ({e})."
        ) from e
    except ValueError as e:
        raise TechAAPIError(f"Tech A's API returned something that wasn't JSON: {e}") from e
