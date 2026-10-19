## Frontier 0.27.47

- DeepSeek Harness now prefers the authenticated installed `dsh` command-line tool, using the desktop app's embedded headless host only as a fallback.
- This fixes misleading `ACCOUNT_SIGN_IN_REQUIRED` failures when the installed `dsh` profile is already signed in and working.
