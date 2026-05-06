# Argus recovery channel — design

**Status:** design doc, not implemented. Triggers ≤ once per 1-2 years.

**Date:** 2026-05-07

## Why we need it

Argus brain server (currently at `52.62.80.197`) is the sole authorised OTA writer for the fleet, per the 5-layer lockdown:

- DNS sinkhole blocks `ota.hub.jupyter.com.au`, `ota.dev.jupyter.com.au`, `ghcr.io`, `pkg-containers.githubusercontent.com`, `vm.dev.jupyter.com.au`
- systemd masks `hub-manager-update.timer`, `apt-daily*`, `unattended-upgrades`
- `chattr +i` on 30+ scripts in `/usr/local/bin`, every AI source dir, `docker-compose.yml`, `daemon.json`
- Identity guard restores from backup if zeroed without reset-intent flag
- Local-image dual-tagging prevents `docker compose up -d` from touching a registry

**Risk:** if Argus brain EC2 IP changes (already happened once this year: 15.134.147.136 → 52.62.80.197) AND the DNS for `argus.jupyterdevices.com` is stale (also currently broken — CF proxy not forwarding to new IP), every fielded hub's `secureprotect-vmagent.service` keeps pushing to the old IP, and there's no path to update them.

The current single-channel design is **fine** for steady state. But it has **no recovery channel** for the once-yearly migration event.

## The threat model

What can go wrong, ranked by likelihood:

1. **Argus EC2 IP migration** (likely: AWS sometimes forces this, or we move regions) — fielded hubs can't push metrics, and crucially, can't *receive* updates. Without a recovery channel, every hub becomes "maintenance-mode locked" until manually visited.
2. **Argus EC2 destroyed** (unlikely but possible: subscription lapse, account compromise) — same outcome. Worse: if the OTA write was about to push a critical security fix, fleet stays unpatched.
3. **Domain hijack** of `jupyterdevices.com` or `jupyter.com.au` (unlikely but irrecoverable) — without a recovery channel, every hub trying to reach Argus follows the new (malicious) DNS and gets adversary-controlled commands. **This is the worst case.**
4. **Cloudflare tunnel revocation / change** (unlikely, dependent on CF policy) — affects remote SSH and HTTP access but not the Argus push direction (vmagent uses IP).

## Design — signed manifest from independent domain

Recovery channel principle: **a hub should be able to discover a NEW Argus location via a separately-rooted, cryptographically-signed manifest**, served from a domain that's NOT under our day-to-day control plane.

### Components

```
+-----------------------+
| recovery.jupyter.com.au (registered separately, e.g. on
| different registrar, with multi-year auto-renew on a
| different credit card; RFC8484 DoH or static IP fallback)
|                                                            |
| Hosted manifest at https://recovery.jupyter.com.au/v1/manifest.json
|                                                            |
| {
|   "argus_brain_endpoints": ["52.62.80.197", "<new IP>"],
|   "argus_brain_dns_aliases": ["argus.jupyterdevices.com"],
|   "issued_at": "2026-05-07T00:00:00Z",
|   "expiry": "2027-05-07T00:00:00Z",
|   "signature": "<ed25519 signature of the above payload>"
| }
+-----------------------+
            |
            | (hub fetches this manifest weekly via cron)
            v
+-----------------------+
| Hub: /usr/local/bin/argus-recovery-check.sh
| 1. Try to push to current Argus brain — if OK, do nothing
| 2. If brain unreachable for >72h:
|    - Fetch recovery manifest
|    - Verify signature against baked-in public key
|    - Verify expiry > now
|    - If new endpoint differs from current: update vmagent
|      remoteWrite URL + restart secureprotect-vmagent.service
+-----------------------+
```

### Key properties

| Property | How |
|---|---|
| **Independent of regular control plane** | Different domain, different registrar, different DNS provider. Compromise of `jupyterdevices.com` doesn't cascade. |
| **Signed** | Ed25519 keypair. Public key baked into hub gold image (in `/usr/local/share/jupyter-recovery.pub`). Private key offline-stored on a hardware key (e.g., Yubikey held by 2 people in escrow). |
| **Time-bounded** | Each manifest expires in 1 year. Hub rejects stale manifests — forces yearly re-issuance. |
| **Pull, not push** | Hub fetches every 7 days. No inbound port. Can't be DDoS'd from outside. |
| **Fail-closed on tampering** | Bad signature, expired, missing fields → hub does NOT update endpoint. Stays on old. |
| **Observable** | Each fetch attempt logged to journald + emitted as a metric `argus_recovery_check_*`. |

### What gets shipped to the hub

In the gold image:

- `/usr/local/bin/argus-recovery-check.sh` — the polling script
- `/etc/systemd/system/argus-recovery-check.{service,timer}` — weekly trigger
- `/usr/local/share/jupyter-recovery.pub` — Ed25519 public key (~32 bytes)
- `/etc/jupyter-recovery.conf` — config file with the recovery URL hardcoded

### What stays cloud-side (Argus team's work)

- Domain registration + DNS + TLS for `recovery.jupyter.com.au`
- A static-hosted manifest (S3 + CloudFront, or similar) — independent of Argus EC2
- Tooling to mint + sign new manifests (offline; hardware key)
- Yearly rotation runbook for the manifest

### Failure modes + mitigations

| Failure | Mitigation |
|---|---|
| Recovery domain expires | Auto-renew on multi-year basis, with 90-day before-expiry alert to a separate ops channel |
| Recovery domain hijacked | Ed25519 signature check fails on a fake manifest — hub stays on last-known-good Argus endpoint; alerts via vmagent metric |
| Public key compromised | Re-key by issuing a manifest with the new public key in a `next_pubkey` field; hubs accept manifests signed by either key for 30 days, then rotate |
| Both old and new Argus down + recovery channel down | Hub stays "frozen" on last-known config but continues serving customers locally. Eventually a physical visit. This is the absolute worst case and the lowest-likelihood; design accepts it. |

## Implementation estimate

- Cloud + ops: **~3 days** (domain registration, manifest hosting, signing tooling, runbook)
- Hub: **~2 days** (recovery check script, systemd unit, public key bake, integration test)
- Pilot integration: **~1 day** (verify in 5-hub test group)

**Total: ~1 week**, can be deferred to post-pilot.

## Trigger to build

- Pilot ships ✓
- 100+ hubs in the field
- OR: Argus brain IP migration is forecast within 6 months
- OR: any hint of domain compromise or registrar issue

Until then, document the manual override:

## Manual override (if Argus migrates before recovery channel ships)

If the brain IP changes and there's no recovery channel:

1. **Don't panic.** Hubs continue serving customers locally (CF tunnel for remote, AI containers process events, alarms work, etc.). Only OTA + metrics reporting are affected.
2. SSH into the brain via Cloudflare tunnel. Identify all hubs that have stopped pushing metrics in the last 24h.
3. For each affected hub: SSH in (via CF tunnel), update `/etc/secureprotect/agent.json` `brain_ip` field + `/etc/hosts` for `brain.argus.jupyterdevices.com`, restart `secureprotect-vmagent.service`.
4. With ~100 hubs this takes ~3 hours. With 10,000 hubs this is impossible — which is exactly why this recovery channel matters.

## Related

- `~/.claude/projects/-Users-topsycombs/memory/argus-monitoring.md`
- `~/.claude/projects/-Users-topsycombs/memory/feedback-no-otherwise-ota.md`
