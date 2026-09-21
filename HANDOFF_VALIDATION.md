# Handoff validation

Release: `0.1.0-handoff.1`

Prepared: 2026-09-18

Supported target: a public GitHub repository using GitHub Actions, Python 3.12,
and GitHub Pages. Local development is documented for Python 3.11 or newer and
a complete, non-shallow Git checkout.

## Included

- scanner source and pinned Python runtime dependency;
- strict publication policy and collaboration guidance;
- synthetic regression tests and recorded test results;
- empty durable state and item-history files for a fresh first run;
- scheduled/manual GitHub workflow and static Pages landing page;
- license, third-party notices, release notes, blank run-results template;
- a complete SHA-256 manifest of the outgoing files.

## Deliberately excluded

- the private workspace and conversation that produced this handoff;
- Git history, caches, virtual environments, downloaded Zephyr trees, logs,
  browser/account state, credentials, webhook URLs, and old service setup;
- generated live state and the 26-item validation feed. The target repository's
  first successful run recreates current state and output from public history.

## Verified behavior

- [x] Default policy publishes only newly introduced SoC families.
- [x] Driver work, policy renames, existing-family additions, membership
  renames, and metadata wrappers are excluded by regression tests.
- [x] First-run lookback is 365 days.
- [x] RSS is valid XML, newest first, and limited to 50 entries.
- [x] Complete accepted history is retained separately from the public cap.
- [x] Invalid YAML, invalid XML text, shallow history, inconsistent state, and
  rewritten history fail closed.
- [x] A no-op run is byte-stable and a fresh checkout regenerates the untracked
  feed without changing durable state.
- [x] A live annual replay processed 185 relevant commits and accepted 26
  families. It included `silabs_s3` and excluded the `litex` and `amd_versal`
  metadata-wrapper changes.
- [x] The automated suite passes 10 tests; `py_compile` and Ruff also pass.

## Verified workflow properties

- [x] Schedule is `17 */6 * * *`; manual dispatch is available.
- [x] Non-default-branch dispatch cannot commit or deploy.
- [x] Runs are serialized.
- [x] The upstream checkout is full-history, blob-filtered, and sparse to
  `soc/`.
- [x] Tests run before scanning; scanning runs before state commit or deploy.
- [x] Build and deploy jobs use separated, limited permissions.
- [x] Third-party actions are pinned to full commit SHAs with major-version
  comments.
- [x] Pages deployment happens in the same workflow and supports a configurable
  `FEED_BASE_URL`.
- [x] Derived URLs handle both project repositories and an `OWNER.github.io`
  site repository.

## Verified package properties

- [x] The ZIP was built from an explicit allowlist under one top-level folder.
- [x] Its manifest verifies after clean extraction.
- [x] Archive paths contain no absolute paths, traversal, symlinks, nested
  archives, VCS data, caches, `__MACOSX`, or `.DS_Store` entries.
- [x] Text and metadata scans found no credentials, private keys, private or
  personal identities, local absolute paths, private hosts, or legacy-service
  configuration. Public upstream URLs and the intentional GitHub Actions bot
  noreply identity remain.
- [x] The documented dependency installation, 10-test suite, compilation check,
  and CLI help run succeeded from a newly extracted copy.

## Remaining validation boundary

This handoff did not create the destination repository or exercise the target
organization's Actions policy, Pages policy, branch protection, custom domain,
or permission for the workflow to push state. A maintainer must verify those on
the first repository run. GitHub's scheduled-workflow delay and 60-day public
repository inactivity behavior are documented in `README.md`.

The strict classifier has two known editorial edges documented in `POLICY.md`:
a total rename with no surviving family, series, or SoC identifiers may appear
new, and virtual platforms represented by Zephyr as SoC families qualify.
