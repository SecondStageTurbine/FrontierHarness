Frontier 0.10.0 — tray icon and one instance at a time

- Closing the window keeps Frontier in the system tray, so automations keep firing and finished turns still notify. Open and Quit from the tray menu; a setting turns this off.
- Launching Frontier while it is running brings the existing window forward instead of starting a second copy.
- A backend that finds its data folder in use says so in one line.
- The sidebar no longer repeats the window's own title; its collapse control sits in the session header.
- Styling that was missing since 0.5.0 (git panel, editor, terminal tabs, preview, approval cards, meters and chips, settings pages) now applies: the rules had been written to a stylesheet the app never loaded.
- New application, taskbar, tray, Start menu and desktop shortcut icon.
