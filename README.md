# coins automation

## Requirements
- Python 3.11+
- A Discord user token and target channel access
- Voice support dependency is installed via `PyNaCl` in `requirements.txt`

## Local setup
1. Create an env file from template:
   - Copy `.env.example` to `.env`
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Run:
   - `python main.py`

## Environment variables
- `DISCORD_USER_TOKEN` (required)
- `TARGET_CHANNEL_ID` (required)
- `TARGET_BOT_ID` (optional, recommended)
- `AUTO_MESSAGE` (optional)
- `AUTO_MESSAGE_DELAY_SECONDS` (optional, default: `2.0`)
- `BUY_MONITOR_SECONDS` (optional, default: `20`)
- `TEMP_VOICE_CREATOR_CHANNEL_ID` (optional, default: `1479600922727547043`)
- `VOICE_MOVE_WAIT_SECONDS` (optional, default: `5`)
- `VOICE_MOVE_TIMEOUT_SECONDS` (optional, default: `45`)

## Deploy on Koyeb
Use a **Worker** service (not Web), since this app does not expose HTTP.

1. Push this repository to GitHub.
2. In Koyeb, create a new service from your GitHub repo.
3. Choose **Worker** type.
4. Build method:
   - Dockerfile (uses the included `Dockerfile`), or
   - Buildpacks (the included `Procfile` defines `worker: python main.py`)
5. Set all required environment variables from `.env.example`.
6. Deploy.

## Notes
- Do not commit your real `.env`.
- The process is long-running; Koyeb will keep it alive as a worker.
