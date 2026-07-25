"""
dashboard.py — Tech B's demo centerpiece for "Infinite Story Universe"
(Mahabharat multi-POV storytelling engine)

Run with:
    streamlit run dashboard.py

THIS PASS: real click-through navigation modeled on Pocket FM's own app,
using st.session_state as a simple router (three stages, not separate
files/pages):

  1. HOME    — a poster card for the series ("Mahabharata"). Click it ->
  2. DETAIL  — big poster + title + a "View Perspectives" pill button.
               Click it -> the right-hand panel reveals every character
               as a row (name, status, a compact play/generate action) —
  3. PLAYER  — clicking a character's row loads their audio in the main
               area with a real waveform above the controls (wavesurfer.js,
               embedded via a base64 data URI, same technique as an
               earlier pass), while the character list stays visible on
               the right so switching perspectives is a single click.

CHARACTER / SCRIPT SOURCE — LIVE FROM TECH A'S API (see tech_a_client.py):
  - Character list + already-generated ones: GET .../characters
  - Recap script text: GET .../characters/{character}/pov

LOCAL STORAGE (Tech B's own audio output, unchanged):
  data/<series_id>_ep<ep_num>/<character>/status.json, audio_v*.mp3
"""

import base64
import json
import subprocess
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from render import render_episode
from tech_a_client import fetch_characters, fetch_pov, TechAAPIError

# --------------------------------------------------------------------------
# Config / field-name constants
# --------------------------------------------------------------------------
DATA_DIR = Path("data")
DEFAULT_SERIES_ID = "mahabharata"
DEFAULT_EP_NUM = 170

F_AUDIO_FILE = "latest_audio_file"
F_JUDGE_PASSED = "judge_passed"

ACCENT_PALETTE = ["#E8763C", "#D4A94C", "#C24B6B", "#4C8ED4", "#7B5CD4", "#4CAE7A"]

CHARACTER_GLYPHS = {
    "dhritarashtra": "🕯️",
    "draupadi": "🔥",
    "arjuna": "🏹",
    "krishna": "🪈",
    "duryodhana": "⚔️",
    "bhishma": "🛡️",
    "drona": "📜",
}
DEFAULT_GLYPH = "🕉️"


def accent_for(name: str, index: int) -> str:
    return ACCENT_PALETTE[index % len(ACCENT_PALETTE)]


def glyph_for(name: str) -> str:
    return CHARACTER_GLYPHS.get(name.lower(), DEFAULT_GLYPH)


