Frontier 0.19.2: a leaner, quicker Frontier

- The sidebar and the projects dashboard, which refresh every few seconds, no longer read every message and file change of every session to show a status.
- A turn starts and ends sooner: its snapshot of the project and its git checkpoint run together, and the project folder is looked up once per scan instead of once per file.
- Live conversations poll only when the event stream goes quiet; resuming a Claude conversation reads just that conversation.
- About 34 KB of stylesheet for screens that no longer exist is gone, along with unused code; nothing you see changes.
