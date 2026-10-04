# Frontier 0.27.6

## Fixed

- Adaptive now recognizes every local model. A model counted as local and free only when its provider was named `ollama` or `lmstudio`; any other OpenCode provider pointing at this machine (a llama.cpp server under its own name, for example) was priced like a paid cloud agent, so a faster cloud agent at the same price won the turn. Any OpenCode provider whose address is on this computer now counts as local and free, so a running local model that covers the task is picked first.