def audio_duration_label(path: Path) -> str:
    """mm:ss via ffprobe (ships alongside ffmpeg, already required by render.py)."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True,
        )
        seconds = float(result.stdout.strip())
        m, s = divmod(int(round(seconds)), 60)
        return f"{m}:{s:02d}"
    except Exception:
        return ""


def render_player_card(character: str, subtitle: str, audio_path: Path, accent_color: str, key: str) -> None:
    """One self-contained card: glyph + name + a real waveform (wavesurfer.js)
    + play/pause, all inside a single styled iframe so it reads as one
    framed panel instead of raw text floating above a thin audio bar.
    Autoplays as soon as it loads — clicking a name in the perspective
    list should start playing immediately, not require a second click."""
    audio_bytes = audio_path.read_bytes()
    b64 = base64.b64encode(audio_bytes).decode()
    safe_key = "".join(c if c.isalnum() else "_" for c in key)
    html = f"""
    <div style="font-family: 'Segoe UI', system-ui, sans-serif;
                background: linear-gradient(135deg, {accent_color}33, {accent_color}11);
                border: 1px solid {accent_color}77; border-radius: 18px; padding: 26px 28px;
                box-sizing: border-box;">
        <div style="font-size: 2.2rem; line-height:1;">{glyph_for(character)}</div>
        <div style="font-weight: 700; font-size: 1.4rem; color: #FBE6C8; margin-top: 8px;">{character.title()}'s account</div>
        <div style="color: #cfc3d9; font-size: 0.85rem; margin-bottom: 18px;">{subtitle}</div>
        <div id="wf-{safe_key}"></div>
        <div style="display:flex; align-items:center; gap:12px; margin-top:12px;">
            <button id="btn-{safe_key}" style="
                background:{accent_color}; border:none; color:#0c0912; font-weight:700;
                padding:8px 20px; border-radius:999px; cursor:pointer; font-size:0.9rem;
            ">⏸ Pause</button>
            <span id="time-{safe_key}" style="color:#cfc3d9; font-size:0.82rem;">0:00 / 0:00</span>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/wavesurfer.js@7/dist/wavesurfer.min.js"></script>
    <script>
        const ws_{safe_key} = WaveSurfer.create({{
            container: '#wf-{safe_key}',
            waveColor: 'rgba(255,255,255,0.25)',
            progressColor: '{accent_color}',
            cursorColor: '{accent_color}',
            barWidth: 2, barGap: 1, barRadius: 2, height: 64,
        }});
        ws_{safe_key}.load('data:audio/mp3;base64,{b64}');
        const btn_{safe_key} = document.getElementById('btn-{safe_key}');
        const timeEl_{safe_key} = document.getElementById('time-{safe_key}');
        function fmt_{safe_key}(t) {{
            if (!t || isNaN(t)) return '0:00';
            const m = Math.floor(t / 60), s = Math.floor(t % 60);
            return m + ':' + (s < 10 ? '0' : '') + s;
        }}
        btn_{safe_key}.onclick = () => {{
            ws_{safe_key}.playPause();
            btn_{safe_key}.innerText = ws_{safe_key}.isPlaying() ? '⏸ Pause' : '▶ Play';
        }};
        ws_{safe_key}.on('audioprocess', () => {{
            timeEl_{safe_key}.innerText = fmt_{safe_key}(ws_{safe_key}.getCurrentTime()) + ' / ' + fmt_{safe_key}(ws_{safe_key}.getDuration());
        }});
        ws_{safe_key}.on('ready', () => {{
            timeEl_{safe_key}.innerText = '0:00 / ' + fmt_{safe_key}(ws_{safe_key}.getDuration());
            ws_{safe_key}.play();  // autoplay — picking a name should start audio immediately
        }});
        ws_{safe_key}.on('finish', () => {{ btn_{safe_key}.innerText = '▶ Play'; }});
    </script>
    """
    components.html(html, height=320)


def do_generate(char: str, series_id: str, ep_num: int, selected_episode: str,
                 already_generated: set, episode_dir: Path) -> None:
    """Fetches this character's script and renders it once. Leaves
    st.session_state.selected_character set so the caller can st.rerun()."""
    spinner_msg = (
        f"Generating {char.title()}'s narration..."
        if char in already_generated
        else f"Generating {char.title()}'s account for the first time — this can take a moment..."
    )
    with st.spinner(spinner_msg):
        try:
            pov_data = fetch_pov(series_id, ep_num, char)
        except TechAAPIError as e:
            st.error(f"Couldn't fetch {char.title()}'s script from Tech A: {e}")
            st.stop()

        render_episode(episode_id=f"{selected_episode}/{char}", text=pov_data.get("text", ""), character=char)

        status_path = episode_dir / char / "status.json"
        fresh_status = json.loads(status_path.read_text())
        fresh_status[F_JUDGE_PASSED] = pov_data.get("judge_passed")
        status_path.write_text(json.dumps(fresh_status, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------
# Page setup + theme
# --------------------------------------------------------------------------
st.set_page_config(page_title="Infinite Story Universe — Mahabharat POV Engine", page_icon="🕉️", layout="wide")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@600;700;800&family=Inter:wght@400;500;600&display=swap');

    html, body, [class*="css"] { font-family: 'Inter', sans-serif; font-size: 16px; }
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    header[data-testid="stHeader"] { background: transparent; }
    .block-container { padding-top: 1rem; max-width: 1200px; }

    /* Tighten button row spacing — with 5 episode rows + 2 rows of names,
       default Streamlit block margins add up to real scroll height */
    div.stButton { margin-bottom: 2px !important; }
    hr { margin: 0.8rem 0 !important; }

    /* Primary buttons ("View Perspectives") — warm gradient pill, matches
       Pocket FM's "Play Ep-1" button */
    button[kind="primary"] {
        background: linear-gradient(120deg, #C24B6B, #7B5CD4) !important;
        border: none !important;
        border-radius: 999px !important;
        font-weight: 700 !important;
        padding: 0.6rem 1.6rem !important;
        font-size: 1rem !important;
    }
    button[kind="primary"]:hover { transform: translateY(-1px); }

    /* Secondary buttons (character names at bottom, dummy episode rows, back nav) */
    button[kind="secondary"] {
        border-radius: 999px !important;
        font-weight: 600 !important;
        font-size: 1.02rem !important;
    }

    /* Episode list buttons (right column) — rectangle, not pill, per request.
       Scoped via a marker div so this doesn't affect the character-name
       buttons at the bottom, which share the same primary/secondary types. */
    div[data-testid="stVerticalBlock"]:has(> div.episode-list-marker) button {
        border-radius: 8px !important;
    }

    /* Home poster card */
    .home-poster {
        height: 320px;
        border-radius: 20px;
        background: linear-gradient(135deg, #2a1030 0%, #5c1f2e 55%, #7a3a12 100%);
        display: flex; align-items: flex-end;
        padding: 26px;
        position: relative;
        overflow: hidden;
        border: 1px solid rgba(232,118,60,0.35);
        cursor: default;
    }
    .home-poster::before {
        content: "🕉️"; position: absolute; top: 10px; right: 20px;
        font-size: 160px; opacity: 0.12;
    }
    .home-poster-title {
        font-family: 'Poppins', sans-serif; font-weight: 800; font-size: 2.2rem;
        background: linear-gradient(90deg, #FBE6C8, #E8763C);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        position: relative;
    }
    .home-poster-meta { color: #cfc3d9; font-size: 0.9rem; margin-top: 4px; position: relative; }

    /* Show-detail poster (left column) */
    .show-poster {
        height: 380px; border-radius: 18px;
        background: linear-gradient(135deg, #2a1030 0%, #5c1f2e 55%, #7a3a12 100%);
        display: flex; align-items: flex-end; padding: 30px;
        position: relative; overflow: hidden;
        border: 1px solid rgba(232,118,60,0.35);
        margin-bottom: 18px;
    }
    .show-poster::before {
        content: "🕉️"; position: absolute; top: 10px; right: 10px;
        font-size: 200px; opacity: 0.12;
    }
    .show-poster-title {
        font-family: 'Poppins', sans-serif; font-weight: 800; font-size: 2.6rem;
        background: linear-gradient(90deg, #FBE6C8, #E8763C);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        position: relative;
    }

    .panel-header {
        font-family: 'Poppins', sans-serif; font-weight: 700; font-size: 1.2rem;
        margin-bottom: 4px;
    }
    .panel-sub { color: #9c93a8; font-size: 0.85rem; margin-bottom: 14px; }

    /* Perspective row text (the box itself is a real st.container(border=True) now) */
    .persp-name { font-family: 'Poppins', sans-serif; font-weight: 600; font-size: 1rem; }
    .persp-meta { color: #9c93a8; font-size: 0.78rem; }
    .persp-meta.instant { color: #f1d9a0; }
    .persp-meta.ready { color: #8fe3b4; }

    /* Tighten the row containers so 10 rows don't sprawl with default
       Streamlit block spacing — this targets every bordered container,
       which in this app is only the perspective rows */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        padding: 2px 6px !important;
        margin-bottom: 6px !important;
        border-radius: 12px !important;
    }

    /* Player header */
    .player-header {
        font-family: 'Poppins', sans-serif; font-weight: 700; font-size: 1.4rem;
        margin-bottom: 2px;
    }
    .player-sub { color: #9c93a8; font-size: 0.85rem; margin-bottom: 14px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------
# Router state
# --------------------------------------------------------------------------
st.session_state.setdefault("stage", "home")           # "home" | "detail"
st.session_state.setdefault("show_perspectives", False)
st.session_state.setdefault("selected_character", None)
st.session_state.setdefault("loaded_key", None)

with st.sidebar:
    with st.expander("⚙️ Chapter settings", expanded=False):
        series_id = st.text_input("Series", value=DEFAULT_SERIES_ID)
        ep_num = st.number_input("Episode", min_value=1, value=DEFAULT_EP_NUM, step=1)

selected_episode = f"{series_id}_ep{ep_num}"
episode_dir = DATA_DIR / selected_episode
episode_dir.mkdir(parents=True, exist_ok=True)

# Reset downstream state if the chapter changed underneath us
loaded_key = f"{series_id}:{ep_num}"
if st.session_state.loaded_key != loaded_key:
    st.session_state.loaded_key = loaded_key
    st.session_state.show_perspectives = False
    st.session_state.selected_character = None

# --------------------------------------------------------------------------
# STAGE 1 — HOME: a single poster card for the series
# --------------------------------------------------------------------------
if st.session_state.stage == "home":
    st.markdown(
        f"""
        <div class="home-poster">
            <div>
                <div class="home-poster-title">Mahabharata</div>
                <div class="home-poster-meta">Ep {ep_num} · multi-perspective recap engine</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")
    if st.button("Explore", type="primary"):
        st.session_state.stage = "detail"
        st.rerun()

