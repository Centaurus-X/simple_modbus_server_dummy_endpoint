# Contributing

Thank you for helping improve Advanced Modbus Simulation Server.

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Quality checks

Run the checks before opening a pull request:

```bash
python -m unittest discover -s tests -v
python -m compileall .
node --check web_interface/app.js
```

## Contribution guidelines

- Keep all source files, documentation, comments and user-facing strings in English.
- Keep the single-file server importable and executable.
- Avoid committing runtime logs, snapshots, generated history files, virtual environments or cache directories.
- Add or update tests when changing runtime history, WebSocket messages, Modbus data block behavior or timing configuration.
- Keep compatibility with `pymodbus>=3.5,<3.13` unless the datastore implementation is migrated deliberately.

## Pull request checklist

- The test suite passes.
- The Web UI JavaScript passes syntax validation.
- Documentation has been updated for behavioral changes.
- Runtime artifacts are not included in the commit.
- Public API or message format changes are documented in `docs/components.md`.
