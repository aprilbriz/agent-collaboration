#!/usr/bin/env python3
"""실제 CLI 최소 왕복: host 세션 ← (요청 한 줄) worker 세션 → 회신 파일 + 완료 알림 → host 수신.

  python3 tests/e2e_roundtrip.py --env orca|tmux --host codex|claude [--followup] [--keep]

- 이 저장소의 `.e2e/<run>/` 임시 workspace(collab-init 적용)에서만 실행한다. 실제 모델 호출이 발생한다.
- host 세션
  - claude: `bin/claude-host`로 시작하고, host가 Bash로 자기 CLAUDE_CODE_MESSAGING_SOCKET을 파일에 적는다.
  - codex: `codex exec`로 thread를 만들고(thread id 확보) 같은 thread를 `codex resume <id>`로 대화형 host로 띄운다.
- worker는 `bin/collab-worker start`로 띄우고 요청 한 줄만 보낸다(helper의 전용 worker 기본 인자 그대로).
- 판정: 회신 파일 생성 + `e2e-result.txt` 내용이 정확히 `ok` 한 줄 + host 화면에 `HOST-RECEIVED <회신 경로>`가 보이면 0.
  `--followup`이면 같은 worker에 `collab-worker send`로 03 요청을 보내 04 회신·`ok2`·host 수신까지 같은 기준으로 본다.
  결과 내용이 다르면 1, 회신은 있으나 host 수신이 보이지 않으면 두 화면을 출력하고 3으로 끝낸다.
  알림을 다른 프로세스에서 대신 보내지 않는다.
- 끝나면 자신이 만든 terminal/session만 닫고 임시 workspace를 지운다(--keep이면 보존).
  codex exec가 만든 host thread id는 출력만 하고 지우지 않는다.
"""
import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"


