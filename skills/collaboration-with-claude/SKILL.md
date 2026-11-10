---
name: collaboration-with-claude
description: Codex가 host일 때 승인된 작업을 전용 Claude Code worker에게 요청하고, codex queue 완료 알림 뒤 회신을 검증·commit한다. 계획 합의 전에는 plan-with-me를 먼저 쓴다.
---

# Claude worker에게 승인된 작업 전달 (Codex host)

절차·양식·금지사항의 정본은 comm dir의 `RULES.md`다. 먼저 읽고 그대로 따른다.
이 스킬은 Codex host에서만 다른 부분만 적는다. `<pkg>`는 패키지 root로, 이 skill 폴더의 실제 위치(`realpath`)가 `<pkg>/skills/<skill 이름>`이다.

1. `plan-with-me`로 사용자에게 승인받은 계획, 사용자의 직접 지시와 이후 결정 내용을 읽는다.
2. workspace·대상 저장소·comm dir을 정한다. 선택한 comm dir에 이 패키지의 `RULES.md`가 없으면 사용자에게
   `"<pkg>/bin/collab-init" "<workspace>" --comm-dir "<comm dir>"`로 적용할지 선택 UI로 묻는다. 직접 설치하지 않는다.
3. `<pkg>/templates/request.md`로 `<comm dir>/<YYYYMMDD>-<주제>/01-codex-request.md`를 쓴다. 승인된 작업 대상·변경 내용·검증·완료 기준을 그대로 반영한다.
   - 헤더: `Codex thread: <셸의 $CODEX_THREAD_ID 값>`.
   - 끝: `codex queue --thread "<그 값>" --message "Claude 회신 완료: <회신 파일 절대경로>"`.
4. 전송 전에 요청서의 각 항목을 승인 내용과 대조한다. 추가가 필요한 사항은 사용자 결정을 받은 뒤 반영한다.
   이미 완료된 조사·검증 결과는 승계한다.
5. 전용 worker를 띄우고 요청 한 줄을 보낸다. 환경은 사용자가 쓰는 쪽(`orca` 또는 `tmux`)이다.
   helper는 전용 worker에 기본 권한 우회 flag를 붙인다(RULES.md 4절). 그 밖의 인자는 사용자가 지정한 경우에만 `--` 뒤에 전달한다.

   ```bash
   "<pkg>/bin/collab-worker" start --env orca --worker claude \
     --workspace "<workspace>" --request "<요청서 절대경로>" --title "<작업명>"
   # tmux에서는 --env tmux
   ```

   출력의 `handle`을 기록하고, 진행 상태를 알린 뒤 턴을 마친다. 전송 접수를 작업 완료로 보고하지 않는다.
6. `Claude 회신 완료: <경로>` queue 메시지로 시작된 턴에서 그 회신 파일을 읽고 RULES.md 7절대로 독립 검증한다.
   worker의 지적·의견·질문·확인 요청은 사용자에게 선택 UI로 전달하고 결정받은 내용을 반영한다.
   수정이 필요하면 다음 연번 요청을 같은 worker에 보낸다
   (`"<pkg>/bin/collab-worker" send --env <env> --handle <handle> --request "<다음 요청서>"`).
7. PASS 후 이번 작업 파일만 commit하고, 정리가 끝나면 `"<pkg>/bin/collab-worker" close --env <env> --handle <handle>`로 worker를 닫는다.
