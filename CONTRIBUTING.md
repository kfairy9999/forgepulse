# Contributing

This repository is a public MIT project. The package is not published to PyPI or npm.

```bash
python -m pip install -e ".[dev]"
python -m ruff check src tests
python -m ruff format src tests
python -m mypy --strict src
python -m pytest --cov=forgepulse --cov-fail-under=90
```

The live playground is the TypeScript Worker in `demo/`. After editing `demo/playground.html`, run `node demo/build.mjs` so `demo/src/page.ts` stays in sync. Keep RunnerMesh labeled as routing-only. New behavior needs tests. Do not add a default webhook secret or commit `FORGEPULSE_MASTER_KEY`.
