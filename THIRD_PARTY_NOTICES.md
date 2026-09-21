# Third-party notices

This project installs [PyYAML](https://pyyaml.org/) at runtime. PyYAML is
distributed under the MIT License. The dependency version used by this handoff
is pinned in `requirements.txt`.

The scanner reads public metadata and Git history from the
[Zephyr Project](https://github.com/zephyrproject-rtos/zephyr). No Zephyr source
tree or binary is included in this handoff archive. Zephyr's own licensing and
copyright notices continue to apply to material obtained from that repository.

The GitHub Actions referenced by `.github/workflows/update-feed.yml` are fetched
by GitHub when the workflow runs; their source and licenses remain in their
respective public repositories.
