# Frontier 0.27.10

## Fixes

- Codex no longer reaches for its own built-in browser plugins when Frontier hands it a Playwright browser. Those plugins ask a per-site permission no one can answer during a Frontier turn, so sites like AppSheet were refused. Codex now uses the browser you set up in Frontier, your own Edge when you choose it.
