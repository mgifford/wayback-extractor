# SBOM and License Tracking

This file documents the software bill of materials (SBOM) for this repository and provides a lightweight process for tracking version and license risk.

## Project

| Component | Version | License | Source of truth |
| --- | --- | --- | --- |
| wayback-extractor | 0.1.0 | AGPL-3.0-or-later | `pyproject.toml`, `LICENSE` |

## Runtime dependencies

Versions below are from `uv.lock` and may vary by Python marker.

| Package | Locked versions in `uv.lock` | License (SPDX/common) | Notes |
| --- | --- | --- | --- |
| requests | 2.32.4 (<3.9), 2.32.5 (3.9), 2.34.2 (>=3.10) | Apache-2.0 | HTTP client |
| beautifulsoup4 | 4.14.3 | MIT | HTML parsing |
| lxml | 6.1.1 | BSD-3-Clause | XML/HTML parser backend |

## Development dependencies

| Package | Locked versions in `uv.lock` | License (SPDX/common) | Notes |
| --- | --- | --- | --- |
| pytest | 8.3.5 (<3.9), 8.4.2 (3.9), 9.0.3 (>=3.10) | MIT | Test runner |
| flake8 | 5.0.4 (<3.8.1), 7.1.2 (3.8.1-<3.9), 7.3.0 (>=3.9) | MIT | Linting |

## Key transitive dependencies (security-relevant)

These are pulled in through `requests` and should be reviewed during dependency updates:

| Package | Locked versions in `uv.lock` | License (SPDX/common) |
| --- | --- | --- |
| certifi | 2026.5.20 | MPL-2.0 |
| charset-normalizer | 3.4.7 | MIT |
| idna | 3.15 (<3.9), 3.16 (>=3.9) | BSD-3-Clause |
| urllib3 | 2.2.3 (<3.9), 2.6.3 (3.9), 2.7.0 (>=3.10) | MIT |

## Version and license control process

When software changes, update this file in the same PR:

1. Update dependency declarations (`pyproject.toml`) and regenerate `uv.lock`.
2. Record any new package/version/license changes in this document.
3. Confirm compatibility with AGPL-3.0-or-later obligations.
4. Run tests/linting and include results in the PR notes.

## Verification commands

- Show top-level dependencies: `python -m pip show requests beautifulsoup4 lxml pytest flake8`
- Inspect lockfile: `rg "name = \\\"(requests|beautifulsoup4|lxml|pytest|flake8|certifi|charset-normalizer|idna|urllib3)\\\"" uv.lock`
