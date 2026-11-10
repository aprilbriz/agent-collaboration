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

<plan-with-me로 사용자가 선택·승인한 내용>

## 경로

- workspace: <worker를 시작하는 폴더 절대경로>
- 대상 저장소: <변경·검증·commit할 저장소 절대경로>
- comm dir: <RULES.md가 있는 폴더 절대경로>

## 결과물과 범위

<완료할 결과, 기본 준수사항. 세부 구현·테스트 방법은 worker가 정한다.>

## 실행 경계

<파괴적 작업·외부 상태 변경·범위 밖 terminal 접근 등 실제로 막아야 하는 것만. commit은 host가 한다.>

완료 후 변경 파일·검증 명령/원출력/종료 상태·남은 한계·의견/질문을 한 번에
`<회신 파일 절대경로>`에 작성해 고정하고 아래 명령으로 알려 주세요.

<Codex host:>
`codex queue --thread "<CODEX_THREAD_ID>" --message "Claude 회신 완료: <회신 파일 절대경로>"`
<Claude host:>
`"<패키지 절대경로>/bin/notify-claude" --socket "<CLAUDE_CODE_MESSAGING_SOCKET>" --message "Codex 회신 완료: <회신 파일 절대경로>"`
