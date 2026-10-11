# Vendored browser files (terminal tab only)

Copied unmodified from npm, checked against the registry's sha512 integrity:

| File | Package | License |
|---|---|---|
| xterm.js, xterm.css | [@xterm/xterm](https://www.npmjs.com/package/@xterm/xterm) 5.5.0 | MIT, (c) The xterm.js authors |
| addon-fit.js | [@xterm/addon-fit](https://www.npmjs.com/package/@xterm/addon-fit) 0.10.0 | MIT, (c) The xterm.js authors |

```
@xterm/xterm-5.5.0.tgz     sha512-hqJHYaQb5OptNunnyAnkHyM8aCjZ1MEIDTQu1iIbbTD/xops91NB5yq1ZK/dC2JDbVWtF23zUtl9JE2NqwT87A==
@xterm/addon-fit-0.10.0.tgz sha512-UFYkDm4HUahf2lnEyHvio51TNGiLK66mqP2JoATy7hRZeXaGMRDr00JiSF7m63vR5WKATF605yEggJKsw0JpMQ==
```

The Python side stays standard-library only; these files are only served to
the browser, from this folder, when the terminal is switched on.
