# Security

ForgePulse is a prototype. Do not expose an unsigned webhook endpoint or treat in-memory state as authoritative.

- Set `FORGEPULSE_WEBHOOK_SECRET` to the GitHub webhook secret before accepting events. Unsigned requests are rejected unless `FORGEPULSE_ALLOW_UNSIGNED=true` *and* no secret is set.
- Never commit secrets, tokens, or `.dev.vars`.
- Report vulnerabilities privately by opening a GitHub security advisory on [kfairy9999/forgepulse](https://github.com/kfairy9999/forgepulse), or by emailing the maintainer through GitHub. Do not include secrets in issues.
