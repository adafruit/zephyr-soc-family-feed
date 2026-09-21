# Repository instructions

This repository generates a public RSS feed for genuinely new Zephyr SoC
families. Keep the default publication policy narrow and reviewable.

## Before changing code or policy

1. Read `README.md` and `POLICY.md`.
2. Treat parsed `soc.yml` history as deciding evidence. Commit-title wording is
   context, not a classifier.
3. Preserve fail-closed behavior. Incomplete Git history, unreadable YAML, or
   inconsistent state must not advance the cursor or replace durable output.
4. Keep new series and new SoCs inside an existing family out of the default
   feed unless a reviewed policy change explicitly adds a separate mode.

## Required checks

Run before proposing a change:

```bash
python -m unittest discover -s tests -v
python -m py_compile src/zephyr_soc_feed.py tests/test_zephyr_soc_feed.py
```

For classifier changes, add at least one passing and one rejected regression.
When practical, run a bounded replay against a complete, non-shallow Zephyr
checkout and inspect every newly accepted family.

## State and generated files

- `state/catalog.json`, `state/cursor.json`, and `data/items.json` are durable
  and must remain mutually consistent.
- `public/feed.xml` is generated and intentionally untracked.
- Do not hand-edit only the cursor or force-push automated state updates.
- Do not commit credentials, private URLs, local paths, downloaded upstream
  repositories, caches, or virtual environments.

Use small pull requests. Keep policy, implementation, fixtures, and tests in
the same change so editorial decisions remain understandable and reversible.
