# ForgePulse playground Worker

This folder is the free live product at `https://forgepulse.kfairley9999.workers.dev`.

It serves the playground and the workspace API against a Cloudflare D1 database (`forgepulse` / binding `DB`). Each visitor gets an isolated workspace. Secrets are wrapped with `FORGEPULSE_MASTER_KEY`, which is a Worker secret, not a file in this repo.

```bash
npm install
node build.mjs
npx wrangler deploy --dry-run
# authenticated deploy (Wrangler or the Cloudflare API):
npx wrangler deploy
```

The Python package in the repository root is the source of truth for tests, SQLite parity, and the Docker image. This Worker is the production-facing port of that API on the free Workers + D1 tier.
