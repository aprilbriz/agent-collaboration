# agent-collaboration

Codex와 Claude Code 중 하나를 **host**(사용자와 대화·계획·검증·commit), 다른 하나를 **worker**(전용 terminal에서 구현)로
쓰는 협업 패키지입니다. Orca와 tmux에서 모두 쓸 수 있습니다.

```text
요청서(01-<host>-request.md) → 전용 worker 작업 → 고정한 회신 문서 + 완료 알림
→ host 독립 검증 → (수정 왕복) → PASS 후 host commit
```

| 파일 | 역할 |
|---|---|
| [RULES.md](RULES.md) | 절차 정본. host·worker 모두 읽음 |
| [templates/request.md](templates/request.md) | 요청서 양식 |
| [skills/plan-with-me](skills/plan-with-me/SKILL.md) | 계획 합의(선택 UI 질문·사전 설명·답변 대기) |
| [skills/collaboration-with-claude](skills/collaboration-with-claude/SKILL.md) | Codex host 진입 |
| [skills/collaboration-with-codex](skills/collaboration-with-codex/SKILL.md) | Claude Code host 진입 |
| `bin/collab-worker` | 전용 worker terminal 생성·요청 전달·화면 확인·종료 (Orca/tmux) |
| `bin/notify-claude` | Codex worker → Claude host native inbox 완료 알림 |
| `bin/claude-host` | 완료 알림을 자동 수신하는 Claude host 세션 실행 |
| `bin/collab-init` | 프로젝트에 RULES.md·skill 연결 |

## 실행 전제와 확인 범위

- 확인한 환경: macOS(Darwin 27.2), Orca 1.4.255, Codex CLI 0.167.0, Claude Code 2.1.322, tmux 3.7c, Python 3.14.
- 필요: `python3`, 사용할 환경의 `orca` CLI 또는 `tmux`, `codex`·`claude` CLI(각자 로그인·workspace trust 완료).
- Linux는 확인하지 않았습니다. 네이티브 Windows는 지원하지 않습니다(`notify-claude`가 unix socket만 씁니다).

## 1. 프로젝트에 적용

이 패키지는 원하는 위치에 두고(전역 설치 없음), 쓰려는 workspace마다 사용자가 한 번 실행합니다.
symlink가 이 위치를 가리키므로 패키지를 옮기면 다시 적용합니다.

```bash
PKG="/path/to/agent-collaboration"   # 이 저장소를 둔 위치
"$PKG/bin/collab-init" "<workspace 절대경로>" --dry-run   # 할 일 확인
"$PKG/bin/collab-init" "<workspace 절대경로>"             # 적용
# comm dir을 workspace가 아닌 대상 저장소에 둘 때:
"$PKG/bin/collab-init" "<workspace>" --comm-dir "<대상 저장소>/docs/.comm"
```

- `<comm dir>/RULES.md`, `<workspace>/.agents/skills/`(Codex용), `<workspace>/.claude/skills/`(Claude용)에 symlink를 만듭니다.
- 만든 경로는 그 git 저장소의 `.git/info/exclude`에 넣어 개인 로컬에서만 무시합니다. 추적 파일(`.gitignore` 등)은 바꾸지 않습니다.
- 연결할 자리에 다른 `RULES.md`나 같은 이름의 skill이 이미 있으면 **아무것도 바꾸지 않고** 그 목록을 알린 뒤 종료 코드 1로 끝납니다.
  기존 파일 정리는 사용자가 결정합니다.
- skill 목록은 CLI가 시작할 때 읽습니다. 적용 뒤 새로 시작한 세션에서 보입니다.

### 프로젝트 고유 지침

프로젝트별 개발 규칙은 이 패키지에 넣지 않습니다. host·worker는 workspace와 대상 저장소에 이미 있는
`AGENTS.md`·`CLAUDE.md`와 그것이 지정한 문서를 읽고 따릅니다(RULES.md 머리말·3절).

## 2. 실행

host 세션은 workspace에서 시작합니다. worker는 host가 `collab-worker`로 새 전용 terminal에 띄웁니다.
사용자는 host에게 평소처럼 요청하고, 계획 합의는 `plan-with-me`, 전달은 host 진입 skill이 맡습니다.

