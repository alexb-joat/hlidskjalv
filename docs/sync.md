# Sync between your computers

Optional. Settings, lifetime XP and finished Learn levels, encrypted, with no account server.

[← Back to the README](../README.md)

---

Settings → Sync keeps your settings, lifetime XP and finished Learn levels the same on every computer you use, without an account server. The copy lives in a place you own:

- **a folder** that Dropbox, Google Drive, OneDrive, Nextcloud or Syncthing already keep in sync, or
- **a private GitHub Gist**, with a fine-grained personal access token that has *Gists: read and write* (kept in `~/.hlidskjalv/sync.json`, readable only by you, never synced).

Before anything leaves the computer it is encrypted with a passphrase you choose (ChaCha20-Poly1305 as in RFC 8439, key from scrypt), so the carrier only ever sees ciphertext; the same passphrase joins the other computers. XP adds up across computers, finished Learn levels unite, and settings follow the newest copy, with an alert telling you so. Machine-specific settings (the Trioditis log path, root in the terminal, the first-start flag) stay local. Pairing, passwords, second-factor secrets, sessions, alerts and activity never travel. Sync pushes shortly after a change and pulls every five minutes; `python3 -m hlidskjalv sync status | now` does the same from the terminal. Nobody can recover a forgotten passphrase: the copy is then unreadable and you start afresh.
