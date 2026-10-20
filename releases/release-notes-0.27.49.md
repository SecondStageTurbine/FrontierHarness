## Frontier 0.27.49

- Conversations automatically compact completed history at 80% context usage, preserving the current request and keeping hard-limit message dropping as a fallback.
- Remembering a conversation now reconciles new facts with existing project memory, removing duplicates, preferring newer facts when they conflict, and preserving dated notes within the 20,000-character limit.
