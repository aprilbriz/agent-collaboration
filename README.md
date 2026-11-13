# agent-collaboration

**English** | [한국어](README.ko.md)

A collaboration plugin that runs one of Codex and Claude Code as the **host** (planning and verification) and the other as the **worker** (working in a dedicated terminal). Worker terminals run in Orca or tmux.

```text
Agree on a plan with the user → request → dedicated worker
→ reply + completion notice → independent host verification → (fix round trips) → after PASS, the designated owner commits
```

> The procedure documents the agents read (`RULES.md`, skills, templates) are written in Korean. This README is a translation of [README.ko.md](README.ko.md); when the two differ, the Korean version is authoritative.

## Install

Install the plugin only in the CLI you use as the host. The worker's CLI needs nothing installed. There are no other commands to run, no PATH setup, and no per-project preparation.

```bash
# Claude Code as the host
claude plugin marketplace add aprilbriz/agent-collaboration
claude plugin install agent-collaboration@agent-collaboration

# Codex as the host
codex plugin marketplace add aprilbriz/agent-collaboration
codex plugin add agent-collaboration@agent-collaboration
```

After installing, start the CLI again and these skills are available:

| Skill | Role |
|---|---|
| `plan-with-me` | Agree on a plan with the user through selection-UI questions |
| `collaborate` | Host entry point: write requests, run the worker, wait for completion, verify, close |

## Use

In the host session, describe the work and ask for collaboration, for example "use collaborate to fix this bug with a Codex worker (tmux)". The host follows the skill from there.

- **Plan:** agree on scope, completion criteria, and the commit owner with `plan-with-me`.
- **Thread:** the host creates `<workspace>/.comm/<date>-<topic>/`. The folder is added only to that git repository's `.git/info/exclude`; tracked files are not changed.
- **Worker:** the host starts the other CLI in an Orca tab or a `collab-` tmux session and sends one request line. On tmux, you can watch the worker with the `tmux attach` command the host gives you.
- **Completion notice:**
  - **Claude Code host:** the host leaves a background completion wait running. When the worker finishes its reply and creates the completion marker file (`<reply>.done`), the host continues automatically. No inbound setting or separate launcher is needed. If the host forgot to start the wait, say "start the wait again"; if the reply is already done, it continues immediately.
  - **Codex host:** the worker notifies the host thread with `codex queue`.
- **Verify and wrap up:** the host independently verifies the reply and the actual changes. If something is wrong, it asks the same worker for a fix. After PASS, it wraps up according to the commit owner in the request, then closes the worker terminal.

### Running several tasks at once

If you ask for several tasks at once, or ask for a new one while another is running, the host gives each task its own thread and worker and runs them in parallel. It verifies replies one at a time, in the order they arrive.

The host proposes a workspace for each task during planning, and you approve it.

| Workspace | When | Wrap-up |
|---|---|---|
| Same tree | The task's files don't overlap with other open tasks. The request lists the files the worker may change; the worker stops and asks before touching anything else | Commit only that task's files |
| Worktree | Files overlap, shared files (lock files, config) are involved, or tests could interfere. The host creates a task branch (`collab/<thread>`) in `<repo>.worktrees/` next to the repository | Commit in the worktree, rebase onto the original branch, and fast-forward. On conflict, leave everything as is and report to you |

A worktree does not contain uncommitted changes from the original working tree or ignored files (such as `node_modules`). The host puts any setup commands the worker needs in the request.

The canonical procedure is [skills/collaborate/RULES.md](skills/collaborate/RULES.md), and the request and reply forms are in [skills/collaborate/templates/](skills/collaborate/templates/). When a thread is created, both are copied into `<thread>/rules/`, and the worker reads the copy named in its request line. Updating the plugin mid-task therefore does not change the rules for work already in progress.

## Requirements

- Verified on: macOS (Darwin 27.2), Orca 1.4.255, Codex CLI 0.167.1, Claude Code 2.1.322, tmux 3.7c, Python 3.14.
- You need `python3`. The helper uses only the standard library.
- You need `orca` or `tmux`, depending on where the worker runs.
- Both `codex` and `claude` must have login and workspace trust completed.
- Dedicated workers run with permission-bypass flags (Claude `--dangerously-skip-permissions`, Codex `--dangerously-bypass-approvals-and-sandbox`).
- Linux is unverified. Native Windows is not supported.

## Development

```bash
claude --plugin-dir "$PWD"                                  # load this checkout as a plugin without installing
claude plugin validate .                                    # check the manifests
python3 -m unittest discover -s tests -v                    # regression isolated with stubs and mocks
python3 tests/e2e_roundtrip.py --env tmux --host claude --followup  # calls real models
```

Adding a local path as a marketplace copies the whole working folder (including the ignored `.comm`) into the cache, so use `--plugin-dir` during development.

The E2E test launches a real host and worker inside `.e2e/` and checks the result file, the reply, and host receipt. The Claude host loads this checkout with `--plugin-dir`. On failure it keeps the workspace and diagnostics; it cleans up only on success (with `--keep`, it keeps them even on success).

When you release an update, bump `version` in both `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`.
