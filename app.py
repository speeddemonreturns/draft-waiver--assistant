import io
import re
import time
import unicodedata
import requests
import pandas as pd
import streamlit as st

DEFAULT_LEAGUE_ID = st.secrets.get("LEAGUE_ID", "cmdnhqw1s06g2kv0431dxfade")
DEFAULT_TEAM_ID   = st.secrets.get("TEAM_ID",   "cmdofouqx0009jt04qjgcm5cn")
DEFAULT_TEAM_NAME = st.secrets.get("TEAM_NAME", "")

CSV_URL   = "https://app.draftfantasy.com/api/players/csv"
LIVE_URL  = "https://app.draftfantasy.com/api/league/{league_id}/transfers-data"

def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def extract_player_name(p):
    # Try common locations for the player's name within the roster item
    return (
        p.get("name")
        or (p.get("player") or {}).get("name")
        or p.get("playerName")
        or (p.get("data") or {}).get("name")
        or (p.get("ws") or {}).get("name")
        or (p.get("info") or {}).get("name")
    )

def fetch_live_league(league_id: str):
    url = LIVE_URL.format(league_id=league_id)
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    league = data.get("league", data)
    teams = league.get("teams", [])
    owner_by_name = {}
    players_by_team = {}
    teams_index = []
    total_names = 0
    for t in teams:
        tname = t.get("name") or t.get("teamName") or "-"
        tid = str(t.get("id") or t.get("teamId") or "").strip()
        # try multiple roster keys
        roster = (
            t.get("teamPlayers")
            or t.get("players")
            or t.get("squad")
            or t.get("roster")
            or []
        )
        teams_index.append((tid, tname))
        players_by_team[tid] = []
        for p in roster:
            pname = extract_player_name(p)
            if pname:
                total_names += 1
                owner_by_name[normalize_name(pname)] = tname
                players_by_team[tid].append(pname)
    return owner_by_name, players_by_team, teams_index, total_names

def build_my_squad_text_from_names(names: list, df_pool: pd.DataFrame) -> str:
    if not names:
        return ""
    df_my = df_pool[df_pool["Name"].isin(set(names))].copy()
    lines = {
        "GK": ", ".join(df_my[df_my["Position"]=="GK"]["Name"].tolist()),
        "DEF": ", ".join(df_my[df_my["Position"]=="DEF"]["Name"].tolist()),
        "MID": ", ".join(df_my[df_my["Position"]=="MID"]["Name"].tolist()),
        "FWD": ", ".join(df_my[df_my["Position"]=="FWD"]["Name"].tolist()),
    }
    return "\n".join([
        f"GK: {lines['GK']}",
        f"DEF: {lines['DEF']}",
        f"MID: {lines['MID']}",
        f"FWD: {lines['FWD']}",
    ])

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

st.set_page_config(page_title="Draft Waiver Assistant", page_icon="⚽", layout="centered")
st.title("FPL DraftFantasy — Waiver Assistant (LIVE transfers-data)")
st.caption("No caching • lists team IDs below • robust roster parsing")

with st.expander("Settings", expanded=False):
    league_id = st.text_input("League ID", value=DEFAULT_LEAGUE_ID)
    team_id   = st.text_input("Team ID",   value=DEFAULT_TEAM_ID)
    team_name_hint = st.text_input("Team name (optional fallback)", value=DEFAULT_TEAM_NAME)

# Live league
try:
    owner_by_name, players_by_team, teams_index, total_names = fetch_live_league(league_id)
except Exception as e:
    st.error(f"Could not fetch live league data: {e}")
    st.stop()

# Stats CSV
try:
    r = requests.get(CSV_URL, timeout=20); r.encoding = "utf-8"
    df_pool = pd.read_csv(io.StringIO(r.text))
except Exception as e:
    st.error(f"Could not fetch CSV stats: {e}")
    st.stop()

df_pool["Name_norm"] = df_pool["Name"].apply(normalize_name)
df_pool = df_pool.rename(columns={
    "PointsPerGame": "Point per game",
    "Goals Conceded": "Goals conceded",
    "Yellow Cards": "Yellow cards",
    "Red Cards": "Red cards",
    "Clean Sheets": "Clean sheets",
})

st.caption("Available team IDs in this league:")
for tid, tname in teams_index:
    st.caption(f"• {tname} → {tid}")
st.caption(f"Detected rostered player names: {total_names}")

# Ownership (LIVE) by name
df_pool["Owner"] = df_pool["Name_norm"].map(owner_by_name).fillna("-")
owned_count = (df_pool["Owner"] != "-").sum()
if owned_count == 0:
    st.warning("No owners detected. If you still see all players as 'Free', the roster structure may differ.")

# Locate your team
tid = str(team_id).strip()
my_names = players_by_team.get(tid)

if (not my_names) and team_name_hint:
    for t_tid, t_name in teams_index:
        if t_name.strip().lower() == team_name_hint.strip().lower():
            my_names = players_by_team.get(t_tid)
            break

my_squad_text = build_my_squad_text_from_names(my_names or [], df_pool)

st.subheader("Your Squad")
st.text(my_squad_text if my_squad_text.strip() else "No players found for this Team ID. Double‑check Settings or use the team name fallback.")

# Available players = unowned (live)
df_avail = df_pool[df_pool["Owner"] == "-"].copy()

prompt = build_prompt(df_avail, my_squad_text)
st.subheader("Copy‑and‑paste Prompt")
st.code(prompt)

st.subheader("Top 25 (by Points per game)")
cols = ["Name", "Club", "Position", "Point per game", "Goals", "Assists", "Clean sheets"]
st.dataframe(df_avail.sort_values("Point per game", ascending=False).head(25)[cols], use_container_width=True)

st.download_button("Download prompt (.txt)", data=prompt, file_name="waiver_prompt.txt", mime="text/plain")

st.caption("⏱️ Last updated: " + time.strftime("%Y-%m-%d %H:%M:%S"))
