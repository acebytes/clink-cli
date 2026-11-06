# Clink development

The [documentation index](docs/index.md) covers installation and protocols. `server.py` exposes MCP tools; `clink/cli.py` exposes the standalone CLI. Keep advisory consultations, explicit native tasks, and programmatic generation separate. Do not weaken protected recipes or change admission pins to make tests pass. Authenticate through the CLI itself. Never commit credentials, raw prompts, session logs or user configuration.

Install development dependencies with `python -m pip install -r requirements-dev.txt -e .`. Run `python -m pytest tests -m "not integration and not real_cli and not private_evidence"`, `ruff check .`, `black --check .` and `isort --check-only .`. See the [release evidence policy](docs/release-evidence.md) for separate provenance checks. Live agent submissions need explicit consent. Do not count an offline test as a live run.
