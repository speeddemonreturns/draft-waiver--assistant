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
DEFAULT_TEAM_NAME = st.secrets.get("TEAM_NAME", "")  # optional fallback

CSV_URL   = "https://app.draftfantasy.com/api/players/csv"
LIVE_URL  = "https://app.draftfantasy.com/api/league/{league_id}/transfers-data"

# FPL (free, no auth)
FPL_BOOTSTRAP = "https://fantasy.premierleague.com/api/bootstrap-static/"
FPL_FIXTURES  = "https://fantasy.premierleague.com/api/fixtures/?future=1"

# --------------- Helpers ----------------
def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def minmax(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").fillna(0)
    if s.max() == s.min():
        return pd.Series(0.0, index=s.index)
    return (s - s.min()) / (s.max() - s.min())

def extract_player_name(p):
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

def get_fpl_data():
    bs = requests.get(FPL_BOOTSTRAP, timeout=20).json()
    fixtures = requests.get(FPL_FIXTURES,  timeout=20).json()
    fpl_players = pd.DataFrame(bs["elements"])[[
        "id","first_name","second_name","web_name","team","status",
        "chance_of_playing_next_round","news","ict_index","form"
    ]].copy()
    fpl_teams = pd.DataFrame(bs["teams"])[["id","name","short_name"]].copy()
    return fpl_players, fpl_teams, fixtures

# Club label mapping (DraftFantasy CSV -> FPL short names)
CLUB_TO_SHORT = {
    "Arsenal":"ARS","Aston Villa":"AVL","Bournemouth":"BOU","Brentford":"BRE","Brighton":"BHA",
    "Chelsea":"CHE","Crystal Palace":"CRY","Everton":"EVE","Fulham":"FUL","Ipswich":"IPS",
    "Leicester":"LEI","Liverpool":"LIV","Man City":"MCI","Man Utd":"MUN","Newcastle":"NEW",
    "Nott'm Forest":"NFO","Nottingham Forest":"NFO","Southampton":"SOU","Spurs":"TOT","West Ham":"WHU","Wolves":"WOL",
}

def club_next_fdr_lookup(fpl_teams: pd.DataFrame, fixtures: list):
    future = pd.DataFrame(fixtures)
    next_events = sorted(set(future["event"].dropna().unique()))
    next_gw = next_events[0] if next_events else None
    team_fdr = {}
    if next_gw is not None:
        fgw = future[future["event"] == next_gw]
        for _, row in fgw.iterrows():
            team_fdr[row["team_h"]] = row["team_h_difficulty"]
            team_fdr[row["team_a"]] = row["team_a_difficulty"]
    short_to_id = dict(zip(fpl_teams["short_name"], fpl_teams["id"]))
    def club_next_fdr(club: str) -> float | None:
        short = CLUB_TO_SHORT.get(club)
        if not short: 
            return None
        tid = short_to_id.get(short)
        return team_fdr.get(tid)  # 1 easy .. 5 hard
    return club_next_fdr

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

def build_prompt(df_top: pd.DataFrame, my_squad_text: str) -> str:
    def rowline(r):
        news_short = (str(r.get("news") or "").split(".")[0])[:120]
        avail = r.get("chance_of_playing_next_round")
        avail_txt = f"{int(avail)}%" if pd.notna(avail) else "—"
        ease = r.get("FixtureEase")
        ease_txt = f"{ease:.1f}" if pd.notna(ease) else "—"
        return (
            f"{r['Name']} ({r['Position']}, {r['Club']}) – "
            f"PPG:{r.get('Point per game',0)}, ICT:{r.get('ict_index','—')}, "
            f"Ease:{ease_txt}, Avail:{avail_txt}, Note:{news_short} 🟢 Free"
        )
    avail_text = "\n".join(rowline(r) for _, r in df_top.iterrows())
    return f"""🟩 My Squad:
{my_squad_text}

🟢 Top 25 (composite score):
{avail_text}

🎯 Rules:
- Only suggest players not already owned
- Blend PPG, ICT, and fixture ease; prefer high minutes and good availability
- Suggest 1–2 picks and who they could replace

Who should I bring in this week and why?"""

# ---------------- UI ----------------
st.set_page_config(page_title="Draft Waiver Assistant", page_icon="⚽", layout="centered")
st.title("FPL DraftFantasy — Waiver Assistant (LIVE + FPL metrics)")
st.caption("Live ownership from transfers-data • FPL availability/ICT/FDR • composite ranking • timestamp below")

with st.expander("Settings", expanded=False):
    league_id = st.text_input("League ID", value=DEFAULT_LEAGUE_ID)
    team_id   = st.text_input("Team ID",   value=DEFAULT_TEAM_ID)
    team_name_hint = st.text_input("Team name (optional fallback)", value=DEFAULT_TEAM_NAME)

with st.expander("Ranking weights", expanded=False):
    w_ppg  = st.slider("Weight: Points per game", 0.0, 2.0, 1.0, 0.05)
    w_ict  = st.slider("Weight: ICT Index",       0.0, 2.0, 0.6, 0.05)
    w_fix  = st.slider("Weight: Fixture ease",    0.0, 2.0, 0.8, 0.05)
    w_form = st.slider("Weight: Form (last 5)",   0.0, 2.0, 0.6, 0.05)
    min_avail = st.selectbox("Availability filter", ["All","75%+ only"], index=1)

# Live league
owner_by_name, players_by_team, teams_index, total_names = fetch_live_league(league_id)

# CSV stats
r = requests.get(CSV_URL, timeout=20); r.encoding = "utf-8"
df_pool = pd.read_csv(io.StringIO(r.text))
df_pool["Name_norm"] = df_pool["Name"].apply(normalize_name)
df_pool = df_pool.rename(columns={
    "PointsPerGame": "Point per game",
    "Goals Conceded": "Goals conceded",
    "Yellow Cards": "Yellow cards",
    "Red Cards": "Red cards",
    "Clean Sheets": "Clean sheets",
})

# FPL data
fpl_players, fpl_teams, fpl_fixtures = get_fpl_data()
club_next_fdr = club_next_fdr_lookup(fpl_teams, fpl_fixtures)

# Fixture ease
df_pool["FDR_next"] = df_pool["Club"].apply(club_next_fdr)
df_pool["FixtureEase"] = df_pool["FDR_next"].apply(lambda d: None if pd.isna(d) else 6 - d)

# Merge FPL player info
def norm(s): 
    return re.sub(r"\s+"," ",unicodedata.normalize("NFKD", str(s)).encode("ascii","ignore").decode("ascii").lower()).strip()
fpl_players["name_key"] = fpl_players["web_name"].map(norm)
df_pool["name_key"]     = df_pool["Name"].map(norm)
fpl_sub = fpl_players[["name_key","status","chance_of_playing_next_round","news","ict_index","form"]].copy()
df_pool = df_pool.merge(fpl_sub, on="name_key", how="left")

# Availability signals
df_pool["Available_nextGW"] = (
    (df_pool["status"].isin(["a","d"])) &
    (df_pool["chance_of_playing_next_round"].fillna(100) >= 75)
)
df_pool["Returning_flag"] = df_pool["news"].fillna("").str.contains("available|returned|back|fit", case=False, regex=True)

# Ownership (LIVE) by name
df_pool["Owner"] = df_pool["Name_norm"].map(owner_by_name).fillna("-")

# Show team ids
st.caption("Available team IDs in this league:")
for tid, tname in teams_index:
    st.caption(f"• {tname} → {tid}")
st.caption(f"Detected rostered player names: {total_names}")

# My squad
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

# Filter to truly free
df_avail = df_pool[df_pool["Owner"] == "-"].copy()
if min_avail == "75%+ only":
    df_avail = df_avail[(df_avail["Available_nextGW"]) | (df_avail["chance_of_playing_next_round"].isna())]

# Composite score
df_avail["_ppg_n"]  = minmax(df_avail["Point per game"])
df_avail["_ict_n"]  = minmax(df_avail["ict_index"])
df_avail["_fix_n"]  = minmax(df_avail["FixtureEase"].fillna(0))
df_avail["_form_n"] = minmax(df_avail["form"])
df_avail["Score"] = (
    w_ppg*df_avail["_ppg_n"] + 
    w_ict*df_avail["_ict_n"] + 
    w_fix*df_avail["_fix_n"] + 
    w_form*df_avail["_form_n"]
    + 0.05*df_avail["Returning_flag"].fillna(False).astype(int)
)

rank_cols = ["Name","Club","Position","Point per game","Goals","Assists","Clean sheets",
             "FixtureEase","ict_index","chance_of_playing_next_round","news","Score"]

df_top = df_avail.sort_values("Score", ascending=False).head(25)

# Prompt + table
prompt = build_prompt(df_top, my_squad_text)
st.subheader("Copy‑and‑paste Prompt")
st.code(prompt)

st.subheader("Top 25 (composite score)")
st.dataframe(df_top[rank_cols], use_container_width=True)

st.download_button("Download prompt (.txt)", data=prompt, file_name="waiver_prompt.txt", mime="text/plain")

st.caption("⏱️ Last updated: " + time.strftime("%Y-%m-%d %H:%M:%S"))
