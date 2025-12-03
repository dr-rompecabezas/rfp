# Repository Guidelines

## Project Structure & Config

- `rfp_monitor.py` — main polling script (fetches sources, filters links, emails report).
- `sources.yaml` — required config for source list and SMTP settings; keep credentials via `password_env` (default `MAILTRAP_TOKEN`) instead of plain text.
- `sources.md` — research notes on candidate sources; not used by the script.
- `seen.json` — auto-created cache of already reported URLs; treat as ephemeral (avoid committing).
- `pyproject.toml` / `uv.lock` — dependency definitions (Python 3.11+).

## Setup & Development

- Create env: `python -m venv .venv && source .venv/bin/activate`.
- Install deps (either):
  - `uv sync` (preferred if `uv` is installed), or
  - `pip install --upgrade pip && pip install beautifulsoup4 pyyaml requests python-dotenv`.
- **Configure environment variables**: Copy `.env.example` to `.env` and fill in your actual credentials:
  - `MAILTRAP_TOKEN` — SMTP password for email sending
  - `OPENAI_API_KEY` — OpenAI API key for LLM filtering
  - `LLM_ENABLED` — Set to `1` to enable LLM filtering, `0` to disable
- Run the monitor locally: `python rfp_monitor.py`.
- Dry-run email: keep `email.enabled: true` but omit the SMTP password so send step is skipped.

## Coding Style & Naming

- Python, PEP 8, 4-space indents; favor small, pure functions.
- Keep functions side-effect free unless clearly a boundary (HTTP, file I/O, email).
- Use descriptive names: `fetch_source`, `diff_new_items`, `format_report` patterns already present.
- Prefer `Path` over raw strings for file paths; keep UTF-8 encoding explicit when reading/writing.

## Testing Guidelines

- No tests exist yet; add `pytest` for new features.
- Test naming: `test_<module>_<behavior>()`; place files under `tests/`.
- Minimum coverage target: exercise fetch/parse, deduping, and email-disable paths before merging.

## Build, Run, and Verification Commands

- Lint quick check (optional): `python -m compileall rfp_monitor.py` to catch syntax errors.
- Basic health run: `python rfp_monitor.py` (with a tiny source list to avoid noisy output).
- Update dependencies: edit `pyproject.toml` then `uv lock` (or re-run install and commit the refreshed `uv.lock`).

## Commit & Pull Request Guidelines

- Use clear, action-style commits (e.g., `Add email fallback when password missing`); keep one logical change per commit.
- PR checklist: summary of changes, testing notes (commands run), config changes called out, and screenshots for new logs/output when relevant.
- Link issues when applicable; flag any manual steps (env vars, cron setup) in the PR description.

## Security & Ops Notes

- Never commit real SMTP credentials; rely on env vars. Consider adding `seen.json` to `.gitignore` if it becomes noisy.
- If scheduling, run via cron/systemd with the virtual env activated; ensure network egress to listed sources and SMTP host.
