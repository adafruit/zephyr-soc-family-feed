# Zephyr new SoC family feed

This repository publishes a small, public RSS feed for **new SoC families added
to Zephyr**. It intentionally excludes driver work, refactors, policy renames,
and new chips added to an already known family.

The default implementation is deliberately narrow:

- Zephyr's `soc/**/soc.yml` metadata is the deciding evidence.
- A feed item is created only when a family name appears that was absent from
  the preceding catalog.
- Commit-title words help describe an item, but do not decide whether it is
  published.
- Incomplete history, a failed checkout, or a YAML error fails the run without
  advancing state or replacing the public feed.

The policy is documented in [POLICY.md](POLICY.md). It is normal for the team to
change that policy through reviewed pull requests as the feed becomes useful.
Repository-specific guidance for coding agents is in [AGENTS.md](AGENTS.md).

## Repository layout

```text
config.json             Human-editable scanner and output settings
src/                    Scanner and feed generator
tests/                  Regression and behavior tests
state/catalog.json      Catalog from the last complete scan
state/cursor.json       Last successfully processed upstream point
data/items.json         Complete accepted-item history
public/feed.xml         Generated RSS feed, newest first, at most 50 items
public/index.html       Human-readable project and feed landing page
.github/workflows/      Six-hour scan and GitHub Pages deployment
```

`state/` and `data/` are committed so a later run can resume and recover across
missed schedules. Generated files under `public/` are deployed as a Pages
artifact and do not need to be committed.

## How it works

1. GitHub Actions runs at minute 17 every six hours, or on manual request.
2. The workflow obtains the full first-parent history of Zephyr's `main` branch
   with a blob-filtered, sparse checkout containing `soc/`.
3. On the first run, the scanner reconstructs the catalog at the configured
   lookback boundary, then processes up to 365 days of changes chronologically.
4. Later runs resolve the saved commit cursor and process every subsequent
   first-parent commit through the new upstream head. Stable identity and
   history validation prevent duplicate entries, so a delayed or missed
   schedule is recoverable.
5. A family is accepted only when the structural rule in `POLICY.md` passes.
6. Accepted entries are appended to `data/items.json`; the newest 50 become
   `public/feed.xml`.
7. Successful changed state/history is committed, then `public/` is deployed to
   GitHub Pages in the same workflow.

## Initial GitHub setup

Create a public repository and copy this project into it. Then:

1. In **Settings > Pages**, select **GitHub Actions** as the source.
2. In **Settings > Actions > General**, allow Actions and give workflows
   permission to write repository contents if organization policy permits it.
3. Run **Update Zephyr SoC family feed** manually once from the Actions tab,
   selecting the repository's default branch.
4. Confirm the test, scan, state commit, and Pages deployment jobs complete.
5. Subscribe to `https://ORG.github.io/REPOSITORY/feed.xml`, or the equivalent
   custom-domain URL.

No external service token is required for the default git-based scan. The
workflow's `GITHUB_TOKEN` is used only to commit this repository's state and
deploy its Pages artifact.

The Adafruit organization's current branch protection, Actions permissions,
Pages policy, and custom-domain choice have not been tested by this handoff.
If automated state commits are blocked, allow the workflow's narrowly scoped
`contents: write` permission or replace that persistence step with an approved
branch or storage mechanism.

### Stable feed URL

Optionally create a repository Actions variable named `FEED_BASE_URL`, for
example `https://example.org/zephyr-soc-families`. Do not add a trailing slash.
The workflow uses it to construct the public `feed.xml` link. Individual entries
continue to link to their upstream Zephyr commits. If omitted, the workflow
derives the URL from `GITHUB_REPOSITORY`: `OWNER.github.io/REPOSITORY` for a
project repository, or the root `OWNER.github.io` URL when the repository itself
is named `OWNER.github.io`.

Scheduled workflows run from the default branch. Manual dispatches are also
allowed to commit and deploy only when the default branch is selected. A manual
run requested from another branch is skipped, preventing experimental code or
state from replacing the production feed.

A custom domain is recommended before broad distribution because it lets the
project move hosts later without asking every subscriber to change URLs.

### Configuration

