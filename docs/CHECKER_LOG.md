# Checker log: every finding, and what we did about it

The code checker (`.github/workflows/checks.yml`, on GitHub under Actions → "Code checker") runs on every push,
every pull request and every Monday. It was set up on 2026-11-10, modeled on CartPacker's checker.

| Checker | What it looks for | Rules |
|---|---|---|
| Tests | Every test, on the Python of Raspberry Pi OS 12 (3.11) and 13 (3.13) | `tests/` |
| Ruff | Python mistakes, likely bugs, security patterns (Bandit's rules) | `ruff.toml` |
| ShellCheck | Mistakes in the install and update scripts | warnings and errors |
| ESLint | Dashboard mistakes; blocks `innerHTML` and running strings as code | `eslint.config.mjs` |
| Gitleaks | Passwords and keys anywhere in the history | defaults |
| Semgrep | Attack patterns in Python and JavaScript | `p/python`, `p/javascript` |

Dependabot checks the checker's own actions for updates every Monday.

## How a finding is handled

1. Read every finding on the run's page (each job lists them; Gitleaks and Semgrep also keep their reports
   for 90 days under the run's Artifacts).
2. Give each finding a row below, in the checker's own words.
3. What we did is one of: **Fixed** (the commit, and the test that guards it if any), **Not a problem**
   (why, and the same reason written next to the line as `# noqa:` or in the rule file), or **Accepted for
   now** (only with Ed's OK, and when we'll look again).
4. `release` only moves to a version whose checks all passed on `main`, and only with Ed's OK.

## Findings

| # | Date | Checker | Rule | Where | What it said | What we did |
|---|---|---|---|---|---|---|
| 1 | 2026-11-10 | Ruff | S310 | reporter: `read_xmrig`, `send` | "Audit URL open for permitted schemes. Allowing use of `file:` or custom schemes is often unexpected." | **Fixed.** The hub and XMRig addresses come from settings; the reporter now accepts only `http://` and `https://` (`is_web_address`), refuses to start with any other hub address, and skips XMRig otherwise. Test: `test_only_web_addresses`. |
| 2 | 2026-11-10 | Ruff | S310 | hub: `check_latest_version` | Same as #1 | **Not a problem.** The address is fixed in the code (GitHub's `release` VERSION file over https). Noted on the line. |
| 3 | 2026-11-10 | Ruff | S311 (16) | hub: demo mode | "Standard pseudo-random generators are not suitable for cryptographic purposes" | **Not a problem.** `random` only makes up readings for `--demo`. Fleet tokens come from the installer's `openssl rand`. Ignored for the hub file in `ruff.toml`, with that reason. |
| 4 | 2026-11-10 | Ruff | S104 | hub: `--host` default `0.0.0.0` | "Possible binding to all interfaces" | **Not a problem, by design.** The Pis on the home network report to the hub, so it has to listen on the network. The dashboard password (0.3.0) is the fix for "anyone on the network can open it". |
| 5 | 2026-11-10 | Ruff | S603, S607 (3) | `install/xmrig-api.py` | "Starting a process with a partial executable path" | **Not a problem.** Fixed `systemctl` commands with no outside input, run by the owner with sudo. Noted on each line. |
| 6 | 2026-11-10 | Ruff | B023 | reporter: `read_power_sensors` | "Function definition does not bind loop variable `folder`" | **Fixed** (not a live bug: the helper ran inside the same loop pass). The folder is now passed in explicitly. |
| 7 | 2026-11-10 | Ruff | E731 (2), E741, E501 (2) | reporter, hub | Lambda assigned to a name; variable named `l`; lines too long | **Fixed.** Rewritten as small functions and shorter lines. |
| 8 | 2026-11-10 | ESLint | no-unused-vars (4) | dashboard `app.js` | "'error' is defined but never used" | **Fixed.** `catch {}` without the unused name. |
