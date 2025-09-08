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
DRAFT_URL = "https://app.draftfantasy.com/api/v1/league/{league_id}/draft"

# Candidate LIVE endpoints (we'll probe them in order)
LIVE_ENDPOINTS = [
    "https://app.draftfantasy.com/api/v1/league/{league_id}/players",
    "https://app.draftfantasy.com/api/v1/league/{league_id}/teams",
    "https://app.draftfantasy.com/api/league/{league_id}/players",
    "https://app.draftfantasy.com/api/league/{league_id}/teams",
]

# --------------- Helpers ----------------
def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def try_get_json(url: str):
    try:
        r = requests.get(url, timeout=20)
        if "application/json" in r.headers.get("Content-Type", ""):
            return r.json()
    except Exception:
        pass
    return None

def fetch_live_rosters(league_id: str):
    """Try multiple endpoints. Return:
       owner_by_pid: {playerId(str) -> teamName}
       ids_by_team:  {teamId(str)   -> [playerId(str), ...]}
       source: url template that worked, else None
    """
    for template in LIVE_ENDPOINTS:
        data = try_get_json(template.format(league_id=league_id))
        if not data:
            continue

        owner_by_pid, ids_by_team = {}, {}

        # Case A: list of dicts (players or teams)
        if isinstance(data, list):
            # assume player-like records
            for item in data:
                pid = str(item.get("playerId") or item.get("id") or item.get("player_id") or "")
                tname = item.get("teamName") or item.get("owner") or item.get("team") or "-"
                tid   = str(item.get("teamId") or item.get("team_id") or "")
                if pid:
                    owner_by_pid[pid] = tname or "-"
                if pid and tid:
                    ids_by_team.setdefault(tid, []).append(pid)

        # Case B: dict with 'players' or 'teams'
        if isinstance(data, dict):
            players = data.get("players") or data.get("data") or []
            if isinstance(players, list) and players:
                for item in players:
                    pid = str(item.get("playerId") or item.get("id") or item.get("player_id") or "")
                    tname = item.get("teamName") or item.get("owner") or item.get("team") or "-"
                    tid   = str(item.get("teamId") or item.get("team_id") or "")
                    if pid:
                        owner_by_pid[pid] = tname or "-"
                    if pid and tid:
                        ids_by_team.setdefault(tid, []).append(pid)

            teams = data.get("teams") or []
            if isinstance(teams, list) and teams:
                for t in teams:
                    tid = str(t.get("teamId") or t.get("id") or t.get("team_id") or "")
                    tname = t.get("teamName") or t.get("name") or ""
                    roster = t.get("players") or t.get("squad") or []
                    ids = []
                    for p in roster:
                        pid = str(p.get("playerId") or p.get("id") or p.get("player_id") or "")
                        if pid:
                            owner_by_pid[pid] = tname or "-"
                            ids.append(pid)
                    if tid and ids:
                        ids_by_team[tid] = ids

        if owner_by_pid or ids_by_team:
            return owner_by_pid, ids_by_team, template

    return {}, {}, None

def fetch_draft_picks(league_id: str):
    r = requests.get(DRAFT_URL.format(league_id=league_id), timeout=20)
    data = r.json()
    return data if isinstance(data, list) else data.get("picks", [])

def build_my_squad_text_from_draft(picks, team_id: str):
    pos_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    my_picks = [p for p in picks if (p.get("teamId")==team_id) or (p.get("team_id")==team_id)]
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
st.title("FPL DraftFantasy — Waiver Assistant")
st.caption("Always live — cache removed")

with st.expander("Settings", expanded=False):
    league_id = st.text_input("League ID", value=DEFAULT_LEAGUE_ID)
    team_id   = st.text_input("Team ID",   value=DEFAULT_TEAM_ID)

# Fetch CSV stats (for PPG etc)
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

# Prefer LIVE rosters; otherwise fallback to /draft
owner_by_pid, ids_by_team, source = fetch_live_rosters(league_id)

if source:
    st.success(f"Using live roster source: {source}")
    # We still need names for the prompt; we’ll rely on /draft names to label ownership
    picks = fetch_draft_picks(league_id)
    owner_map_by_name = { normalize_name(p.get("playerName")): p.get("teamName","-") for p in picks if p.get("playerName") }
    df_pool["Owner"] = df_pool["Name_norm"].map(owner_map_by_name).fillna("-")

    # Build my squad text: try live ids first; if none found, fall back to /draft
    pid_list = ids_by_team.get(team_id, [])
    if not pid_list:
        st.warning("Live roster did not list your team players; falling back to /draft for squad display.")
        my_squad_text = build_my_squad_text_from_draft(picks, team_id)
    else:
        # We don't have a direct id->name join without a live players endpoint; show counts via draft names by teamId
        my_squad_text = build_my_squad_text_from_draft(picks, team_id)
else:
    st.warning("Live roster endpoint not found; using /draft (initial draft only).")
    picks = fetch_draft_picks(league_id)
    owner_map_by_name = { normalize_name(p.get("playerName")): p.get("teamName","-") for p in picks if p.get("playerName") }
    df_pool["Owner"] = df_pool["Name_norm"].map(owner_map_by_name).fillna("-")
    my_squad_text = build_my_squad_text_from_draft(picks, team_id)

# Available players = unowned
df_avail = df_pool[df_pool["Owner"] == "-"].copy()

st.subheader("Your Squad")
st.text(my_squad_text if my_squad_text.strip() else "No squad found. Check Team ID.")

# Prompt + table
prompt = build_prompt(df_avail, my_squad_text)
st.subheader("Copy‑and‑paste Prompt")
st.code(prompt)

st.subheader("Top 25 (by Points per game)")
cols = ["Name", "Club", "Position", "Point per game", "Goals", "Assists", "Clean sheets"]
st.dataframe(df_avail.sort_values("Point per game", ascending=False).head(25)[cols], use_container_width=True)

st.download_button("Download prompt (.txt)", data=prompt, file_name="waiver_prompt.txt", mime="text/plain")

# Timestamp so you can see reruns
st.caption("⏱️ Last updated: " + time.strftime("%Y-%m-%d %H:%M:%S"))
