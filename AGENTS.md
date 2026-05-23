# Agents

This repository does not run or ship autonomous agents. If you are using AI tooling to work on it, keep the following in mind:

- Scope: The code is a single Python script (`wayback_extractor.py`) that mirrors Wayback Machine snapshots; there is no long-running control loop or external action stack.
- Safety: Avoid automating high-rate crawl settings; respect the Internet Archive by keeping `--rps` conservative.
- Reproducibility: Keep any AI-assisted changes small, documented, and testable; prefer explaining why defaults change (e.g., cutoff dates, snapshot preference).
- Data handling: Snapshots may include personal data from archived sites; avoid adding processing that extracts or republishes sensitive information.
- Logging: Keep logs local unless explicitly redacting URLs and outputs.
- Security: Treat all downloaded content as untrusted input; avoid dynamic code execution, sanitize output paths, and keep dependency versions and licenses tracked in `SBOM.md`.

Even though Wayback content is public, continue to follow security best practices:

- Keep `--rps` conservative to avoid abusive crawl behavior.
- Minimize retained data and avoid republishing personal or sensitive information from snapshots.
- Review dependency updates for both known vulnerabilities and license changes before merging.
- Keep the software inventory current in `SBOM.md` whenever dependencies or tooling changes.

If you plan to add actual agent behaviors (workflow orchestration, background crawlers, or multi-agent coordination), document:
1. The agent's goals and stop conditions.
2. Allowed external actions (HTTP domains, file writes) and rate limits.
3. How human oversight works (review steps, approval gates).
4. How to reproduce and disable the agent cleanly.

## Python Coding Standards

When writing or reviewing Python code in this repository, follow the guidelines in [PYTHON_GUIDANCE.md](PYTHON_GUIDANCE.md). Key points:

- Add docstrings to every function, class, and module.
- Use type annotations on all function signatures.
- Keep functions ≤ 50 lines; split larger ones into focused helpers.
- Run `flake8` (or `ruff check`) before committing and fix all warnings.
- Never leave trailing whitespace on blank lines.
