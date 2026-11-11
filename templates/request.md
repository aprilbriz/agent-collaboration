Status: OPEN
Next actor: <worker: claude | codex>
Expected next output: 02-<worker>-implementation.md
<Codex host면 "Codex thread: <CODEX_THREAD_ID>", Claude host면 "Claude socket: <CLAUDE_CODE_MESSAGING_SOCKET>">
In reply to: [사용자 원문]
Verdict: N/A
Approved plan: 01-<host>-request.md

작업의 성격과 복잡도에 따라 필요한 경우, 적절한 모델과 추론 수준의 서브 에이전트를
할당해 주세요. 독립적으로 진행할 수 있는 조사·구현·검증에 적극 활용해 주세요.

## 사용자 원문과 선택

> <사용자 원문 그대로>

<사용자가 선택·승인한 범위와 완료 기준>

## 경로와 commit

- workspace: <worker 시작 폴더 절대경로>
- 대상 저장소: <변경·검증할 저장소 절대경로>
- git·테스트 명령 cwd: <절대경로>
- comm dir: <RULES.md가 있는 폴더 절대경로, 기본값은 workspace/.comm>
- host 시작 위치·지침 인계: <workspace·대상과 대조한 차이와 적용 규칙, 없으면 없음>
- commit 주체: <host | user | 없음> — <사용자 결정·프로젝트 지침 근거>

## baseline

<기준 commit·기존 변경 / 명령·cwd·결과·종료 상태·증거 / 기존 실패와 승인된 처리 방침>

## 결과물과 실행 경계

<완료할 결과·승인 범위·제약. 세부 구현은 worker가 정한다.>
worker는 기존 실패 수정, 요구와 승인 범위 충돌, 보안·회귀 가드 성격 테스트의 삭제·약화가 필요하면 실행 전 멈추고 질문한다.
그 밖의 위임된 작업은 진행한다. worker는 commit하지 않는다.

완료 후 패키지의 `templates/worker-reply.md`에 따라 `<회신 파일 절대경로>` 하나에 작성해 고정한다.
보존할 원출력·해시는 `<thread>/<회신 NN>-evidence/`에 두고 회신에 경로를 적는다.
`Expected next output`은 예상값이며 host 판정에 따라 달라질 수 있다. worker `Verdict`는 `N/A`다.

<Codex host:>
`codex queue --thread "<CODEX_THREAD_ID>" --message "Claude 회신 완료: <회신 파일 절대경로>"`
<Claude host:>
`"<패키지 절대경로>/bin/notify-claude" --socket "<CLAUDE_CODE_MESSAGING_SOCKET>" --message "Codex 회신 완료: <회신 파일 절대경로>"`
