# Running QuoteDesk

## Locally (about 10 minutes the first time)

1. **Install Python 3.11 or newer** from python.org. On Windows, tick **"Add python.exe to PATH"** in the installer.
2. **Get an API key:** console.anthropic.com → Billing (add ~$10 credit) → API Keys → Create key.
3. **Unzip** this folder anywhere.
4. **Start it**
   - Windows: double-click `run.bat`. The first time, it installs packages and opens `.env` in Notepad.
     Paste your key after `ANTHROPIC_API_KEY=`, save, close, and double-click `run.bat` again.
   - Mac: open Terminal in the folder, then `chmod +x run.sh && ./run.sh`, paste the key into `.env`, run `./run.sh` again.
5. The app opens at http://localhost:8501.
6. Go to **2 · Inbox → Read all responses**. It reads all five vendors in parallel (about 1–3 minutes, roughly $1).
   Results are cached in `cache/`, so the demo is instant afterwards. Use "Re-read with AI" on a vendor
   to show it live.
7. Optional: measure accuracy against the answer key: `python tests/eval_extraction.py`
   (activate the venv first: `.venv\Scripts\activate` on Windows, `source .venv/bin/activate` on Mac).

Manual install instead of the scripts: `python -m venv .venv`, activate it, `pip install -r requirements.txt`,
copy `.env.example` to `.env` and add the key, then `streamlit run app.py`.

## Online (Streamlit Community Cloud, free)

1. **Read the replies once locally** (step 6 above) so `cache/extractions/` holds the AI's reads.
   They're committed with the code, so the hosted app opens instantly and costs nothing until someone re-reads.
2. **Push to GitHub:** create a repository (private is fine) and upload this folder.
   `.env` is excluded by `.gitignore`, so your key never goes to GitHub.
3. Go to **share.streamlit.io** → sign in with GitHub → **Create app** → pick the repository, branch `main`,
   main file `app.py` → under **Advanced settings** choose Python 3.12 and paste into **Secrets**:
   ```
   ANTHROPIC_API_KEY = "sk-ant-..."
   APP_PASSWORD = "a-password-for-viewers"
   ```
4. **Deploy.** You get a link like `https://<name>.streamlit.app`; that's the live link. The GitHub URL is
   the "where it's hosted" link.
5. **Protect your credit:** set a monthly spend limit in the Anthropic console (Settings → Limits).

Notes for the hosted demo: everyone using the link shares the same review decisions (use
*Review queue → Audit trail → Reset all decisions* before a demo), and the app sleeps after a few days
without visits; opening the link wakes it in under a minute.
