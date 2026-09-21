# Test results

This file records checks performed on the prepared handoff.

## Local automated run

The latest synthetic test run completed with PyYAML 6.0.2:

```text
python -m unittest discover -s tests -v
Ran 10 tests
OK
```

`ruff` also passed. The suite includes a fail-closed regression for XML 1.0
control characters. A separate live 365-day Zephyr replay processed 185 relevant
commits and produced 26 feed items from 101 current families. It included
`silabs_s3` and excluded the `litex` and `amd_versal` metadata-wrapper changes.
An immediate no-op rerun left the durable state, catalog, history, and feed
byte-identical. A fresh-checkout simulation with the generated `feed.xml` absent
reconstructed it deterministically without changing durable files.

The target GitHub organization's settings have not been exercised.

## Required release checks

- [x] Unit and regression tests pass with
  `python -m unittest discover -s tests -v`.
- [x] The Six301 / `silabs_s3` introduction fixture is included.
- [x] The `SOC_SILABS_XG*` policy rename fixture is excluded.
- [x] A new SoC under an existing family is excluded in strict mode.
- [x] Wrapping existing top-level SoC metadata in a family is excluded,
  including mixed existing/new SoC wrapper cases.
- [x] A probable family rename is excluded.
- [x] Repeated entries for one family merge series and SoCs instead of
  overwriting earlier entries.
- [x] Malformed relevant YAML fails closed without changing durable state.
- [x] A direct depth-1 checkout validation failed closed with exit status 2 and
  created no state, history, or feed output.
- [x] A no-op scan is byte-stable and creates no duplicate items.
- [x] RSS output is valid XML, newest first, and respects the configured cap.
- [x] Full accepted history remains in `data/items.json` when the feed is
  capped.
- [x] Generated HTML and the live-validation RSS contain no private paths or
  credentials.
- [x] The scrubbed archive contains no Git metadata, caches, or secrets.

## Current result

The ten automated scanner tests, live annual replay, privacy scan, and clean
archive extraction pass. The target GitHub organization remains the deployment
boundary. See `HANDOFF_VALIDATION.md` for the final handoff record.
