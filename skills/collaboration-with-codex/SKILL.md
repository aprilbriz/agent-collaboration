---
name: collaboration-with-codex
description: Claude Code host가 승인된 작업을 전용 Codex worker에게 요청하고 완료 알림 뒤 독립 검증한다. commit은 요청서의 주체를 따른다. 계획 합의 전에는 plan-with-me를 먼저 쓴다.
---

# Codex worker에게 승인된 작업 전달

절차 정본은 comm dir의 `RULES.md`다. 먼저 읽는다. `<pkg>`는 이 skill의 실제 경로(`<pkg>/skills/<이름>`)에서 구한다.

1. `plan-with-me`의 사용자 결정과 이후 지시를 읽는다. workspace·대상 저장소·comm dir(기본 `<workspace>/.comm`)을 정한다.
   host 시작 위치·로드한 지침을 workspace·대상과 대조하고, 다르면 해당 지침을 읽어 적용 규칙과 차이를 요청서에 인계한다.
2. Bash 도구의 `$PPID`(Claude PID)로 `"<pkg>/bin/claude-host" --check-host "$PPID" --workspace "<workspace>"`를 실행한다.
   이 진단은 `ps` 프로세스 인자의 inline `crossSessionInbound=accept`를 감지하고 재시작 명령을 출력만 한다.
   `accept_arg_detected` 외의 결과는 수신 설정 미적용·불명으로 취급한다. settings 파일 내용이나 managed 정책 적용 여부는 추정하지 않는다.
   `$CLAUDE_CODE_MESSAGING_SOCKET`가 비어 있으면 native inbox를 쓸 수 없음을 알린다.
   미감지·불명일 때는 다음 선택의 동작·영향과 진단의 `restart_command`를 먼저 설명하고 **선택 UI로** 결정받는다.
   - ① 이대로 진행: 알림이 승인 대화상자로 보류될 수 있으므로 회신 파일로 확인한다.
   - ② 재시작 명령 제시: 사용자가 그대로 붙여 넣을 `cd "<workspace>" && "<pkg>/bin/claude-host" --continue` 명령을 제시한다.
     `ORCA_CLAUDE_AGENT_VIEW_PLUGIN_DIR`가 설정돼 있을 때만 `--plugin-dir "<그 값>"`을 포함한다. helper가 경로를 shell quote한다.
   host가 스스로 재시작하거나 settings 파일을 고치지 않는다. 재시작하면 새 socket을 요청서에 사용한다.
3. comm dir에 이 패키지의 `RULES.md`가 없으면 `"<pkg>/bin/collab-init" "<workspace>" --comm-dir "<comm dir>"` 적용 여부를 사용자에게 선택 UI로 묻는다. 직접 설치하지 않는다.
4. 요청 전 baseline(기준 코드·기존 변경, 명령·cwd·결과·기존 실패와 처리 방침)을 확인하거나 같은 조건의 유효한 결과를 승계한다.
   `templates/request.md`로 `01-claude-request.md`를 작성한다. 경로·git/테스트 cwd·commit 주체와 근거를 명시한다.
   worker가 기존 실패 수정 / 요구와 승인 범위 충돌 / 보안·회귀 가드 성격 테스트 삭제·약화 전에 멈춰 질문하도록 적는다.
   헤더는 `Claude socket: <$CLAUDE_CODE_MESSAGING_SOCKET 값>`, 끝에는 아래 명령 전체를 넣는다.
   `"<pkg>/bin/notify-claude" --socket "<그 값>" --message "Codex 회신 완료: <회신 절대경로>"`
5. 각 항목을 사용자 승인과 대조한 뒤 전용 worker를 띄운다. 환경은 사용자가 쓰는 `orca` 또는 `tmux`다.

   ```bash
   "<pkg>/bin/collab-worker" start --env orca --worker codex \
     --workspace "<workspace>" --request "<요청서 절대경로>" --title "<작업명>"
   ```

   권한 우회는 worker 기본값이고, 그 밖의 worker 인자는 사용자가 지정했을 때만 `--` 뒤에 붙인다.
   thread의 `worker-record.json`과 handle을 기록한다. 접수·turn 시작 관찰·완료를 구별하며, 미관찰 시 제시된 확인 명령을 자동 실행하지 않는다.
   생성 후 실패하면 단계·handle·전송 여부를 보고하고 자동 종료·재전송하지 않는다. 진행 상태를 알린 뒤 턴을 마친다.
6. 완료 알림으로 시작된 턴에서 회신과 실제 변경·증거를 독립 검증한다. worker `Verdict`는 `N/A`이고 PASS/FAIL은 host가 판정한다.
   worker 의견·질문은 사용자에게 선택 UI로 전달한다. 원출력·해시는 `<thread>/<회신 NN>-evidence/`에서 확인한다.
   수정은 `templates/review-request.md`로 다음 연번에 작성하고 같은 worker에 보낸다.
   `"<pkg>/bin/collab-worker" send --env <env> --handle <handle> --request "<다음 요청서>"`
   read·close는 같은 thread를 반드시 지정한다. `Expected next output`은 예상값이라 판정에 따라 review/pass 등으로 달라질 수 있다.
7. PASS 후 요청서의 commit 주체가 host일 때만 이번 작업 파일을 commit한다. user·없음이면 commit하지 않는다.
   정리 후 `"<pkg>/bin/collab-worker" close --env <env> --handle <handle> --thread "<thread 절대경로>"`로 전용 worker를 닫는다(tmux는 해당 pane만).
