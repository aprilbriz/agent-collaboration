# agent-collaboration

Codex와 Claude Code 중 하나를 **host**(계획·검증), 다른 하나를 **worker**(전용 terminal에서 작업)로 쓰는 협업 패키지입니다. Orca와 tmux를 지원합니다.

```text
사용자 계획 합의 → baseline·commit 주체를 적은 요청서 → 전용 worker
→ 고정 회신 + 완료 알림 → host 독립 검증 → (수정 왕복) → PASS 후 지정된 주체가 commit
```

| 파일 | 역할 |
|---|---|
| [RULES.md](RULES.md) | host·worker가 읽는 절차 정본 |
| [templates/request.md](templates/request.md) | 최초 요청 |
| [templates/review-request.md](templates/review-request.md) | 후속 review/fix 요청 |
| [templates/worker-reply.md](templates/worker-reply.md) | worker 회신 (`Verdict: N/A`) |
| [skills/plan-with-me](skills/plan-with-me/SKILL.md) | 선택 UI로 계획 합의 |
| [skills/collaboration-with-claude](skills/collaboration-with-claude/SKILL.md) | Codex host 진입 |
| [skills/collaboration-with-codex](skills/collaboration-with-codex/SKILL.md) | Claude Code host 진입 |
| `bin/collab-init` | 프로젝트에 RULES·skill 연결 |
| `bin/collab-worker` | 전용 worker 생성·요청 전달·화면 확인·종료 |
| `bin/claude-host` / `bin/notify-claude` | Claude host 실행·진단 / native inbox 알림 |

## 실행 전제

- 확인 환경: macOS(Darwin 27.2), Orca 1.4.255, Codex CLI 0.167.0, Claude Code 2.1.322, tmux 3.7c, Python 3.14.
- `python3`, 사용할 환경의 `orca` 또는 `tmux`, 로그인·workspace trust를 마친 `codex`·`claude`가 필요합니다.
- Linux는 미확인입니다. 네이티브 Windows는 지원하지 않습니다(`notify-claude`는 Unix socket 사용).

## 1. 프로젝트에 적용

패키지를 원하는 위치에 두고 workspace마다 사용자가 한 번 실행합니다. 패키지를 옮기면 symlink를 다시 적용합니다.

```bash
PKG="/path/to/agent-collaboration"
"$PKG/bin/collab-init" "<workspace 절대경로>" --dry-run
"$PKG/bin/collab-init" "<workspace 절대경로>"
# 기본값은 <workspace>/.comm. 별도 경로도 명시할 수 있습니다.
"$PKG/bin/collab-init" "<workspace>" \
  --comm-dir "<대상 저장소>/.comm"
```

- `<comm dir>/RULES.md`와 workspace의 `.agents/skills/`·`.claude/skills/`에 symlink를 만듭니다.
- 만든 경로는 해당 저장소의 `.git/info/exclude`에 로컬로 무시합니다. 대상의 추적 `.gitignore`는 바꾸지 않습니다.
- 기존 파일·skill 또는 디렉터리가 아닌 부모 경로가 충돌하면 적용 전에 목록을 알리고 종료합니다. 인자 오류는 사용법과 종료 코드 2를 출력합니다.
- skill은 CLI 시작 때 읽습니다. 프로젝트 개발 규칙은 workspace·대상 저장소의 `AGENTS.md`·`CLAUDE.md`를 따릅니다.

## 2. host와 worker 실행

host는 workspace에서 시작합니다. 다른 곳에서 시작했다면 선언한 workspace·대상 저장소 지침을 읽고, 시작 위치·로드한 지침과의 차이를 요청서에 인계합니다. worker는 workspace에서 시작하며 git·테스트는 요청서의 대상 저장소 cwd에서 실행합니다.

```bash
cd "<workspace>" && codex                     # Codex host
cd "<workspace>" && "$PKG/bin/claude-host"    # Claude host (Orca·tmux 공통)
```

일반 Claude host도 프로세스 인자로 수신 설정을 확인합니다. Claude의 Bash 도구에서:

```bash
"$PKG/bin/claude-host" --check-host "$PPID" --workspace "<workspace>"
```

진단은 실제 Claude argv의 inline `crossSessionInbound=accept`를 검사하고 `status`·`restart_command`를 출력합니다. 인자 확인은 managed 정책·실제 수신까지 보장하지 않습니다. 미감지·불명일 때 host는 선택 UI로 **① 현재 세션 계속**(알림이 승인 대화상자로 보류될 수 있어 회신 파일 확인) 또는 **② 재시작 명령 제시**를 선택받습니다. 자동 재시작하지 않습니다. 출력되는 명령의 형태는 다음과 같습니다.

```bash
cd "<workspace>" && "$PKG/bin/claude-host" --continue
# ORCA_CLAUDE_AGENT_VIEW_PLUGIN_DIR가 설정됐을 때만 위 명령 끝에 추가:
# --plugin-dir "<ORCA_CLAUDE_AGENT_VIEW_PLUGIN_DIR 값>"
```

