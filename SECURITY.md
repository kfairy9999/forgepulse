# Security

- Workspace tokens are shown once at creation (and kept in the browser for the playground). They are stored only as HMAC-SHA256 hashes. Treat a leaked token as a leaked workspace: rotate by creating a new workspace, or rotate the webhook secret if only the webhook secret leaked.
- Webhook secrets and GitHub tokens are encrypted with AES-GCM under `FORGEPULSE_MASTER_KEY`. Never commit that key, `.data/master.key`, `.dev.vars`, or a personal access token. The live Worker receives the master key as a Workers secret binding, not from the repository.
- `POST /w/{workspace_id}/webhooks/github` requires a valid `X-Hub-Signature-256` for that workspace's secret. Unsigned and badly signed deliveries are rejected and recorded.
- Mutating HTTP APIs (and workspace reads) require `Authorization: Bearer` or `X-ForgePulse-Token`.
- Report vulnerabilities privately by opening a GitHub security advisory on [kfairley9999/forgepulse](https://github.com/kfairley9999/forgepulse), or by emailing the maintainer through GitHub. Do not include secrets in issues.
