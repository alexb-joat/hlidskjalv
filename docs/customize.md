# Make it yours

Look, background, layout, features and sources, all in Settings (the gear icon).

[← Back to the README](../README.md)

---

The first time you open it, a short setup asks how it should look; skip it and everything stays at sensible defaults. Change anything later in **Settings** (the gear icon):

- **Look:** light, dark or follow the system; seven palettes, or your own accent, surface and text colours picked on a colour wheel (with a readability check); brightness; fourteen fonts; text size, density, corner rounding, animations.
- **Background:** plain, gradient, glow, or slow particles (they pause when the tab is hidden and respect your system's reduce-motion setting).
- **Layout:** the icon navigation at the top, bottom, left or right, with or without names; which views to show, in what order, and which one opens first.
- **Features and sources:** the in-page terminal, levels and XP, Trioditis's log, Claude Code, and folders to leave out of the map. Up to 5,000 files are watched (build and library folders are skipped already); past that the page says how many were left out, and *Skip folders* in Settings → Sources brings a big project back under the cap. Each source polls on its own thread, so a slow one (a huge folder, a sluggish system journal) never holds the others back; a source that hasn't answered for 30 seconds shows a "stalled" pill next to the source chips.
- **Your settings:** export, import, reset, or run the setup again.

The live map starts with the last seven days of earlier sessions (up to 3000 events), so a restart doesn't wipe the activity log, and lifetime XP is kept in `~/.hlidskjalv/progress.json` even after old sessions are pruned.

Settings are saved in `~/.hlidskjalv/settings.json` and checked on every change. Changing them needs a paired browser (see [Pairing](pairing.md)).
