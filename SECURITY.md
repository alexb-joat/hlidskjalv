# Security policy

## Reporting a vulnerability

Please report it **privately**: open the repository's **Security** tab and choose **Report a vulnerability**
([direct link](https://github.com/alexb-joat/hlidskjalv/security/advisories/new)). Please do not open a public issue.

Include the version (`python3 -m hlidskjalv --version`), your system and browser, what you expected and what happened,
and the smallest steps that reproduce it. Never include your owner key, pairing links or anything from `~/.hlidskjalv`.

This is a small project with one maintainer. Expect an acknowledgement within a week; confirmed issues get a fix or a
documented mitigation as soon as practical, and reporters are credited unless they prefer not to be.

## Supported versions

Fixes land on `main` and ship in the next release. Please reproduce on the latest release or `main` first.

## In scope

Anything that lets someone who is not the owner read, change or run something through Hlidskjalv: bypassing the
`Host` or `Origin` checks, pairing, the session or second-factor checks; making the page run injected script; reaching
the terminal without a paired browser or getting root without the opt-in; file contents, secrets or session data leaving
the computer; a path or request that escapes the folders it is meant to read; or a weakness in the install, vendored
files or CI supply chain.

## Out of scope

Someone who already runs code as your user (they can read `~/.hlidskjalv` as you can); alert rules and secret masks that
miss a pattern (they are a safety net; new patterns are welcome as ordinary pull requests); weaknesses in GitHub,
Google or Gist themselves; using the opt-in terminal to do what any shell can do.

## Using it safely

- Pair with the one-time link or a password, and add two-factor if others use the computer.
- Leave "Root in the terminal" off unless you need it, and the terminal off unless you use it.
- Keep `~/.hlidskjalv` private (the program sets `0700`). If a pairing link or owner key was ever pasted somewhere,
  run `python3 -m hlidskjalv pair --rotate`.

The design, its controls and its limits are described in [docs/security-model.md](docs/security-model.md).
