# Frontier 0.27.20

## Fixes

- Fewer leftover tabs in your own Chrome or Edge. Every agent turn connects to the browser afresh, and the Playwright extension opens a "Welcome" tab for each connection that it never closes, so a long goal or team left dozens behind. Agents now browse in that Welcome tab instead of opening another, and close it as their last browser step.
