# Contributing

This is a small public project with a narrow editorial contract. Code and policy
changes are welcome when they make that contract clearer, more accurate, or
more maintainable.

## Before opening a change

1. Read `POLICY.md`.
2. Keep new-family detection based on parsed Zephyr metadata, not title scoring.
3. Add or update tests for the behavior being changed.
4. Avoid committing credentials, private URLs, local absolute paths, downloaded
   repositories, virtual environments, or generated caches.

## Development check

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python src/zephyr_soc_feed.py --help
```

For an end-to-end run, follow the sparse-checkout instructions in `README.md`.
Inspect all changes under `state/`, `data/`, and `public/` before committing.

## Pull requests

A useful pull request explains:

- what upstream event should now be included or excluded;
- the metadata evidence for that decision;
- whether historical items need rebuilding;
- how the change was tested.

For a classifier change, include at least one positive and one negative fixture.
Real upstream commit links are useful when licensing and fixture size permit it;
small, attributed excerpts are preferable to copying a large upstream tree.

Do not weaken fail-closed behavior to make a broken scan appear successful.

## Policy discussions

Open an issue or draft pull request when the desired editorial result is not
settled. A policy decision becomes durable when its documentation and tests land
together. New series and new individual SoCs should remain separate from the
default new-family feed unless the team explicitly chooses otherwise.

## State and generated output

- `state/catalog.json` and `state/cursor.json` must describe the same completed
  upstream scan.
- `data/items.json` is append-oriented accepted history. Correcting it should be
  explicit and reviewed.
- `public/` is generated for deployment and is not the durable source of scan
  history.

If a state commit conflicts with a human edit, stop and resolve it through Git.
Do not force-push or silently choose one version.
