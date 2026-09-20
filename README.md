# Dynovia Alerts

Private match notifier for Dynovia Dynow: Telegram bot + PWA, fed by scrapers
that run on GitHub Actions. No server, no backend, one user.

    py -3.12 -m pip install -e ".[dev]"
    py -3.12 -m pytest -q

Parser tests run offline against the HTML snapshots in `tests/fixtures/`.
Refresh a snapshot with `py -3.12 -m dynovia.snapshot <source>`.

Secrets live in `.env` locally and in GitHub Secrets in CI - never in the repo.