def log(msg):
    print(f"[e2e {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd, **kw):
    res = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if res.returncode != 0:
        raise RuntimeError(f"{shlex.join(map(str, cmd))} -> {res.returncode}\n{res.stdout}\n{res.stderr}")
    return res.stdout


class Term:
    """이 스크립트가 만든 terminal 하나(Orca handle 또는 tmux session)."""

    def __init__(self, env, title, ws, command):
        self.env = env
        if env == "orca":
            out = json.loads(run(["orca", "terminal", "create", "--title", title,
                                  "--command", f"cd {shlex.quote(str(ws))} && {command}", "--json"]))
            self.handle = find_handle(out)
            if not self.handle:
                raise RuntimeError(f"orca handle 없음: {out}")
        else:
            # collab-worker read/close가 다룰 수 있게 collab- session의 pane id를 handle로 쓴다.
            self.handle = run(["tmux", "new-session", "-d", "-P", "-F", "#{pane_id}", "-s", "collab-" + title,
                               "-c", ws, "-x", "220", "-y", "50"]).strip()
            run(["tmux", "send-keys", "-t", self.handle, "-l", f"cd {shlex.quote(str(ws))} && {command}"])
            time.sleep(0.5)
            run(["tmux", "send-keys", "-t", self.handle, "Enter"])

    def screen(self):
        try:
            return run([BIN / "collab-worker", "read", "--env", self.env, "--handle", self.handle, "--lines", "200"])
        except RuntimeError as e:
            return f"<read 실패: {e}>"

    def close(self):
        try:
            run([BIN / "collab-worker", "close", "--env", self.env, "--handle", self.handle])
            log(f"닫음: {self.handle}")
        except RuntimeError as e:
            log(f"닫기 실패: {e}")


def find_handle(data):
    if isinstance(data, dict):
        if isinstance(data.get("handle"), str):
            return data["handle"]
        data = list(data.values())
    if isinstance(data, list):
        for v in data:
            h = find_handle(v)
            if h:
                return h
    return None


def wait(pred, timeout, every=3):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(every)
    return None


HOST_RULE = ("이후 '회신 완료:'가 포함된 메시지를 받으면 도구를 쓰지 말고 "
             "'HOST-RECEIVED <메시지에 있는 파일 절대경로>' 한 줄만 답하세요.")


def write_request(req, reply, worker, header, notify, ws, result_name, content):
    req.write_text(f"""Status: OPEN
Next actor: {worker}
Expected next output: {reply.name}
{header}
In reply to: [사용자 원문]
Verdict: N/A
Approved plan: {req.name}

작업의 성격과 복잡도에 따라 필요한 경우, 적절한 모델과 추론 수준의 서브 에이전트를
할당해 주세요. 독립적으로 진행할 수 있는 조사·구현·검증에 적극 활용해 주세요.

## 사용자 원문과 선택

> 협업 패키지 E2E 연결 확인

## 경로

- workspace: {ws}
- 대상 저장소: {ws}
- comm dir: {ws / 'docs' / '.comm'}

## 결과물과 범위

연결 확인용 최소 작업입니다. 단순하므로 서브 에이전트 없이 직접 처리해도 됩니다.
workspace에 `{result_name}` 파일을 만들고 내용으로 `{content}` 한 줄만 쓰세요. 다른 파일은 바꾸지 않습니다.

## 실행 경계

이 workspace 밖은 변경하지 않습니다. commit하지 않습니다.

완료 후 변경 파일과 확인 결과를 `{reply}`에 작성해 고정하고 아래 명령으로 알려 주세요.

`{notify}`
""")
    log(f"요청서: {req}")


def check_round(ws, reply, result_name, content, host, wterm, timeout):
    """회신 파일·결과 내용·host 수신을 확인해 종료 코드(0/1/3)를 돌려준다."""
    if not wait(reply.exists, timeout, every=5):
        raise RuntimeError("회신 파일 없음(시간 초과). worker 화면:\n" + wterm.screen())
    log(f"회신 파일 생성: {reply}")
    result = ws / result_name
    got_content = result.read_text() if result.exists() else None
    result_ok = got_content in (content, content + "\n")
    log(f"작업 결과 {result_name}: {got_content!r} -> {'일치' if result_ok else '불일치'}")
    pat = re.compile(r"HOST-RECEIVED\s+\S*" + re.escape(reply.name))
    got = wait(lambda: pat.search(re.sub(r"\s*\n\s*", "", host.screen())), 300, every=5)
    if not got:
        log("host 수신이 화면에 보이지 않음.\n--- worker 화면 ---\n" + wterm.screen()
            + "\n--- host 화면 ---\n" + host.screen())
        return 3
    log(f"host 수신 확인: {got.group(0)}")
    return 0 if result_ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=["orca", "tmux"], required=True)
    ap.add_argument("--host", choices=["codex", "claude"], required=True)
    ap.add_argument("--followup", action="store_true",
                    help="첫 왕복 뒤 collab-worker send로 두 번째 요청(03→04)을 같은 worker에 보낸다. "
                         "tmux에서는 그 전에 worker session에 다른 창을 열어 활성화한다")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--timeout", type=int, default=900, help="worker 회신 대기 초")
    a = ap.parse_args()
    worker = "claude" if a.host == "codex" else "codex"
    run_id = f"{a.env}-{a.host}-{time.strftime('%H%M%S')}"
    ws = ROOT / ".e2e" / run_id
    ws.mkdir(parents=True)
    run([BIN / "collab-init", ws])
    thread = ws / "docs" / ".comm" / "20261110-e2e"
    thread.mkdir()
    terms, code = [], 1
    try:
        if a.host == "claude":
            sock_file = ws / "host-socket.txt"
            prompt = ("E2E 연결 확인용 host 세션입니다. 지금 Bash로 "
                      "`printf %s \"$CLAUDE_CODE_MESSAGING_SOCKET\" > host-socket.txt` 하나만 실행하고 "
                      "'HOST-READY'라고만 답하세요. " + HOST_RULE)
            host = Term(a.env, f"e2e-host-{run_id}", ws, shlex.join([str(BIN / "claude-host"), prompt]))
            terms.append(host)
            sock = wait(lambda: sock_file.exists() and sock_file.read_text().strip(), 180)
            if not sock:
                raise RuntimeError("host socket 경로를 얻지 못함\n" + host.screen())
            header = f"Claude socket: {sock}"
            notify_for = lambda r: shlex.join([str(BIN / "notify-claude"), "--socket", sock,
                                               "--message", f"Codex 회신 완료: {r}"])
        else:
            out = run(["codex", "exec", "--json", "-C", ws,
                       "E2E 연결 확인용 host 세션입니다. 'READY'라고만 답하세요. " + HOST_RULE])
            tid = next(json.loads(l)["thread_id"] for l in out.splitlines()
                       if l.startswith("{") and json.loads(l).get("type") == "thread.started")
            log(f"codex host thread: {tid}")
            host = Term(a.env, f"e2e-host-{run_id}", ws, shlex.join(["codex", "resume", tid]))
            terms.append(host)
            time.sleep(15)
            header = f"Codex thread: {tid}"
            notify_for = lambda r: shlex.join(["codex", "queue", "--thread", tid,
                                               "--message", f"Claude 회신 완료: {r}"])
        log(f"host 준비: {host.handle} ({header})")

        req = thread / f"01-{a.host}-request.md"
        reply = thread / f"02-{worker}-implementation.md"
        write_request(req, reply, worker, header, notify_for(reply), ws, "e2e-result.txt", "ok")
        started = json.loads(run([BIN / "collab-worker", "start", "--env", a.env, "--worker", worker,
                                  "--workspace", ws, "--request", req, "--title", f"e2e-worker-{run_id}"]))
        wterm = Term.__new__(Term)
        wterm.env, wterm.handle = a.env, started["handle"]
        terms.append(wterm)
        log(f"worker 시작: {wterm.handle} send={json.dumps(started.get('send_result'), ensure_ascii=False)[:300]}")
        code = check_round(ws, reply, "e2e-result.txt", "ok", host, wterm, a.timeout)

        if code == 0 and a.followup:
            time.sleep(10)  # worker가 첫 턴을 마칠 시간
            if a.env == "tmux":
                session = run(["tmux", "display-message", "-p", "-t", wterm.handle, "#{session_name}"]).strip()
                run(["tmux", "new-window", "-t", f"={session}:", "-n", "user-other"])
                active = run(["tmux", "display-message", "-p", "-t", f"={session}:", "#{pane_id}"]).strip()
                log(f"worker session 활성 pane: {active} (worker handle {wterm.handle})")
            req2 = thread / f"03-{a.host}-request.md"
            reply2 = thread / f"04-{worker}-implementation.md"
            write_request(req2, reply2, worker, header, notify_for(reply2), ws, "e2e-result-2.txt", "ok2")
            sent = run([BIN / "collab-worker", "send", "--env", a.env, "--handle", wterm.handle, "--request", req2])
            log(f"후속 요청 전송: {' '.join(sent.split())[:300]}")
            code = check_round(ws, reply2, "e2e-result-2.txt", "ok2", host, wterm, a.timeout)
    finally:
        for t in reversed(terms):
            t.close()
        if a.keep:
            log(f"보존: {ws}")
        else:
            shutil.rmtree(ws, ignore_errors=True)
            log(f"삭제: {ws}")
        log(f"결과 코드 {code}")
    sys.exit(code)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        log(f"중단: {e}")
        sys.exit(1)