| 조합 | host 시작 | host가 쓰는 worker 명령 |
|---|---|---|
| Orca × Codex host | Orca terminal에서 `cd "<workspace>" && codex` | `collab-worker start --env orca --worker claude ...` |
| tmux × Codex host | tmux에서 `cd "<workspace>" && codex` | `collab-worker start --env tmux --worker claude ...` |
| Orca × Claude host | Orca terminal에서 `cd "<workspace>" && "$PKG/bin/claude-host"` | `collab-worker start --env orca --worker codex ...` |
| tmux × Claude host | tmux에서 `cd "<workspace>" && "$PKG/bin/claude-host"` | `collab-worker start --env tmux --worker codex ...` |

```bash
"$PKG/bin/collab-worker" start --env orca --worker claude \
  --workspace "<workspace>" --request "<요청서 절대경로>" --title "<작업명>" [-- <추가 worker 인자>]
"$PKG/bin/collab-worker" send  --env orca --handle <handle> --request "<다음 요청서>"   # 수정 왕복
"$PKG/bin/collab-worker" read  --env orca --handle <handle>                            # 진단용 화면 확인
"$PKG/bin/collab-worker" close --env orca --handle <handle>
# tmux에서는 --env tmux
```

- 전용 worker에는 기존처럼 권한 우회를 기본 적용합니다: Claude worker `--dangerously-skip-permissions`,
  Codex worker `--dangerously-bypass-approvals-and-sandbox`. host의 도구 권한·model·전역 설정은 바꾸지 않습니다.
  그 밖의 worker 인자는 사용자가 지정한 경우에만 `--` 뒤에 붙입니다.
- `--env orca`는 Orca 안의 terminal에서 실행해야 현재 Orca workspace에 tab이 생깁니다. 밖에서 실행하면 `--orca-worktree <선택자>`를 줍니다.
- `--env tmux`는 매번 새 `collab-<workspace>-<작업명>-<무작위>` session을 만들고, worker가 실행된 pane id(`%N`)를 `handle`로 돌려줍니다.
  `send`·`read`·`close`는 그 pane에 결속되어 사용자가 창을 바꿔도 원래 worker를 가리킵니다. 출력의 `attach` 명령으로 worker를 볼 수 있습니다.

### 완료 알림

| host | worker가 실행하는 명령 | 비고 |
|---|---|---|
| Codex | `codex queue --thread "<CODEX_THREAD_ID>" --message "Claude 회신 완료: <회신>"` | host 입력창과 분리된 queue |
| Claude Code | `"$PKG/bin/notify-claude" --socket "<CLAUDE_CODE_MESSAGING_SOCKET>" --message "Codex 회신 완료: <회신>"` | native inbox |

- `claude-host`는 그 세션에만 `crossSessionInbound=accept`를 적용합니다. 전역·프로젝트 settings는 바꾸지 않습니다.
  이 세션은 worker뿐 아니라 **이 컴퓨터의 다른 로컬 Claude 세션이 보내는 메시지도 자동으로 받습니다.**
  관리자(managed) 설정이 수신을 막으면 그 설정이 우선하며, 이 패키지는 우회하지 않습니다.
- 일반 `claude`로 시작한 host는 사용자 설정에 따라 알림이 보류(승인 대화상자)되거나 거부될 수 있습니다.
- 두 명령의 성공 출력은 **알림 전송 접수**입니다. 작업 완료는 host가 회신 파일과 실제 diff로 확인합니다.
- host가 재시작되면 Claude socket 경로가 바뀝니다. 진행 중인 요청의 알림은 실패하므로 회신 파일로 상태를 확인합니다.

## 3. 검증

```bash
python3 -m unittest discover -s tests -v                    # 격리 회귀(실제 모델·사용자 terminal 미사용)
python3 tests/e2e_roundtrip.py --env tmux --host claude --followup   # 실제 CLI 최소 왕복 + 후속 요청(모델 호출 발생)
```

`e2e_roundtrip.py`는 이 저장소 안의 `.e2e/` 임시 workspace에서 실제 host·worker CLI를 띄워 최소 요청 하나를 왕복합니다.
worker 회신 파일, 요청한 결과 파일 내용, host 화면의 수신 응답을 모두 확인해야 성공(종료 코드 0)이며,
끝나면 자신이 만든 terminal과 임시 폴더를 정리합니다. 자세한 범위는 스크립트 머리말을 보세요.
