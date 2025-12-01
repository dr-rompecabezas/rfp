# RFP Monitor

Lightweight scraper that polls a curated set of nonprofit/public RFP sources, deduplicates links, and optionally emails a daily summary.

## Quick start
- Python 3.11+. Create env: `python -m venv .venv && source .venv/bin/activate`.
- Install deps (pip): `pip install --upgrade pip && pip install beautifulsoup4 pyyaml requests`.
- Configure sources/email in `sources.yaml` (SMTP password via `MAILTRAP_TOKEN` or inline `password`; optional `username_env` if you want the username from an env var; `from` sets the visible From address).
- Run once: `python rfp_monitor.py` (email send is skipped if no password is set).

## How it works
1. Loads `sources.yaml` for URLs plus include/exclude keyword filters.
2. Fetches each source, filters links by keywords, and skips previously seen URLs stored in `seen.json`.
3. Prints a report and, if enabled, emails it.

## Files
- `rfp_monitor.py` — main script.
- `sources.yaml` — source list + SMTP config.
- `seen.json` — auto-created URL cache (ignored by git).
- `sources.md` — background research; not used by the script.
- `pyproject.toml` / `uv.lock` — dependencies.

## Testing
- Install `pytest`: `pip install pytest`.
- Run smoke tests: `pytest`.

## GitHub Actions
- Workflow `.github/workflows/rfp-monitor.yml` runs daily and on demand.
- It restores the last `seen.json` artifact (if any), installs `requirements.txt`, exports `MAILTRAP_TOKEN` from `secrets.MAILTRAP_TOKEN`, runs the monitor, then uploads the updated `seen.json` artifact.
- Add the `MAILTRAP_TOKEN` secret in the repo for email delivery.

## Scheduling (optional)
- Cron example: `0 8 * * * /path/to/.venv/bin/python /path/to/rfp_monitor.py >> /var/log/rfp_monitor.log 2>&1`
- Ensure env vars (`MAILTRAP_TOKEN`) are exported in the cron environment.
