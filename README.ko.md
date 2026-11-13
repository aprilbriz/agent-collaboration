# agent-collaboration

[English](README.md) | **한국어**

Codex와 Claude Code 중 하나를 **host**(계획·검증), 다른 하나를 **worker**(전용 terminal에서 작업)로 쓰는 협업 plugin입니다. worker terminal은 Orca 또는 tmux로 띄웁니다.

```text
사용자와 계획 합의 → 요청서 → 전용 worker 실행
→ 회신 + 완료 알림 → host 독립 검증 → (수정 왕복) → PASS 후 지정된 주체가 commit
```

## 설치

host로 쓸 CLI에 plugin만 설치하면 됩니다. worker 쪽 CLI에는 설치하지 않아도 됩니다. 그 밖에 실행할 명령, PATH 설정, 프로젝트별 준비는 없습니다.

```bash
# Claude Code를 host로 쓸 때
claude plugin marketplace add aprilbriz/agent-collaboration
claude plugin install agent-collaboration@agent-collaboration

# Codex를 host로 쓸 때
codex plugin marketplace add aprilbriz/agent-collaboration
codex plugin add agent-collaboration@agent-collaboration
```

설치한 뒤 CLI를 새로 시작하면 다음 skill이 보입니다.

| skill | 역할 |
|---|---|
| `plan-with-me` | 선택 UI로 질문하며 사용자와 계획을 합의 |
| `collaborate` | host 진입점: 요청서 작성, worker 실행, 완료 대기, 검증, 종료 |

## 사용

host 세션에서 하고 싶은 일을 말하고 협업을 요청합니다. 예: "collaborate로 Codex worker(tmux)와 이 버그를 고쳐줘". 이후 과정은 host가 skill에 따라 진행합니다.

- **계획:** `plan-with-me`로 범위·완료 기준·commit 주체를 합의합니다.
- **thread:** host가 `<workspace>/.comm/<날짜>-<주제>/`를 만듭니다. 이 폴더는 그 git 저장소의 `.git/info/exclude`에만 추가하고, 추적 파일은 바꾸지 않습니다.
- **worker 실행:** host가 Orca 탭 또는 `collab-` tmux session에 상대 CLI를 띄우고 요청 한 줄을 보냅니다. tmux라면 host가 알려 주는 `tmux attach` 명령으로 worker 화면을 볼 수 있습니다.
- **완료 알림:**
  - **Claude Code host:** host가 완료 대기를 background로 걸어 두고, worker가 회신 뒤 완료 표시 파일(`<회신>.done`)을 만들면 자동으로 이어갑니다. 수신 설정이나 별도 launcher는 필요 없습니다. host가 대기를 빠뜨렸다면 "wait 다시 걸어"라고 말하면 됩니다. 이미 끝난 회신이면 바로 이어갑니다.
  - **Codex host:** worker가 `codex queue`로 host thread에 알립니다.
- **검증·마무리:** host가 회신과 실제 변경을 독립 검증합니다. 문제가 있으면 같은 worker에 수정을 요청하고, PASS 뒤에는 요청서의 commit 주체에 따라 마무리합니다. 마지막에 worker terminal을 닫습니다.

### 여러 작업 동시에 하기

여러 작업을 한 번에 요청하거나 진행 중에 새 작업을 요청하면, host가 작업마다 thread와 worker를 따로 띄워 병렬로 진행합니다. 회신은 도착하는 순서대로 하나씩 검증합니다.

작업 공간은 계획 단계에서 host가 작업마다 제안하고, 사용자가 승인합니다.

| 작업 공간 | 언제 | 마무리 |
|---|---|---|
| 같은 트리 | 다른 작업과 수정 파일이 겹치지 않을 때. 요청서에 수정 가능 범위를 적고, 범위 밖은 worker가 멈춰 질문 | 작업별로 해당 파일만 commit |
| worktree | 파일이 겹치거나, 공통 파일(lock·설정)을 건드리거나, 테스트끼리 간섭할 수 있을 때. 저장소 옆 `<저장소>.worktrees/`에 작업 branch(`collab/<thread>`)를 만듦 | worktree에서 commit한 뒤 원래 branch 위로 rebase하고 fast-forward로 합침. 충돌하면 그대로 두고 사용자에게 보고 |

worktree에는 원래 작업 트리의 commit하지 않은 변경과 무시된 파일(`node_modules` 등)이 없습니다. 필요한 준비 명령은 host가 요청서에 적습니다.

절차 정본은 [skills/collaborate/RULES.md](skills/collaborate/RULES.md)이고, 요청·회신 양식은 [skills/collaborate/templates/](skills/collaborate/templates/)에 있습니다. thread를 만들 때 이 둘을 `<thread>/rules/`에 복사하고, worker는 요청 한 줄에 적힌 그 사본을 읽습니다. 그래서 작업 중에 plugin을 업데이트해도 진행 중인 작업은 처음 규칙대로 끝납니다.

## 실행 전제

- 확인 환경: macOS(Darwin 27.2), Orca 1.4.255, Codex CLI 0.167.1, Claude Code 2.1.322, tmux 3.7c, Python 3.14.
- `python3`가 있어야 합니다. helper는 표준 라이브러리만 씁니다.
- worker 환경에 맞춰 `orca` 또는 `tmux`가 필요합니다.
- `codex`와 `claude` 모두 로그인과 workspace trust를 마쳐 두어야 합니다.
- 전용 worker는 권한 우회 flag로 실행됩니다(Claude `--dangerously-skip-permissions`, Codex `--dangerously-bypass-approvals-and-sandbox`).
- Linux는 확인하지 않았습니다. 네이티브 Windows는 지원하지 않습니다.

## 개발

```bash
claude --plugin-dir "$PWD"                                  # 설치 없이 이 checkout을 plugin으로 불러오기
claude plugin validate .                                    # manifest 검사
python3 -m unittest discover -s tests -v                    # stub·mock 격리 회귀
python3 tests/e2e_roundtrip.py --env tmux --host claude --followup  # 실제 모델 호출
```

로컬 경로를 marketplace로 추가해 설치하면 작업 폴더 전체(무시된 `.comm` 포함)가 cache로 복사되므로, 개발 중에는 `--plugin-dir`를 씁니다.

E2E는 `.e2e/` 안에서 실제 host·worker를 띄워 결과 파일·회신·host 수신을 확인합니다. Claude host는 이 checkout을 `--plugin-dir`로 불러옵니다. 실패하면 workspace와 진단을 보존하고, 성공했을 때만 정리합니다(`--keep`이면 성공해도 보존).

plugin을 갱신해 배포할 때는 `.claude-plugin/plugin.json`과 `.codex-plugin/plugin.json`의 `version`을 함께 올립니다.
