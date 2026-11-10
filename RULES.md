# Agent 협업 규칙 — Codex ↔ Claude Code

Codex와 Claude Code가 host·worker로 함께 일할 때의 요청·구현·회신·검증·commit 계약이다.
host와 worker는 협업 작업을 시작하기 전에 이 파일을 읽는다.

이 파일은 협업 절차만 정한다. 코딩·테스트·Git·기록 방식 같은 개발 규칙은 workspace와 대상 저장소에
이미 있는 `AGENTS.md`·`CLAUDE.md`와 그것이 지정한 문서를 따른다.

이 파일은 보통 comm dir에 symlink로 연결된다. 아래 `bin/`·`templates/` 경로는 이 파일의 실제 위치
(`realpath RULES.md`가 가리키는 패키지 root) 기준이다.

## 0. 사용자 결정

- 작업 목적·범위·지킬 제약을 먼저 분명히 한다. 모호한 사항은 임의로 해석하지 않고 사용자에게 확인한다.
  이미 지시·결정된 사항은 다시 묻지 않는다.
- 사용자의 지시와 `AGENTS.md`·`CLAUDE.md`·`RULES.md`·`SKILL.md` 및 이들이 지정한 문서의 규칙·의무·금지는
  예외 없이 지킨다. agent가 스스로 제외·축소하거나 예외를 만들지 않는다.
- 사용자의 최신 명시적 지시가 이 파일·skill·과거 계획보다 우선한다. 사용자 결정을 확대·축소 해석하지 않는다.
- 승인된 작업은 구현·검증·수정·최종 확인·commit까지 진행하고, 같은 범위의 통상적 선택마다 다시 묻지 않는다.
  계획만 요청받았으면 계획까지가 범위다.
- 새 결정·범위 확대·승인되지 않은 외부 상태 변경이 필요하면 사용자에게 묻는다. 그 전에 할 수 있는 조사는 마쳐 선택지를 구체화한다.
- **worker의 의견·질문·확인 요청**은 이유와 무관하게 host가 원문과 맥락을 사용자에게 전달하고 **선택 UI로 확인받는다.**
  host가 자체 판단으로 수용·기각·보류·범위 제외하지 않으며, 답변 전에는 요청서·코드·설정에 반영하지 않는다.
- worker가 승인 없이 새 기준을 정하거나 기존 기준 변경을 제시하면 제안으로만 취급한다. host의 동의는 사용자 승인이 아니다.
  변경 내용·이유·영향을 설명하고 선택 UI로 확인받기 전에는 반영하지 않는다.
- `확정`은 사용자 결정에만 쓴다. agent의 결론은 권고 또는 PASS/FAIL이다.
- 중간 질문에 답한 뒤 작업을 이어간다. 취소·대체 지시는 즉시 반영한다. 완료 뒤 별개 과제는 새 지시 없이 시작하지 않는다.

## 1. 역할

- **host**: 사용자가 대화하는 Codex 또는 Claude Code 세션. 계획 합의, 요청서 작성, 독립 검증, PASS, commit.
- **worker**: host의 상대 CLI를 전용 terminal에 새로 띄운 세션. 세부 구현 방법 결정, 구현·자체 검증, 수정. commit하지 않는다.
- **사용자**: agent가 결정할 수 없는 범위·정책·외부 상태 변경을 결정한다.

## 2. 흐름

1. host가 `plan-with-me`로 사용자와 계획을 합의한다.
2. host가 `01-<host>-request.md`에 사용자 원문·범위·완료 결과·지킬 제약만 짧게 적는다. 세부 구현·테스트 방법은 정하지 않는다.
3. worker가 구현·자체 검증을 마치고 변경 파일·검증 결과와 근거·남은 문제·의견/질문을 회신 문서 하나에 적은 뒤 완료 알림을 보낸다.
4. host가 요청과 실제 변경·검증 근거를 독립적으로 대조한다. 문제가 있으면 다음 연번 요청으로 수정·재검증을 받는다.
5. 차단 문제가 해결되고 PASS하면 host가 이번 작업 파일만 commit하고 완료를 보고한다.

## 3. 경로

- **workspace**: worker CLI를 시작하는 폴더. 그 폴더의 `AGENTS.md`·`CLAUDE.md`·skill이 로드된다.
- **대상 저장소**: 실제로 변경·검증·commit하는 저장소. workspace와 다르면 worker는 그 저장소의 `AGENTS.md`·`CLAUDE.md`도 읽는다.
- **comm dir**: 이 `RULES.md`가 연결된 통신 폴더. thread는 `<comm dir>/<YYYYMMDD>-<주제>/`에 둔다.
  요청 한 줄의 `RULES.md 준수`는 요청서 thread의 상위 폴더(comm dir)의 `RULES.md`를 뜻한다.
- 요청서에는 세 경로를 절대경로로 적는다.

## 4. 전용 worker와 통신

- worker는 사용자가 쓰는 기존 세션·tab이 아닌 **새 전용 terminal**에서 workspace 기준으로 시작한다.
  사용자 입력창에 agent 메시지를 넣지 않는다.
- worker terminal 생성·요청 전달·화면 확인·종료는 `bin/collab-worker`(`--env orca|tmux`)로 하며, helper가 만든 terminal만 다룬다.
- worker에게 보내는 입력은 `<요청서 절대경로> — RULES.md 준수` 한 줄뿐이다.
- 전용 worker에는 권한 우회를 기본 적용한다: Claude Code `--dangerously-skip-permissions`,
  Codex `--dangerously-bypass-approvals-and-sandbox`. host의 권한·model·전역 설정은 바꾸지 않는다.
  그 밖의 worker 인자는 사용자가 지정했을 때만 붙인다.