`config.json` uses a small, validated schema:

| Key | Purpose |
| --- | --- |
| `repo` | Local Zephyr checkout when `--repo` is not supplied |
| `revision` | Upstream revision to resolve once at scan start |
| `catalog_file` | Complete family catalog at the saved revision |
| `cursor_file` | Last completely processed upstream commit |
| `history_file` | Full accepted-item history |
| `feed_file` | Generated public RSS document |
| `feed_title`, `feed_description` | Public channel metadata |
| `feed_link` | Absolute public URL of `feed.xml`; overridden by the workflow |
| `repository_url` | Canonical upstream URL used for commit links |
| `max_feed_items` | Maximum public entries, 50 by default |
| `backfill_days` | First-run historical window, 365 by default |

Paths are resolved relative to the selected config file. Unknown keys and
invalid values stop the scan, which prevents misspelled policy settings from
being silently ignored.

## Run locally

Requirements:

- Python 3.11 or newer
- Git 2.27 or newer with partial-clone support
- Network access to the public Zephyr repository for a fresh scan

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v

git clone --filter=blob:none --no-checkout --single-branch --branch main \
  https://github.com/zephyrproject-rtos/zephyr.git ../zephyr
git -C ../zephyr sparse-checkout init --cone
git -C ../zephyr sparse-checkout set soc
git -C ../zephyr switch main

python src/zephyr_soc_feed.py --config config.json --repo ../zephyr
```

The exact supported command-line options are available with:

```bash
python src/zephyr_soc_feed.py --help
```

For a non-default public URL in a local run:

```bash
python src/zephyr_soc_feed.py --config config.json --repo ../zephyr
```

For a local custom URL, copy `config.json`, change `feed_link`, and pass the copy
with `--config`. The `FEED_BASE_URL` override is applied by the GitHub workflow,
not read directly by the scanner.

## Expected output

A successful run leaves:

- a valid newest-first RSS document at `public/feed.xml`;
- a static project and feed page at `public/index.html`;
- no more than `max_feed_items` public entries;
- every accepted entry, including entries older than the public limit, in
  `data/items.json`;
- catalog and cursor state representing the same completed upstream scan.

Every feed item should state the new family name, any series and SoCs visible in
the introduction, the vendor area, the introducing commit and date, and a direct
upstream link. Long series and SoC lists are deterministically shortened after
12 names; complete accepted metadata remains in `data/items.json`.
Stable item identity is based on the family name and introducing commit.

## Recovery and reruns

- **A scheduled run was delayed or skipped:** run the workflow manually. It
  processes the complete first-parent gap after the saved cursor and validates
  entry uniqueness.
- **A scan failed:** fix the underlying checkout, parse, or test error and rerun.
  A failed scan must not advance the cursor or replace a known-good feed.
- **State has a bad commit:** revert the state/history commit through normal Git
  review, then run the workflow manually. Do not edit only the cursor without
  checking the matching catalog.
- **Policy changed:** update `POLICY.md`, `config.json`, implementation, and
  regression tests in one pull request. Rebuilding historical entries is a
  separate, explicit operation.
- **The upstream branch was rewritten:** manually inspect the last saved SHA,
  retain `data/items.json`, rebuild the catalog from a verified boundary, and
  rerun. Avoid fabricating a replacement cursor.

The workflow serializes runs so manual and scheduled scans do not race. Its
state commit may still conflict with a simultaneous human edit; that conflict is
intentional and reviewable rather than silently overwriting work.

## Scheduling limitations

GitHub's scheduled workflows are best-effort. They can start late or, during
high load, be dropped. In a public repository, GitHub also disables scheduled
workflows after 60 days with no repository activity. A manual dispatch,
subsequent active run, and the cursor design allow recovery, but someone
should still check the feed build time or `data/items.json`'s `generated_at`
value.

If those limits become an actual operational problem, the same scanner and
static outputs can be moved to an existing maintained server or adapted to a
durable scheduled platform. Keep the public URL stable when moving it.

## Collaboration

The repository is meant to be edited by the people using the feed. Prefer small
pull requests with an example that should pass and one that should fail. Tests
should encode policy decisions so later edits remain understandable and
reversible. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