helper는 실제 경로를 shell quote한 붙여 넣기용 명령을 생성합니다. 재시작하면 새 socket을 요청서에 기록합니다. launcher는 세션에만 수신 설정을 적용하며 전역·프로젝트 settings를 바꾸지 않습니다. 이 세션은 다른 로컬 Claude 세션의 메시지도 자동 수신하며, managed 설정이 막으면 우회하지 않습니다.

host는 요청 전 baseline의 명령·cwd·결과·기존 실패 처리와 `commit 주체: host | user | 없음` 및 근거를 적습니다. worker는 기존 실패 수정, 요구와 승인 범위 충돌, 보안·회귀 가드 성격 테스트의 삭제·약화 전에 멈춰 질문합니다. 그 밖의 위임된 작업은 진행합니다.

```bash
"$PKG/bin/collab-worker" start --env orca --worker claude \
  --workspace "<workspace>" --request "<요청서 절대경로>" --title "<작업명>"
"$PKG/bin/collab-worker" send --env orca --handle "<handle>" --request "<다음 요청서>"
"$PKG/bin/collab-worker" read --env orca --handle "<handle>" --thread "<thread 절대경로>"
"$PKG/bin/collab-worker" close --env orca --handle "<handle>" --thread "<thread 절대경로>"
# tmux는 --env tmux, Claude host의 worker는 --worker codex
```

- start는 thread에 `worker-record.json`을 남깁니다. send는 요청서의 thread, read·close는 `--thread`로 기록과 실행 인스턴스를 대조합니다. 다른 thread·교체된 프로세스·host 자기 terminal은 거부합니다. Orca 제목은 식별에 쓰지 않습니다.
- tmux close는 worker pane만 닫아 다른 window를 보존합니다. worker 권한 우회는 기본값이며 추가 인자는 사용자 지정 시에만 `--` 뒤로 전달합니다.
- tmux에서는 start 출력의 `attach` 명령으로 전용 worker를 볼 수 있습니다.
- Orca 밖에서 실행하면 `--orca-worktree <선택자>`가 필요합니다. send 결과는 접수와 turn 시작 관찰을 구별합니다. 미관찰 시 출력된 `--retry-request` 확인 명령을 자동 실행하지 않습니다.
- 생성 뒤 실패해도 단계·handle·전송 여부와 기록을 보존하며 자동 종료·재전송하지 않습니다. 회신 파일과 worker 화면으로 진단합니다.

닫힌(`closed`) 기록이 있으면 같은 thread에서 다시 start할 수 있습니다. 이전 기록 원문은 `worker-record-history/`에 덮어쓰지 않고 보존합니다. `open`·`failed` 기록이 있으면 먼저 close합니다.

identity 저장 전 실패나 Orca 앱 재시작 등으로 인스턴스 대조가 불가능하면 helper는 terminal을 조작하지 않습니다. **사용자가 Orca/tmux에서 해당 terminal/pane을 직접 닫았음을 확인한 뒤**, 아래 명령으로 사유를 남겨 기록만 닫고 start를 다시 실행합니다. JSON을 직접 편집할 필요는 없습니다.

```bash
"$PKG/bin/collab-worker" mark-closed --env orca --handle "<기록의 handle>" \
  --thread "<thread 절대경로>" --closed-by-user --reason "Orca에서 직접 종료 확인"
# tmux는 --env tmux. 이 명령은 terminal 조회·입력·종료를 수행하지 않습니다.
```

기록 자체가 생성되지 못했다면 먼저 남은 terminal을 직접 닫고, 기록 경로의 쓰기 오류를 해결한 뒤 start를 재시도합니다. 기록 쓰기 권한이 없으면 `mark-closed`도 저장할 수 없습니다.

## 3. 완료·검증

| host | worker가 실행할 완료 알림 |
|---|---|
| Codex | `codex queue --thread "<CODEX_THREAD_ID>" --message "Claude 회신 완료: <회신>"` |
| Claude Code | `"$PKG/bin/notify-claude" --socket "<CLAUDE_CODE_MESSAGING_SOCKET>" --message "Codex 회신 완료: <회신>"` |

성공 출력은 전송 접수입니다. host는 회신 파일·실제 변경·검증 증거를 독립 대조합니다. 보존할 원출력·해시는 `<thread>/<회신 NN>-evidence/`에 둡니다. 이미 알린 회신은 고치지 않고 다음 연번으로 정정합니다. `Expected next output`은 예상값으로 host 판정에 따라 review/pass 등으로 달라질 수 있습니다. PASS/FAIL 판정은 host 전용입니다.

PASS 뒤 commit은 요청서의 주체를 따릅니다. host인 경우에만 이번 작업 파일을 stage·commit하고, user·없음이면 host도 commit하지 않습니다. comm thread는 commit하지 않으며 push는 별도 사용자 지시에 따릅니다.

```bash
python3 -m unittest discover -s tests -v                  # stub·mock 격리 회귀
python3 tests/e2e_roundtrip.py --env tmux --host claude --followup  # 실제 모델 호출
```

E2E는 `.e2e/` 안에서 실제 host·worker를 띄워 결과 파일·회신·수신을 확인합니다. 실패하면 workspace·진단을 보존하고 성공 때만 정리합니다(`--keep`이면 성공해도 보존). 자신이 만든 terminal만 다룹니다.
