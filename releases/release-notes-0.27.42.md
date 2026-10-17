## Frontier 0.27.42

- Adaptive no longer sends a turn to a local model whose server is already busy with Frontier turns in every slot (llama.cpp's --parallel). The next cheapest capable agent takes it instead, so several sessions stop queueing behind one GPU. If no other agent can take the turn, it still goes to the busy server.
- The routing details name any local agent that was passed over as busy.
