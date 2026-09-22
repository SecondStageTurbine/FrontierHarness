Frontier 0.5.0 — queue and steer, turn stats, notifications, session management, auto-update

- Enter while a turn is running queues the message for when it finishes; Ctrl+Enter stops the turn and sends the new message instead. Queued messages are listed under the conversation and can be removed.
- Each finished turn shows its duration, tokens in and out, cost when the model has rates, and how many agents Adaptive tried.
- A turn that finishes while Frontier is in the background raises a desktop notification with a chime. Settings → General controls it.
- Right-click a session to rename, pin, or archive it.
- Frontier now checks GitHub releases at launch and installs signed updates in place. This is the first version that can update itself.
