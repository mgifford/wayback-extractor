# SBOM and License Tracking

This file is the project's software bill of materials (SBOM) and legal/security
tracking record. Keep it updated in the same PR as dependency changes.

## Document metadata

| Field | Value |
| --- | --- |
| Project | wayback-extractor |
| Repository license | AGPL-3.0-or-later |
| SBOM format | Markdown inventory (human-maintained) |
| Primary version source | `uv.lock` |
| Last reviewed (UTC) | 2026-08-23 |

## Component inventory

Versions below are from `uv.lock` and can vary by Python marker.

| Component | Ecosystem | Type | Direct/Transitive | Version(s) in lockfile | License (SPDX/common) | Package URL (purl) | Version source | License evidence | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| wayback-extractor | python | application | direct | 0.1.0 | AGPL-3.0-or-later | n/a | `pyproject.toml` | `LICENSE` | Project itself |
| requests | pypi | library | direct (runtime) | 2.32.4 (<3.9), 2.32.5 (3.9), 2.34.2 (>=3.10) | Apache-2.0 | `pkg:pypi/requests` | `uv.lock` | upstream metadata | HTTP client |
| beautifulsoup4 | pypi | library | direct (runtime) | 4.14.3 | MIT | `pkg:pypi/beautifulsoup4` | `uv.lock` | upstream metadata | HTML parsing |
| pytest | pypi | library | direct (dev) | 8.3.5 (<3.9), 8.4.2 (3.9), 9.0.3 (>=3.10) | MIT | `pkg:pypi/pytest` | `uv.lock` | upstream metadata | Test runner |
| flake8 | pypi | library | direct (dev) | 5.0.4 (<3.8.1), 7.1.2 (3.8.1-<3.9), 7.3.0 (>=3.9) | MIT | `pkg:pypi/flake8` | `uv.lock` | upstream metadata | Linting |
| certifi | pypi | library | transitive | 2026.5.20 | MPL-2.0 | `pkg:pypi/certifi` | `uv.lock` | upstream metadata | via requests |
| charset-normalizer | pypi | library | transitive | 3.4.7 | MIT | `pkg:pypi/charset-normalizer` | `uv.lock` | upstream metadata | via requests |
| idna | pypi | library | transitive | 3.15 (<3.9), 3.16 (>=3.9) | BSD-3-Clause | `pkg:pypi/idna` | `uv.lock` | upstream metadata | via requests |
| urllib3 | pypi | library | transitive | 2.2.3 (<3.9), 2.6.3 (3.9), 2.7.0 (>=3.10) | MIT | `pkg:pypi/urllib3` | `uv.lock` | upstream metadata | via requests |

## Version, license, and security control process

When software changes, update this file in the same PR:

1. Update dependency declarations (`pyproject.toml`) and regenerate `uv.lock`.
2. Update version rows in the inventory table for direct and relevant transitive dependencies.
3. Verify and record license identifiers for new/changed packages.
4. Check for known vulnerabilities for newly added or updated dependencies.
5. Confirm compatibility with AGPL-3.0-or-later obligations.
6. Run project validation (`python -m pytest`, `python -m flake8`) and include results in PR notes.
7. Update **Last reviewed (UTC)** in this file.

## PR review checklist for dependency changes

- [ ] Inventory rows updated for all changed dependencies
- [ ] License fields verified for changed dependencies
- [ ] Vulnerability review completed for changed dependencies
- [ ] AGPL compatibility checked
- [ ] Tests and lint run and results recorded

## Verification commands

- Show top-level dependencies: `python -m pip show requests beautifulsoup4 pytest flake8`
- Inspect lockfile entries:
  `rg "name = \\\"(requests|beautifulsoup4|pytest|flake8|certifi|charset-normalizer|idna|urllib3)\\\"" uv.lock`
