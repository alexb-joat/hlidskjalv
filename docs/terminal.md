# Terminal in the page

Optional. A real shell in the project folder, off by default and locked down when on.

[← Back to the README](../README.md)

---

```bash
python3 -m hlidskjalv watch ~/projects/my-app --terminal
```

(or switch it on in Settings). The **Terminal** tab then runs your normal shell in the project folder, as you. Commands typed there land in the activity log like any other (with the shell hook installed). A shell in a web page can do anything you can, so it's off by default and locked down when on:

- Only Hlidskjalv's own page can connect. Other websites are refused by an Origin check, and other users or programs on the computer by the session token (see [Pairing](pairing.md)).
- At most four shells at a time; each one is closed when its tab disconnects.
- **Root (sudo) is off by default, Linux only.** The shell gets the kernel's no-new-privileges flag, so sudo and other setuid programs can't elevate, however Hlidskjalv was started. Settings → Features → "Root in the terminal" switches it on: it needs a pairing password, is refused while sudo works without a password on this computer, asks for a fresh two-factor code when one is set up, and raises an alert. The login service keeps its sandbox either way: when Hlidskjalv runs sandboxed, the shell is started through your user service manager (`systemd-run --user --pty`) instead of as a child of the service, which is the path the sandbox deliberately leaves open. sudo then asks for your password as usual, and each sudo use shows up in Alerts via the system log. On macOS the shell can always use sudo (the launch agent has no such flag); on Windows the option doesn't exist.

The terminal display is [xterm.js](https://xtermjs.org/) (MIT), shipped unmodified in `hlidskjalv/static/vendor/` with its npm checksums. Linux and macOS use a pty. Windows 10 (1809) and later use ConPTY with PowerShell; that part is **experimental**: it's tested on Windows CI, not yet on a Windows desktop.
