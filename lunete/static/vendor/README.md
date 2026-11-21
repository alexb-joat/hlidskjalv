# Vendored browser files (terminal tab and two-factor setup only)

Copied unmodified from npm, checked against the registry's sha512 integrity:

| File | Package | License |
|---|---|---|
| xterm.js, xterm.css | [@xterm/xterm](https://www.npmjs.com/package/@xterm/xterm) 5.5.0 | MIT, (c) The xterm.js authors |
| addon-fit.js | [@xterm/addon-fit](https://www.npmjs.com/package/@xterm/addon-fit) 0.10.0 | MIT, (c) The xterm.js authors |
| qrcode.js | [qrcode-generator](https://www.npmjs.com/package/qrcode-generator) 2.0.4 (`dist/qrcode.js`) | MIT, (c) Kazuhiko Arase (notice in the file header) |

```
@xterm/xterm-5.5.0.tgz     sha512-hqJHYaQb5OptNunnyAnkHyM8aCjZ1MEIDTQu1iIbbTD/xops91NB5yq1ZK/dC2JDbVWtF23zUtl9JE2NqwT87A==
@xterm/addon-fit-0.10.0.tgz sha512-UFYkDm4HUahf2lnEyHvio51TNGiLK66mqP2JoATy7hRZeXaGMRDr00JiSF7m63vR5WKATF605yEggJKsw0JpMQ==
qrcode-generator-2.0.4.tgz  sha512-mZSiP6RnbHl4xL2Ap5HfkjLnmxfKcPWpWe/c+5XxCuetEenqmNFf1FH/ftXPCtFG5/TDobjsjz6sSNL0Sr8Z9g==
```

The Python side stays standard-library only; these files are only served to
the browser, from this folder: the xterm files when the terminal is switched on, qrcode.js when two-factor authentication is being set up.
