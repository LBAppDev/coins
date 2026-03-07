# coins automation

## Requirements
- Python 3.11+
- A Discord user token and target channel access

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

## Deploy on Koyeb
This app now exposes a simple health endpoint on `0.0.0.0:$PORT` (`/` returns `ok`), so it can run as a Web service.

1. Push this repository to GitHub.
2. In Koyeb, create a new service from your GitHub repo.
3. Choose **Web** type (or Worker if you prefer).
4. Build method:
   - Dockerfile (uses the included `Dockerfile`), or
   - Buildpacks (the included `Procfile` defines `worker: python main.py`)
5. Set all required environment variables from `.env.example`.
6. Deploy.

## Notes
- Do not commit your real `.env`.
- The process is long-running; Koyeb will keep it alive as a worker.
