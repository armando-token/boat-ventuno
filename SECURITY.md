# Security

`sdv-resource-governor` is a hackathon prototype. Its runtime makes no network calls (see [PROVENANCE.md](PROVENANCE.md)), but its operator console has **no authentication**.

## Deployment boundary

- By default the console binds to `127.0.0.1:8765`.
- When it is exposed on a network (for example the Arduino Ventuno Q on a demo LAN), use it **only on a trusted, private network**: a dedicated hotspot or travel router. Never expose it to the public internet without an authenticating reverse proxy.
- The jury endpoints evaluate guest cards (YAML or JSON) against the engine. YAML is parsed with `yaml.safe_load`, and every request passes strict type and unknown-key checks before it reaches the engine. No input is executed.

## Evidence integrity

Each campaign run produces an evidence bundle with hashed artifacts. `sdv-governor verify-evidence <run> --run-mutations` re-checks the bundle and runs 37 adversarial mutations; all 37 must be rejected.

## Reporting

Report issues privately to the repository owner through GitHub, not in public issues.
