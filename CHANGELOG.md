# Changelog

## 0.3.2 — 2026-11-16

- HACS compatibility: ship a 256 × 256 PNG icon and logo in `custom_components/rtl_sdr/brand/` so the HACS branding validator can recognize the integration.
- HACS validation now checks out repository files and uses SHA-pinned Actions before inspecting the branding assets.
- Preserve v0.3.1 network isolation, bearer authentication, and telemetry redaction. No engine or SDR protocol changes in this follow-up.

## 0.3.1 — 2026-11-16

- Security: local-only Docker bridge binding by default, with explicit trusted LAN binding for remote Home Assistant hosts.
- Do not log untrusted WebSocket JSON messages on parse failure.
- Require validation runs from pushes to this repository's main before release publication.
- Add source-level security regression checks and CodeQL scanning.
- Validate import compatibility against Home Assistant Core 2026.11.1.
- Preserve built-in local RTL-SDR mode, managed application, remote engine API, entity IDs, receiver jobs and existing environment overrides.
- Physical receiver interoperability is not verified by CI.
