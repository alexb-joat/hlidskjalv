# Browsers and systems

What the page and the server need.

[← Back to the README](../README.md)

---

Hlidskjalv is one page of plain HTML, CSS and JavaScript with no build step. It needs a browser from 2023 or later:

| Browser | From | Notes |
|---|---|---|
| Chrome, Edge, Brave, Opera | 111 | |
| Firefox | 113 | ESR 115 and later included |
| Safari | 16.2 (macOS 11.7, 12.6, 13.1; iOS 16.2) | `hlidskjalv.localhost` needs a line in `/etc/hosts` (see [Pairing](pairing.md)); SVG icons are replaced by PNG ones automatically |

Older browsers still show the page, only with plainer colours (the derived shades fall back to fixed ones). The server side runs on Linux, macOS and Windows 10 (1809) or later with Python 3.10+, and CI runs the whole suite on all three.
