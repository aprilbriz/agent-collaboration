# agent-collaboration

**English** | [한국어](README.ko.md)

A collaboration package that runs one of Codex and Claude Code as the **host** (planning and verification) and the other as the **worker** (working in a dedicated terminal). Orca and tmux are supported.

```text
User agrees on a plan → request stating the baseline and commit owner → dedicated worker
→ fixed reply + completion notification → independent host verification → (fix round trips) → after PASS, the designated owner commits
```

> The procedure documents the agents read (`RULES.md`, skills, templates) are written in Korean. This README is a translation of [README.ko.md](README.ko.md); when the two differ, the Korean version is authoritative.

| File | Role |
|---|---|
| [RULES.md](RULES.md) | Canonical procedure read by the host and the worker |
| [templates/request.md](templates/request.md) | Initial request |
| [templates/review-request.md](templates/review-request.md) | Follow-up review/fix request |
| [templates/worker-reply.md](templates/worker-reply.md) | Worker reply (`Verdict: N/A`) |
| [skills/plan-with-me](skills/plan-with-me/SKILL.md) | Agree on a plan through a selection UI |
| [skills/collaboration-with-claude](skills/collaboration-with-claude/SKILL.md) | Entry point for a Codex host |
| [skills/collaboration-with-codex](skills/collaboration-with-codex/SKILL.md) | Entry point for a Claude Code host |
| `bin/collab-init` | Link RULES and skills into a project |
| `bin/collab-worker` | Start a worker, deliver requests, inspect its screen, close it |
| `bin/claude-host` / `bin/notify-claude` | Launch and diagnose the Claude host / native inbox notification |

## Requirements

- Verified on: macOS (Darwin 27.2), Orca 1.4.255, Codex CLI 0.167.0, Claude Code 2.1.322, tmux 3.7c, Python 3.14.
- You need `python3`, `orca` or `tmux` for the environment you use, and `codex` and `claude` with login and workspace trust completed.
- Linux is unverified. Native Windows is not supported (`notify-claude` uses a Unix socket).

## 1. Apply to a project

Put the package anywhere and run this once per workspace. If the comm dir has no `RULES.md`, the host skill can also ask for approval and run it. If you move the package, apply the symlinks again.

```bash
PKG="/path/to/agent-collaboration"
"$PKG/bin/collab-init" "<absolute workspace path>" --dry-run
"$PKG/bin/collab-init" "<absolute workspace path>"
# The default is <workspace>/.comm. You can also name a separate path.
"$PKG/bin/collab-init" "<workspace>" \
  --comm-dir "<target repo>/.comm"
```

- Creates `<comm dir>/RULES.md` and symlinks under the workspace's `.agents/skills/` and `.claude/skills/`.
- The created paths are ignored locally through that repository's `.git/info/exclude`. The target's tracked `.gitignore` is not changed.
- If existing files or skills, or a parent path that is not a directory, conflict, it lists them before applying and exits. Argument errors print the usage and exit with code 2.
- Skills are read when the CLI starts. Project development rules follow the `AGENTS.md` and `CLAUDE.md` of the workspace and the target repository.

## 2. Run the host and the worker

Start the host in the workspace. If it started elsewhere, it reads the guidance of the declared workspace and target repository, and hands over the difference between its start location and the guidance it loaded in the request. The worker starts in the workspace and runs git and tests in the target repository cwd stated in the request.

```bash
cd "<workspace>" && codex                     # Codex host
cd "<workspace>" && "$PKG/bin/claude-host"    # Claude host (Orca and tmux)
```

A Claude host started some other way still checks the inbound setting from its process arguments. From Claude's Bash tool:

```bash
"$PKG/bin/claude-host" --check-host "${CLAUDE_PID:-$PPID}" --workspace "<workspace>"
```

The diagnostic PID prefers `CLAUDE_PID` and falls back to `$PPID`. The check looks for an inline `crossSessionInbound=accept` in the actual Claude argv and prints `status`, `restart_command`, and `warnings`. The argument check does not guarantee managed policy or actual delivery. When the setting is not detected or unknown, the host asks through a selection UI to either **① continue in the current session** (notifications may be held behind an approval dialog, so check the reply file) or **② show the restart command**. It never restarts automatically. When `CLAUDE_CODE_SESSION_ID` is set, the printed command names the current conversation by ID.

```bash
cd "<workspace>" && "$PKG/bin/claude-host" --resume "<value of CLAUDE_CODE_SESSION_ID>"
# Append to the command above only when ORCA_CLAUDE_AGENT_VIEW_PLUGIN_DIR is set:
# --plugin-dir "<value of ORCA_CLAUDE_AGENT_VIEW_PLUGIN_DIR>"
```

