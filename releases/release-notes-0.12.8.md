Frontier 0.12.8

- Claude Code installed through npm is found again. Newer releases ship a native bin/claude.exe in the npm package instead of the old cli.js; Frontier now reads the program each npm package declares in its package.json, runs a native executable directly, and uses Node only for a JavaScript entry point. Older cli.js installs keep working.
