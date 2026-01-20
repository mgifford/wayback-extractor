# Agents

This repository does not run or ship autonomous agents. If you are using AI tooling to work on it, keep the following in mind:

- Scope: The code is a single Python script (`wayback_extractor.py`) that mirrors Wayback Machine snapshots; there is no long-running control loop or external action stack.
- Safety: Avoid automating high-rate crawl settings; respect the Internet Archive by keeping `--rps` conservative.
- Reproducibility: Keep any AI-assisted changes small, documented, and testable; prefer explaining why defaults change (e.g., cutoff dates, snapshot preference).
- Data handling: Snapshots may include personal data from archived sites; avoid adding processing that extracts or republishes sensitive information.
- Logging: Keep logs local unless explicitly redacting URLs and outputs.

If you plan to add actual agent behaviors (workflow orchestration, background crawlers, or multi-agent coordination), document:
1. The agent's goals and stop conditions.
2. Allowed external actions (HTTP domains, file writes) and rate limits.
3. How human oversight works (review steps, approval gates).
4. How to reproduce and disable the agent cleanly.