The helper generates a ready-to-paste command with the real paths and ID shell-quoted. Copy the command first, **exit the existing session with `/exit`, then paste it into a shell and run it**. Running it while the old session is still open makes two processes open the same conversation.

In the verified environment, `--resume <ID>` from the workspace resumed the same conversation even when the original start cwd was different. Without an ID, the helper prints `--continue` with a limitation warning. `--continue` opens the most recent conversation of the workspace, so if the original cwd or the most recent conversation differs, it may open another conversation or find none. Conversations created in print mode (`claude -p`) cannot be picked up by this restart procedure.

After a restart, record the new socket in the request. The launcher applies the inbound setting only to that session and does not change global or project settings. This session also accepts messages from other local Claude sessions automatically; if managed settings block it, it does not work around them.

Before a request, the host writes the baseline command, cwd, result, and handling of pre-existing failures, plus `commit 주체: host | user | 없음` (commit owner: host | user | none) with its reason. The worker stops and asks before fixing pre-existing failures, resolving conflicts between requirements and the approved scope, or deleting or weakening tests that act as security or regression guards. It proceeds with all other delegated work.

```bash
"$PKG/bin/collab-worker" start --env orca --worker claude \
  --workspace "<workspace>" --request "<absolute request path>" --title "<job name>"
"$PKG/bin/collab-worker" send --env orca --handle "<handle>" --request "<next request>"
"$PKG/bin/collab-worker" read --env orca --handle "<handle>" --thread "<absolute thread path>"
"$PKG/bin/collab-worker" close --env orca --handle "<handle>" --thread "<absolute thread path>"
# --env tmux for tmux; --worker codex for a Claude host's worker
```

- start leaves `worker-record.json` in the thread. send checks the record and the running instance through the request's thread, and read and close through `--thread`. A different thread, a replaced process, or the host's own terminal is rejected. Orca titles are not used for identification.
- tmux close closes only the worker pane and keeps other windows. Bypassing worker permissions is the default; extra arguments are passed after `--` only when the user specifies them.
- On tmux, you can watch the dedicated worker with the `attach` command from the start output.
- Outside Orca, `--orca-worktree <selector>` is required. The send result distinguishes acceptance from an observed turn start. When no turn start is observed, the printed `--retry-request` confirmation command is not run automatically.
- If something fails after creation, the step, handle, send state, and record are kept, with no automatic close or resend. Diagnose with the reply file and the worker screen.

If a closed (`closed`) record exists, you can start again in the same thread. The previous record is preserved in `worker-record-history/` without being overwritten. If an `open` or `failed` record exists, close it first.

If the instance cannot be matched, for example after a failure before the identity was saved or after an Orca app restart, the helper does not touch the terminal. **After the user confirms they closed that terminal/pane directly in Orca/tmux**, close only the record with a reason using the command below, then run start again. There is no need to edit the JSON by hand.

```bash
"$PKG/bin/collab-worker" mark-closed --env orca --handle "<handle from the record>" \
  --thread "<absolute thread path>" --closed-by-user --reason "Confirmed closed directly in Orca"
# --env tmux for tmux. This command does not query, type into, or close any terminal.
```

If the record itself could not be created, first close the leftover terminal directly, fix the write error at the record path, and retry start. Without write permission for the record, `mark-closed` cannot save either.

## 3. Completion and verification

| Host | Completion notification the worker runs |
|---|---|
| Codex | `codex queue --thread "<CODEX_THREAD_ID>" --message "Claude 회신 완료: <reply>"` |
| Claude Code | `"$PKG/bin/notify-claude" --socket "<CLAUDE_CODE_MESSAGING_SOCKET>" --message "Codex 회신 완료: <reply>"` |

The message text (`… 회신 완료: <reply>`, "reply complete") is the literal string the skills look for, so it stays in Korean.

Successful output means the message was accepted for delivery. The host independently checks the reply file, the actual changes, and the verification evidence. Raw output and hashes to keep go in `<thread>/<reply NN>-evidence/`. A reply that has already been announced is not edited; corrections go in the next numbered file. `Expected next output` is an estimate and may become review/pass and so on depending on the host's verdict. PASS/FAIL verdicts belong to the host only.

After PASS, the commit follows the owner in the request. Only when it is host does the host stage and commit this task's files; for user or none, the host does not commit either. Comm threads are not committed, and pushing follows a separate user instruction.

```bash
python3 -m unittest discover -s tests -v                  # regression isolated with stubs and mocks
python3 tests/e2e_roundtrip.py --env tmux --host claude --followup  # calls real models
```

The E2E test launches a real host and worker inside `.e2e/` and checks the result file, the reply, and delivery. On failure it keeps the workspace and diagnostics; it cleans up only on success (with `--keep`, it keeps them even on success). It touches only the terminals it created.
