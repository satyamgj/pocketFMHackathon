"""
pipeline/schemas.py — data shapes for the Multi-POV algorithm.

Deliberately minimal: only the fields the pipeline actually reads or
writes. No scene/cast-selection fields (Phase C/D — out of scope for
on-demand single-character generation, see ALGORITHM.md).
"""

from typing import Optional
from pydantic import BaseModel


class Misperception(BaseModel):
    char: str
    believes: str


class Fact(BaseModel):
    id: str
    claim: str
    type: str  # event | state | relationship | attribute
    established_at: int  # episode number this became true/known
    observers: list[str] = []
    misperceivers: list[Misperception] = []
    confidence: str = "explicit"  # explicit | implied
    supersedes: Optional[str] = None


class Beat(BaseModel):
    id: str
    unit: int  # episode number
    order: int
    summary: str
    present: list[str] = []
    perceived_by: dict[str, str] = {}  # char -> full | partial | none
    establishes: list[str] = []        # fact ids
    silences: list[str] = []           # what the text doesn't record


class BibleEntry(BaseModel):
    """Phase A output for one episode."""
    series_id: str
    episode_num: int
    facts: list[Fact] = []
    beats: list[Beat] = []


class Voice(BaseModel):
    tempo: str = ""
    diction: str = ""
    tells: list[str] = []


class CharacterPersonality(BaseModel):
    """Phase B Profile output. Kept under this class name for backward
    compatibility with storage.py's save_personality/load_latest_personality."""
    character: str
    episode_num: int
    want: str
    need: str
    lens: str
    preoccupation: str
    voice: Voice = Voice()
    forbidden_affect: str
    wound: str = ""  # the specific old injury/memory THIS episode's events reopen —
                      # without this, lens and forbidden_affect stay generic ("distrust",
                      # "grief") instead of anchored to something concrete this character
                      # can't stop circling back to. Optional so old data still loads.
    valid_through: int


class JudgeResult(BaseModel):
    faithful: bool
    reasons: list[str] = []
    tier: str  # "tier1" (deterministic reject) | "tier3" (model regenerate) | "pass"


class RecapScript(BaseModel):
    series_id: str
    episode_num: int
    character: Optional[str]  # None = omniscient
    version: int
    text: str
    word_count: int
    judge: Optional[JudgeResult] = None