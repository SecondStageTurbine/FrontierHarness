## Frontier 0.27.81

- Turns no longer stall before they start. Frontier re-read every PDF in a project before and after each turn; in a folder with many PDFs that kept the app busy for minutes, so a session showed "Working" with nothing happening. PDF text is now read once and reused until the file changes.
- Each chat now has a folder of its own, instead of every chat sharing the Chats folder. Files saved by earlier chats stay where they were, and a chat is told where to find them.
- Undo no longer overwrites a PDF with its extracted text when there is no checkpoint to restore from.