# --------------------------------------------------------------------------
# STAGE 2 — DETAIL + PERSPECTIVES + PLAYER (one screen, panel reveals progressively)
# --------------------------------------------------------------------------
else:
    if st.button("← Back", type="secondary"):
        st.session_state.stage = "home"
        st.session_state.show_perspectives = False
        st.session_state.selected_character = None
        st.rerun()

    # Fetch quietly so the button below can show a real count — the actual
    # per-character list only renders once "View Perspectives" is clicked.
    try:
        with st.spinner("Loading chapter..."):
            characters_response = fetch_characters(series_id, int(ep_num))
    except TechAAPIError as e:
        st.error(
            f"Couldn't load characters from Tech A's API: {e}\n\n"
            f"Start it with: `python -m uvicorn pipeline.api:app --reload --port 8000`."
        )
        st.stop()

    characters = characters_response.get("characters", [])
    already_generated = set(characters_response.get("already_generated", []))

    if not characters:
        st.info(f"No characters found for **{series_id}** episode **{ep_num}** yet.")
        st.stop()

    left, right = st.columns([2, 1])

    # ---- LEFT: poster, or the waveform player once a character is picked ----
    with left:
        sel = st.session_state.selected_character
        if sel:
            character_dir = episode_dir / sel
            status_path = character_dir / "status.json"
            status = json.loads(status_path.read_text()) if status_path.exists() else {}
            audio_file = status.get(F_AUDIO_FILE)
            audio_path = character_dir / audio_file if audio_file else None
            idx = characters.index(sel) if sel in characters else 0
            color = accent_for(sel, idx)

            if audio_path and audio_path.exists():
                render_player_card(
                    sel, f"Mahabharata · Ep {ep_num}", audio_path, color,
                    key=f"{selected_episode}_{sel}",
                )
            else:
                st.info("Not rendered yet — pick this witness again from the list to generate it.")
        else:
            st.markdown(
                f"""
                <div class="show-poster">
                    <div class="show-poster-title">Mahabharata</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.caption(f"Ep {ep_num} · seen through every eye in the room.")
            if not st.session_state.show_perspectives:
                if st.button(f"🔀 View Perspectives ({len(characters)})", type="primary"):
                    st.session_state.show_perspectives = True
                    st.rerun()

    # ---- RIGHT: dummy episode list (decorative — only Ep {ep_num} is real) ----
    with right:
        st.markdown('<div class="episode-list-marker"></div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="panel-header">Episodes</div>'
            f'<div class="panel-sub">This chapter · Ep {ep_num}</div>',
            unsafe_allow_html=True,
        )
        # Fills downward from the current episode. These are placeholders —
        # only ep_num actually has data behind it; clicking another number
        # doesn't load anything, it's here to match the show-page look.
        NUM_DUMMY_EPISODES = 5
        episode_numbers = [int(ep_num) + i for i in range(NUM_DUMMY_EPISODES)]
        for n in episode_numbers:
            is_current = n == int(ep_num)
            st.button(
                f"{'▶ ' if is_current else ''}Episode {n}",
                key=f"ep_dummy_{n}",
                type="primary" if is_current else "secondary",
                use_container_width=True,
                disabled=False,
            )

    # --------------------------------------------------------------------------
    # BOTTOM: perspective names — plain clickable text, no glyphs/status, one
    # row wrapping as needed. This is what actually drives selection/generation
    # now (moved off the right panel, which is now the decorative episode list).
    # --------------------------------------------------------------------------
    if st.session_state.show_perspectives:
        st.divider()
        st.markdown(f'<div class="panel-header">Perspectives · {len(characters)} available</div>', unsafe_allow_html=True)

        NAMES_PER_ROW = 6
        for row_start in range(0, len(characters), NAMES_PER_ROW):
            row_chars = characters[row_start:row_start + NAMES_PER_ROW]
            cols = st.columns(NAMES_PER_ROW)
            for col, char in zip(cols, row_chars):
                with col:
                    is_active = char == st.session_state.selected_character
                    if st.button(
                        char.title(),
                        key=f"name_{char}",
                        type="primary" if is_active else "secondary",
                        use_container_width=True,
                    ):
                        character_dir = episode_dir / char
                        status_path = character_dir / "status.json"
                        status = json.loads(status_path.read_text()) if status_path.exists() else {}
                        audio_file = status.get(F_AUDIO_FILE)
                        audio_path = character_dir / audio_file if audio_file else None
                        has_audio = audio_path is not None and audio_path.exists()
                        if not has_audio:
                            do_generate(char, series_id, int(ep_num), selected_episode, already_generated, episode_dir)
                        st.session_state.selected_character = char
                        st.rerun()
