# Publication policy

## Purpose

The feed reports genuinely new Zephyr SoC **families**. It is not a general
Zephyr SoC, board, binding, or driver activity feed.

The default policy is intentionally strict. False negatives can be discussed
and recovered later; a broad feed that repeatedly reports refactors as new
families is harder for subscribers to trust.

## Deciding evidence

Zephyr's `soc/**/soc.yml` family metadata is the source of truth used by this
project. A commit title is supporting context only.

The catalog is global, not file-local. Repeated entries with the same normalized
family name are valid Zephyr metadata and their series and SoCs are merged as a
union. A parser must not reject the file or silently let the last entry overwrite
earlier membership. Top-level series-only and SoC-only metadata remains part of
the parsed hardware inventory, but does not itself create a family event.

An item qualifies when all of the following are true:

1. Processing Zephyr `main` chronologically causes a non-empty family name to
   appear in parsed `soc.yml` metadata.
2. That family name was absent from the complete catalog immediately before the
   introducing commit.
3. The family still exists at the completed scan's upstream head. This avoids
   announcing a transient addition removed later in the same scan window.
4. The event is not classified as a rename or move of an existing family.
5. The introducing commit and family identity have not already been published.

For each accepted family, record the introducing commit SHA, authored or
committed date used by the implementation, source `soc.yml` path, vendor area,
series, and SoCs that can be established from that commit.

## Included by default

- A new family entry and its first series or SoCs.
- Multiple newly introduced families in one commit, as separate stable items.
- A new family whose commit title does not say `add`, if the metadata proves the
  introduction.

Example that should pass:

```text
soc: silabs: Add Six301 SoC and silabs_s3 family
```

The structural reason is the new `silabs_s3` family metadata, not the word
`Add`.

## Excluded by default

- New SoCs or series added under an already known family.
- Driver, binding, board, documentation, CI, or sample changes.
- Identifier, Kconfig, directory, or policy renames.
- File moves and metadata reshaping with no new family identity.
- Fixes, cleanups, refactors, and enablement work for existing families.
- Changes inferred only from a commit title.

Example that should fail:

```text
soc: silabs: Rename SOC_SILABS_XG* to match policy
```

It does not add a new family to `soc.yml`.

## Rename guard

An apparent addition should not publish automatically when the same change also
removes a family with substantially the same series or SoC membership, or moves
that membership to a different file. Treat that as a likely rename and reject
or quarantine it for review.

Case-only family-name changes are also rename-like and are not new families.

The scanner should prefer an explicit non-public review record over guessing.
If the available diff or history is incomplete, the entire scan fails closed.

## Known policy edges

- A total rename that changes the family name, every series name, and every SoC
  name has no membership overlap for the structural guard to recognize. It may
  appear to be new. The stricter alternative is to quarantine every same-commit
  add/remove pair in one vendor area, but that can hide a genuine replacement.
- The rule follows Zephyr metadata, not a judgment about physical availability.
  Virtual or synthetic platforms represented as families, such as a QEMU SoC
  family, therefore qualify unless the project adds an explicit exclusion.
- New series and individual SoCs inside an existing family remain intentionally
  absent from this feed.

These are editorial choices for normal issue and pull-request discussion, not
reasons to silently broaden or narrow the classifier.

## First run and historical window

The first successful run looks back 365 days by default. It reconstructs the
family catalog at the boundary and then applies relevant commits in chronological
order. Families that already existed at the boundary are baseline state, not
new feed items.

This is a bounded backfill, not a claim to be a complete history of Zephyr. The
lookback is editable in `config.json`. Changing it after state exists does not
implicitly rebuild history.

## Ordering, retention, and identity

- The public RSS feed is newest first.
- It contains at most 50 items by default.
- `data/items.json` retains the complete accepted history.
- A stable item key contains the normalized family name and full introducing
  commit SHA.
- Reprocessing or replaying history must not duplicate an item.
- Dates in generated output use UTC and RFC-compatible forms where applicable.

## Failure policy

Do not advance state or publish replacement output when any required evidence
is incomplete, including:

- missing or shallow history needed for the scan window;
- a failed upstream fetch or checkout;
- malformed or unreadable relevant YAML;
- an unhandled history topology or missing commit;
- inconsistent catalog, cursor, or item state;
- output validation failure.

Keep the last known-good public feed and state. A later manual or scheduled run
can recover from the saved cursor.

## Changing the policy

Policy changes should be made through a reviewed pull request that updates:

1. this document;
2. `config.json` if the behavior is configurable;
3. scanner logic when needed;
4. fixtures and regression tests for both inclusion and exclusion.

Possible future feed modes such as new series or new individual SoCs should be
separate, explicit choices. They should not silently broaden the strict family
feed.
