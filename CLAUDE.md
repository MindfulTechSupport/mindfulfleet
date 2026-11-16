# MindfulFleet: notes for Claude

## Conventions

The project's coding rules. They come from its standing rules and change only when the project decides to change them.

### Naming

- The product is **MindfulFleet**, one word, in the dashboard, README and docs.
- On the Pis the internal names stay as they are: the `mindful-fleet` command, `/opt/mindful-fleet` and `/etc/mindful-fleet`, the `mindful-fleet-hub`, `mindful-fleet-reporter` and `mindful-fleet-update` services, and the `MINDFUL_FLEET_*` settings. Renaming any of them needs a planned migration, tested on one Pi first.

### Code

- Python on the Pis uses the standard library only (Python 3.11 on Raspberry Pi OS Bookworm, 3.13 on Trixie). Nothing for the owner to install.
- The dashboard writes text with `textContent` or `el()`. Never `innerHTML`, `outerHTML` or `insertAdjacentHTML`, and never run strings as code. ESLint enforces this.
- The hub checks every field it receives (`clean_report`, `clean_layout`) and drops anything unexpected.
- The reporter accepts only `http://` or `https://` addresses for the hub and XMRig.
- Remote actions follow the guard rails: opt-in on the hub and on each Pi, a timed unlock, confirm first, back up first, one-tap undo, automatic undo if a Pi doesn't come back healthy, one Pi before the rest, everything logged.

### Errors

- Security files fail closed: if a file such as the password file can't be read or is malformed, the feature locks with a plain message instead of opening.
- Messages for owners are plain English: what happened and what to do next.

### Tests and checks (before every commit)

- `python3 -B -m unittest discover -s tests -v` passes.
- `ruff check .` (rules in `ruff.toml`), `shellcheck --severity=warning install.sh install/*.sh install/mindful-fleet` and `npx eslint hub/dashboard` pass.
- `__pycache__` and `.ruff_cache` are removed or ignored.
- Every fix or feature comes with a test. An installer change gets a test that starts an installed copy.
- The GitHub code checker (tests on Python 3.11 and 3.13, Ruff, ShellCheck, ESLint, Gitleaks, Semgrep) and the SonarQube quality gate pass. Nothing ships with a known failure.
- Anything touching passwords, keys, remote control or the network gets an independent review before it's released.

### Never

- No passwords, tokens, keys, wallet addresses, home IP addresses or login names in code, tests, commits or the README. Use documentation examples (192.0.2.x, example.com).
- No private project-tracking documents in this repo.
- Don't remove the Raspberry Pi trademark line or the "independent project, not made or endorsed by Raspberry Pi Ltd" note from the dashboard footer and README.
- Don't push to `release`. Every installed Pi updates from it, so it moves only with the owner's OK, after the checks pass on `main`.
- No force pushes, no branch deletes, no `rm -rf` (blocked in `.claude/settings.json`).

### Commits

- Author: "Mindful Tech Projects" <noreply@users.noreply.github.com>.
