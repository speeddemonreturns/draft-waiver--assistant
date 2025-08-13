# Draft Waiver Assistant (Streamlit)

A tiny mobile-friendly web app that builds a GPT prompt for FPL DraftFantasy waivers.

## How it works
- Reads player stats CSV and league draft picks from DraftFantasy public endpoints.
- Blocks owned players, shows top 25 unowned by Points per game.
- Formats a ready-to-copy prompt.
- No Google auth, no secrets required (optional Secrets for IDs).

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
Then open the local URL in your browser.

## Deploy on Streamlit Community Cloud
1. Push this repo to GitHub.
2. Go to https://share.streamlit.io/ → Deploy app.
3. Set **app.py** as the entry point.
4. (Optional) In **Settings → Secrets** add:
```
LEAGUE_ID = "cmdnhqw1s06g2kv0431dxfade"
TEAM_ID   = "cmdofouqx0009jt04qjgcm5cn"
```
5. Open the app URL on your phone. In Safari: Share → Add to Home Screen.
