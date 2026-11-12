Status: FAIL
Next actor: <worker: claude | codex>
Expected next output: <NN>-<worker>-fix.md
<host 알림 주소: Codex thread 또는 Completion marker(이번 회신 기준)>
In reply to: <직전 worker 회신>
Verdict: <host 판정과 blocker·비차단 수>
Approved plan: <01 요청서와 이후 사용자 결정 문서>

작업의 성격과 복잡도에 따라 필요한 경우, 적절한 모델과 추론 수준의 서브 에이전트를
할당해 주세요. 독립적으로 진행할 수 있는 조사·구현·검증에 적극 활용해 주세요.

## host 검증과 사용자 결정

<독립 검증 근거 / 수정할 문제 / 사용자가 결정한 내용. 새 결정은 승인 후에만 반영한다.>

## 수정 범위와 기준

<이번에 고칠 것·보존할 것·완료 기준. 01 대비 경로·cwd·commit 주체·baseline·실행 경계의 변경, 없으면 모두 01과 동일.>
worker는 기존 실패 수정, 요구와 승인 범위 충돌, 보안·회귀 가드 성격 테스트의 삭제·약화가 필요하면 실행 전 멈추고 질문한다.

완료 후 `<templates 절대경로>/worker-reply.md`로 `<회신 절대경로>`에 고정한다.
원출력·해시는 `<thread>/<회신 NN>-evidence/`에 둔다. worker `Verdict`는 `N/A`다.
이미 알린 회신은 수정하지 않고 정정·판정 영향을 새 회신에 적는다.
`Expected next output`은 예상값이며 host 판정에 따라 review/pass 등으로 달라질 수 있다.

<01과 같은 방식의 완료 알림 명령 전체(Claude host면 이번 회신의 .done). 경로·값은 큰따옴표로 감싼다.>
