# Frontier 0.27.1

## Fixed

- Codex editing sessions now honor the project's Open network setting instead of inheriting Codex's offline workspace-write default. This applies to new and resumed sessions.
- Restricted network settings retain their existing Frontier proxy behavior; unfiltered restricted sessions remain offline.

## Known limitation

Windows-native HTTPS clients (PowerShell and Schannel-based curl) can still encounter `SEC_E_NO_CREDENTIALS` inside the Codex Windows sandbox. This separate TLS issue is not fixed in this release; Python HTTPS was verified to work with network access enabled.
