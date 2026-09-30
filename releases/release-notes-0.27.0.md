Frontier 0.27.0

- **Teams always get an independent review.** When the lead's plan has no review task and another model family is connected, Frontier adds one after the work. It goes to a family other than the lead's and the workers', and the reviewer can fix clear defects itself. The lead still reviews at the end.
- **Teams run your tests.** If the project has a test command Frontier recognises (`npm test` with a real test script, or pytest), it runs on the finished work before the lead decides, and again after fixes. A failure the team caused becomes a fix. Tests that still fail are named in the final reply, whatever the lead decided. The run shows in the Terminal panel.
- **Fixes to new files now land.** A fix, or a later task, that changed a file an earlier task had created could fail to merge back ("does not exist in index"), and the change was lost. It now applies.
- **Linux.** An AppImage and a .deb are built for every release from this one on, and the in-app updater serves Linux too.