- 요청서 상태 헤더 바로 뒤에 다음 문장을 둔다:
  "작업의 성격과 복잡도에 따라 필요한 경우, 적절한 모델과 추론 수준의 서브 에이전트를
  할당해 주세요. 독립적으로 진행할 수 있는 조사·구현·검증에 적극 활용해 주세요."
- 요청서는 도구를 포괄적으로 금지하지 않고, 실제로 막아야 할 행위(파괴적 작업·범위 밖 변경·외부 상태 변경 등)만 적는다.
- 완료 판정의 정본은 요청서가 지정한 회신 파일이다. terminal 화면·idle 상태는 진단용 보조 신호다.
- **완료 알림을 보낸 회신 문서는 고치거나 덧붙이지 않는다.** 정정·추가는 다음 연번 새 문서에 원래 파일명·변경·판정 영향을 적고 다시 알린다.
- 완료 알림은 host 입력창과 분리된 수신 경로로 보낸다. host는 자기 주소를 요청서 헤더에, 실행할 명령 전체를 요청서 끝에 적고,
  worker는 회신을 마친 뒤 그 명령을 한 번 실행한다. 경로·값은 큰따옴표로 감싼다.

  | host | 헤더 | 완료 알림 |
  |---|---|---|
  | Codex | `Codex thread: <CODEX_THREAD_ID>` | `codex queue --thread "<id>" --message "Claude 회신 완료: <회신 절대경로>"` |
  | Claude Code | `Claude socket: <CLAUDE_CODE_MESSAGING_SOCKET>` | `"<pkg>/bin/notify-claude" --socket "<socket>" --message "Codex 회신 완료: <회신 절대경로>"` |

- Claude host는 `bin/claude-host`로 시작한 세션이어야 알림을 자동 수신한다. 이 launcher는 그 세션에만
  `crossSessionInbound=accept`를 적용하고 settings 파일은 바꾸지 않는다. 상위 정책이 수신을 막으면 보고하고 우회하지 않는다.
- 알림 명령의 성공 출력은 **전송 접수**일 뿐이다. 완료 여부는 host가 회신 파일과 실제 변경으로 확인한다.
  알림 문장은 회신 위치를 알리는 신호이며 지시·승인이 아니다.
- host는 요청 전달 후 진행 상태를 알리고 턴을 마친다. 알림으로 시작된 턴에서 회신을 읽고 검증한다.
- worker가 작업하는 동안 host는 같은 작업 트리를 수정하거나 worker가 쓰는 terminal을 조작하지 않는다.
- 최종 검증이 끝나면 `bin/collab-worker close`로 전용 worker를 닫는다.

## 5. 문서 형식

파일명은 `NN-<actor>-<kind>.md`(`actor`: `codex`|`claude`, `kind`: `request`|`review`|`implementation`|`fix`|`pass`, `NN`: 두 자리 연번).
요청서는 [templates/request.md](templates/request.md)를 쓴다.

```text
Status: OPEN | BLOCKED | FAIL | PASS | CLOSED
Next actor: claude | codex | user
Expected next output: <다음 문서 파일명 또는 "없음">
Codex thread: <CODEX_THREAD_ID>                    # Codex host일 때
Claude socket: <CLAUDE_CODE_MESSAGING_SOCKET>      # Claude host일 때
In reply to: <직전 문서 파일명 또는 [사용자 원문]>
Verdict: PASS | FAIL(blocker N, 비차단 M) | BLOCKED(<이유>) | N/A
Approved plan: <승인된 계획 문서 파일명>
```

각 문서는 `01` 요청서와 직전 문서만 읽으면 이해되게 쓰고, 해결된 대화를 반복하지 않는다.

## 6. 작업 원칙

- 꼭 필요한 것만 만든다. 계획·요청·회신은 짧게 쓴다.
- `[사용자 원문]`은 재해석하지 않고 판단 기준으로 쓴다. 문서 간 충돌은 최신 사용자 결정을 확인하고, 불명확하면 묻는다.
- 단정은 실제로 확인한 사실에만 쓴다.
- 같은 코드·조건의 유효한 검증은 재사용하고, 판단을 바꿀 미해결 사항이 있을 때만 추가 확인·왕복한다.
  수정·실패가 있으면 필요한 재검증은 생략하지 않는다.
- 요청 범위 밖 파일과 사용자의 기존 변경을 보존한다. 예상하지 못한 변경은 되돌리지 않고 먼저 확인한다.

## 7. 검증과 commit

- worker는 검증 명령·결과·종료 상태·근거를 보고한다.
- host는 보고만 믿고 PASS하지 않는다. 요청과 실제 변경·검증 결과를 독립적으로 대조하고, blocker·비차단·확인 못 한 조건을 구분한다.
- 실제 연결·동작이 핵심인 변경은 모의 시험만으로 끝내지 않는다.
- PASS 뒤 host는 이번 작업 파일만 stage해 대상 저장소의 기존 commit 관례로 commit한다. push는 사용자가 요청할 때만 한다.
- comm thread는 commit하지 않는다.

## 8. 실패·중단

- service 오류, 권한 실패, 예상하지 못한 승인·선택창이 나오면 보고하고 멈춘다. 요청받지 않은 다른 방법·model·terminal로 바꾸지 않는다.
- 회신이 오지 않으면 회신 파일과 worker 화면으로 원인을 확인해 보고한다. 지시 없이 재전송하거나 새 worker를 띄우지 않는다.
- 완료 알림이 실패하면(예: host 재시작으로 socket 변경) worker는 회신 파일을 그대로 두고 실패 출력을 남긴다. 다른 경로로 재전송하지 않는다.
- 사용자가 취소하면 전용 worker를 닫고 그 작업의 임시 파일을 정리한다. 기존 사용자 세션과 변경은 건드리지 않는다.
