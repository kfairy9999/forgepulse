# ForgePulse playground Worker

This folder is the free live demo at `https://forgepulse.kfairley9999.workers.dev`.

It serves the same playground page and the triage, blast-radius, search, policy, and CI-queue endpoints against seeded fictional data. State is isolate-local and in-memory. There are no KV, D1, R2, or Durable Object bindings.

```bash
npm install
npm run dry-run
# authenticated deploy:
npm run deploy
```

The Python package in the repository root is the source of truth for tests and the Docker image. This Worker is a port of that logic for a free, always-on demo.
