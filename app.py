import io
import re
import time
import unicodedata
import requests
import pandas as pd
import streamlit as st

# ---------------- Config ----------------
DEFAULT_LEAGUE_ID = st.secrets.get("LEAGUE_ID", "cmdnhqw1s06g2kv0431dxfade")
DEFAULT_TEAM_ID   = st.secrets.get("TEAM_ID",   "cmdofouqx0009jt04qjgcm5cn")

CSV_URL   = "https://app.draftfantasy.com/api/players/csv"
LIVE_URL  = "https://app.draftfantasy.com/api/league/{league_id}/transfers-data"

# --------------- Helpers ----------------
def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def fetch_live_league(league_id: str):
    url = LIVE_URL.format(league_id=league_id)
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    league = data.get("league", data)  # handle both {league:{...}} or {...}
    teams = league.get("teams", [])
    # Build owner maps by NAME and by TEAM
    owner_by_name = {}
    players_by_team = {}
    for t in teams:
        tname = t.get("name") or t.get("teamName") or "-"
        tid = str(t.get("id") or t.get("teamId") or "")
        roster = t.get("teamPlayers", []) or t.get("players", [])
        players_by_team[tid] = []
        for p in roster:
            pname = p.get("name") or p.get("playerName")
            if pname:
                owner_by_name[normalize_name(pname)] = tname
                players_by_team[tid].append(pname)
    return owner_by_name, players_by_team

def build_my_squad_text(players_by_team, my_team_id: str):
    # We only have names here; we’ll classify positions via the CSV
    my_names = set(players_by_team.get(my_team_id, []))
    if not my_names:
        return ""
    # Return as a simple flat list first; positions will be added via CSV cross join
    return "\n".join(sorted(my_names))

def build_prompt(df_available: pd.DataFrame, my_squad_text: str) -> str:
    top = df_available.sort_values("Point per game", ascending=False).head(25)
    def rowline(r):
        return (
            f"{r['Name']} ({r['Position']}, {r['Club']}) – "
            f"PPG:{r.get('Point per game',0)}, G/A:{r.get('Goals',0)}/{r.get('Assists',0)}, "
            f"CS:{r.get('Clean sheets',0)} 🟢 Free Agent"
        )
    avail_text = "\n".join(rowline(r) for _, r in top.iterrows())
    return f"""🟩 My Squad:
{my_squad_text}

🟢 Top 25 Available Players:
{avail_text}

🎯 Rules:
- Only suggest players not already owned
- Prefer players with high minutes, strong PPG, goal involvement, or clean sheets
- Suggest 1–2 picks and who they could replace

Who should I bring in this week and why?"""

# ---------------- UI ----------------
st.set_page_config(page_title="Draft Waiver Assistant", page_icon="⚽", layout="centered")
st.title("FPL DraftFantasy — Waiver Assistant (LIVE)")
st.caption("Now using /api/league/{league}/transfers-data • no caching • timestamp below")

with st.expander("Settings", expanded=False):
    league_id = st.text_input("League ID", value=DEFAULT_LEAGUE_ID)
    team_id   = st.text_input("Team ID",   value=DEFAULT_TEAM_ID)

# Fetch live league ownership and rosters
try:
    owner_by_name, players_by_team = fetch_live_league(league_id)
except Exception as e:
    st.error(f"Could not fetch live league data: {e}")
    st.stop()

# Fetch CSV stats
try:
    r = requests.get(CSV_URL, timeout=20); r.encoding = "utf-8"
    df_pool = pd.read_csv(io.StringIO(r.text))
except Exception as e:
    st.error(f"Could not fetch CSV stats: {e}")
    st.stop()

# Normalize + rename for consistent columns
df_pool["Name_norm"] = df_pool["Name"].apply(normalize_name)
df_pool = df_pool.rename(columns={
    "PointsPerGame": "Point per game",
    "Goals Conceded": "Goals conceded",
    "Yellow Cards": "Yellow cards",
    "Red Cards": "Red cards",
    "Clean Sheets": "Clean sheets",
})

# Ownership (LIVE) by name
df_pool["Owner"] = df_pool["Name_norm"].map(owner_by_name).fillna("-")

# Build 'My Squad' (classify by position using CSV after mapping)
my_names_set = set(players_by_team.get(team_id, []))
if my_names_set:
    # Join to CSV to obtain positions
    df_my = df_pool[df_pool["Name"].isin(my_names_set)].copy()
    lines = {
        "GK": ", ".join(df_my[df_my["Position"]=="GK"]["Name"].tolist()),
        "DEF": ", ".join(df_my[df_my["Position"]=="DEF"]["Name"].tolist()),
        "MID": ", ".join(df_my[df_my["Position"]=="MID"]["Name"].tolist()),
        "FWD": ", ".join(df_my[df_my["Position"]=="FWD"]["Name"].tolist()),
    }
    my_squad_text = "\n".join([
        f"GK: {lines['GK']}",
        f"DEF: {lines['DEF']}",
        f"MID: {lines['MID']}",
        f"FWD: {lines['FWD']}",
    ])
else:
    my_squad_text = ""

st.subheader("Your Squad")
st.text(my_squad_text if my_squad_text.strip() else "No players found for this Team ID. Double‑check Settings.")

# Available players = unowned (live)
df_avail = df_pool[df_pool["Owner"] == "-"].copy()

# Prompt + table
prompt = build_prompt(df_avail, my_squad_text)
st.subheader("Copy‑and‑paste Prompt")
st.code(prompt)

st.subheader("Top 25 (by Points per game)")
cols = ["Name", "Club", "Position", "Point per game", "Goals", "Assists", "Clean sheets"]
st.dataframe(df_avail.sort_values("Point per game", ascending=False).head(25)[cols], use_container_width=True)

st.download_button("Download prompt (.txt)", data=prompt, file_name="waiver_prompt.txt", mime="text/plain")

# Timestamp
st.caption("⏱️ Last updated: " + time.strftime("%Y-%m-%d %H:%M:%S"))
