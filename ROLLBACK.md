# Generated-site publication and recovery

The Windows update workflow runs offline code regressions before collection.
After collection it validates the current generated schema, full/slim IDs,
sidecars, timestamps and exact supporting quotations, then runs data-quality
tests. Only passing output reaches the existing commit/push step. Hosted PR
Code checks require desktop/mobile Chromium; the publisher does not install or
pretend to run unavailable device browsers.

The push retry verifies that concurrent main changes touch only generated data
or synced keyword files. A changed source/asset revision stops publication; the
next normal build must test that revision. It never overwrites concurrent code
or silently combines untested new assets with old build output.

A failed gate leaves the prior GitHub Pages data in place and triggers the
existing failure/guardian mechanism. Do not bypass a failed test to release.
The prior **tracked public docs** version is archived before collection, with
its Git SHA, per-file SHA256 checksums and validation disposition. GitHub
`rss-news-recovery-<run-id>` artifacts retain that ZIP for three days; Git history
retains the corresponding immutable version after artifact expiry. The archive
contains no .env, credentials, Obsidian vault or browser-local reading state.
An existing invalid legacy version is marked unvalidated; do not call it good
or automatically restore it.

## Review a recovery version

Download the recovery artifact from the relevant completed/failed Actions run.
Check the recorded SHA against the run and select a known validated version.
These commands only inspect files and restore into a new empty review folder:

```sh
python tools/site_snapshot.py verify /path/to/rss-news-prior.zip
python tools/site_snapshot.py restore /path/to/rss-news-prior.zip --out /tmp/rss-recovery-review
python tools/validate_publication.py /tmp/rss-recovery-review/docs
```

`restore` rejects changed checksums, traversal/symlink entries, unvalidated
snapshots and nonempty destinations. It does not write into the checkout,
change a branch, run a build, push, notify or deploy.

## Prepare an approved rollback

Create a dedicated repair branch from current main. With the recorded SHA
available in local Git history, restore `docs` from that exact version:

```sh
git switch -c repair/restore-reviewed-site
git restore --source=<reviewed-recovery-sha> -- docs
python tools/validate_publication.py docs
python -m pytest tests --ignore=tests/test_data_quality.py -q
python -m pytest tests/test_data_quality.py -q
```

Review the diff, run the owning CI checks, and open a draft PR explaining the
recovery SHA and failure. If using artifact bytes because history is unavailable,
compare the empty-folder restore against the checkout, including deleted files,
and prepare the same reviewed PR. Do not overwrite a dirty checkout.

Merging the rollback/deploying Pages requires explicit approval. Never force
push main, silently change Pages settings, rerun a paid/notification build or
restore credentials. A frontend rollback can need a new SW CACHE version so
installed readers fetch the reviewed assets; preserve the cache gate.

GitHub Pages may publish approved source commits independently of generated-data
updates. These gates cover generated publication; PR checks and human approval
still govern source releases. An Actions success is not a claim that physical
iPhone Cantonese speech or every publisher's network is available.
