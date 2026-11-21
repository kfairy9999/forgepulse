# Changelog

## 0.2.0 — 2026-11-20

- Durable, isolated workspaces in Cloudflare D1 (live Worker) and SQLite (Python / Docker). State survives restarts.
- Guest "try it" onboarding with sample org data, plus a documented webhook + per-workspace secret path to connect a real GitHub repository. Optional fine-grained PAT fetches changed files and merged-PR counts because native `pull_request` webhooks omit file names.
- Access tokens stored as HMAC hashes; webhook secrets and GitHub tokens wrapped with AES-GCM. Rate limits, body-size caps, CORS, and security headers on mutating and public routes.
- Playground UI covers TriageGate, RunnerMesh (routing decisions only), GraphSync, CodeAtlas, and an editable, validated PolicyVault. History and queue persist per workspace.
- Expanded unit, integration, and GET-only live smoke tests. CI uses `actions/setup-python` v7.1.0 to drop the Node 20 warning.

## 0.1.0 — 2026-11-20

- First public prototype of TriageGate, RunnerMesh, GraphSync, CodeAtlas, and PolicyVault.
- Policy loads from YAML. Webhook signatures fail closed. In-memory state is thread-safe and clearly scoped as non-durable.
- Live Cloudflare Workers playground on seeded fictional data.
