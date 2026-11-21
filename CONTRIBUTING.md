# Contributing

This repository is a public prototype. The package is not published to PyPI.

```bash
python -m pip install -e ".[dev]"
python -m ruff check src tests
python -m ruff format src tests
python -m mypy --strict src
python -m pytest --cov=forgepulse --cov-fail-under=90
```

Keep policy rules in YAML, not in GitHub UI screenshots. New behavior needs tests. Do not add a default webhook secret.
