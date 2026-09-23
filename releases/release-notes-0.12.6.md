Frontier 0.12.6

- Agent tools installed after Frontier started are found without restarting it: Frontier re-reads PATH from the registry, as a new terminal would, and looks in the folders each tool's installer uses (such as ~/.local/bin for Claude Code's native installer).
- When a tool really is missing, the message says where Frontier looked and the exact install and sign-in commands.
