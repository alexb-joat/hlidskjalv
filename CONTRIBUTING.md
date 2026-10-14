# Contributing

Thanks for helping. Hlidskjalv watches a person's computer and can open a shell, so the bar for changes is
"small, tested, and no new attack surface without a reason".

## Workflow

1. **Fork** the repo and create a branch: `git switch -c fix/short-description`.
2. **Set up:** nothing to install. The Python side is the standard library; you need Python 3.10 or newer.
3. **Make the change** and add tests in `tests/`. The page is `hlidskjalv/static/index.html` (markup and styles) and
   `hlidskjalv/static/app.js` (script).
4. **Check** before pushing:
   ```bash
   python3 -m unittest discover -s tests -t .
   ```
   If the page changed, run it too: start a throwaway instance with `HLIDSKJALV_HOME=$(mktemp -d)
   python3 -m hlidskjalv watch <some folder> --no-open --port 8799` and try it in Firefox and one other engine.
5. **Open a pull request** and fill in the template. CI, CodeQL and dependency review run automatically; for
   first-time contributors a maintainer approves the run first.

## Ground rules

- **No runtime dependencies.** A new package needs a strong reason and a discussion first. Vendored browser files go
  in `static/vendor/` with their source, version and license in its README, and their checksum in `SHA256SUMS`
  (the unit test fails until both match).
- **Network.** No outbound calls (telemetry, update checks, remote config) and nothing loaded from another site
  without a discussion first. A default boot must stay silent; a test checks it.
- **Page script.** No inline `<script>` and no inline event-handler attributes: the CSP is `script-src 'self'`.
- **Reading files.** Only names and line counts leave `sources.py`. Never read, log or store file contents or
  prompt text.
- **Secrets.** Never commit keys, owner keys, pairing links or real session files, not even in tests. If one slips
  into a pushed commit, rotate it: forks keep history even after you delete it.
- **Security issues** go to the Security tab ("Report a vulnerability"), not public issues.

## Style

Small functions, plain names, comments that say why. Match the code around you.
