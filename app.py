import io, re, unicodedata, time
import requests, pandas as pd, streamlit as st

# ---------- Page ----------
st.set_page_config(page_title="Draft Waiver Assistant", page_icon="⚽", layout="centered")
st.title("FPL DraftFantasy — Waiver Assistant")
st.caption("API-only • no Google auth • tuned for mobile")

# ---------- Config ----------
DEFAULT_LEAGUE_ID = st.secrets.get("LEAGUE_ID", "cmdnhqw1s06g2kv0431dxfade")
DEFAULT_TEAM_ID   = st.secrets.get("TEAM_ID",   "cmdofouqx0009jt04qjgcm5cn")
CSV_URL   = "https://app.draftfantasy.com/api/players/csv"
DRAFT_URL = "https://app.draftfantasy.com/api/v1/league/{league_id}/draft"

# ---------- Helpers ----------
def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def get_json(url, timeout=25):
    r = requests.get(url, timeout=timeout)
    # Safari/PWA sometimes shows grey if we throw before drawing anything;
    # so we parse defensively and show readable errors instead of exceptions.
    ctype = r.headers.get("Content-Type", "")
    if "application/json" not in ctype:
        raise RuntimeError(f"Non-JSON response ({r.status_code}) from {url.split('?')[0]}")
    return r.json()

def get_csv(url, timeout=25):
    r = requests.get(url, timeout=timeout)
    r.encoding = "utf-8"
    try:
        return pd.read_csv(io.StringIO(r.text))
    except Exception as e:
        raise RuntimeError(f"Could not parse CSV ({e})")

@st.cache_data(ttl=300)
def fetch_player_pool():
    df = get_csv(CSV_URL)
    df["Name_norm"] = df["Name"].apply(normalize_name)
    df = df.rename(columns={
        "PointsPerGame": "Point per game",
        "Goals Conceded": "Goals conceded",
        "Yellow Cards": "Yellow cards",
        "Red Cards": "Red cards",
        "Clean Sheets": "Clean sheets",
    })
    return df

@st.cache_data(ttl=60)
def fetch_draft(league_id: str):
    data = get_json(DRAFT_URL.format(league_id=league_id))
    return data if isinstance(data, list) else data.get("picks", [])

def build_owner_map(picks):
    owner_map = {}
    for p in picks:
        nm = p.get("playerName")
        if nm:
            owner_map[normalize_name(nm)] = p.get("teamName", "?")
    return owner_map

def build_my_squad_text(picks, team_id: str):
    pos_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    my_picks = [p for p in picks if (p.get("teamId") == team_id) or (p.get("team_id") == team_id)]
    lines = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in my_picks:
        pos = pos_map.get(int(p.get("playerPosition") or 0))
        name = p.get("playerName")
        if pos and name:
            lines[pos].append(name)
    return "\n".join([
        f"GK: {', '.join(lines['GK'])}",
        f"DEF: {', '.join(lines['DEF'])}",
        f"MID: {', '.join(lines['MID'])}",
        f"FWD: {', '.join(lines['FWD'])}",
    ])

def build_prompt(df_available: pd.DataFrame, my_squad_text: str) -> str:
    top = df_available.sort_values("Point per game", ascending=False).head(25)
    def rowline(r):
        return (f"{r['Name']} ({r['Position']}, {r['Club']}) – "
                f"PPG:{r.get('Point per game',0)}, G/A:{r.get('Goals',0)}/{r.get('Assists',0)}, "
                f"CS:{r.get('Clean sheets',0)} 🟢 Free Agent")
    return f"""🟩 My Squad:
{my_squad_text}

🟢 Top 25 Available Players:
{'\n'.join(rowline(r) for _, r in top.iterrows())}

🎯 Rules:
- Only suggest players not already owned
- Prefer players with high minutes, strong PPG, goal involvement, or clean sheets
- Suggest 1–2 picks and who they could replace

Who should I bring in this week and why?"""

# ---------- UI: Settings + Hard Refresh ----------
with st.expander("Settings", expanded=False):
    league_id = st.text_input("League ID", value=DEFAULT_LEAGUE_ID)
    team_id   = st.text_input("Team ID",   value=DEFAULT_TEAM_ID)
    if st.button("🔁 Hard refresh (clear server cache)"):
        fetch_player_pool.clear()
        fetch_draft.clear()
        st.success("Cache cleared. Page will now reload fresh.")
        st.rerun()

# ---------- Data fetch with visible spinner ----------
with st.spinner("Warming up… fetching latest league draft and player stats"):
    try:
        df_pool = fetch_player_pool()
        picks   = fetch_draft(league_id)
    except Exception as e:
        st.error(f"{e}")
        st.stop()

# ---------- Ownership + filters ----------
owner_map = build_owner_map(picks)
df_pool["Owner"] = df_pool["Name_norm"].map(owner_map).fillna("-")
df_avail = df_pool[df_pool["Owner"] == "-"].copy()

# ---------- Output ----------
my_squad = build_my_squad_text(picks, team_id)
st.subheader("Your Squad")
st.text(my_squad if my_squad.strip() else "No picks found for this team id. Double-check Team ID in Settings.")

st.subheader("Copy-and-paste Prompt")
prompt = build_prompt(df_avail, my_squad)
st.code(prompt)

st.subheader("Top 25 (by Points per game)")
cols = ["Name", "Club", "Position", "Point per game", "Goals", "Assists", "Clean sheets"]
st.dataframe(df_avail.sort_values("Point per game", ascending=False).head(25)[cols], use_container_width=True)

st.caption("Tip: pull-to-refresh if you wake the app from sleep. If it ever looks blank, tap 🔁 Hard refresh.")
