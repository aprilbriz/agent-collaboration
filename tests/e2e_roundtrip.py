#!/usr/bin/env python3
"""실제 CLI 최소 왕복: host 세션 ← (요청 한 줄) worker 세션 → 회신 파일 + 완료 알림 → host 수신.

  python3 tests/e2e_roundtrip.py --env orca|tmux --host codex|claude [--followup | --parallel] [--keep]

- 이 저장소의 `.e2e/<run>/` 임시 workspace에서만 실행한다. 실제 모델 호출이 발생한다.
- host 세션
  - claude: `claude --plugin-dir <이 저장소>`로 plugin을 불러와 시작한다. host는 회신마다
    `collab.py wait`를 background로 실행하고 턴을 마친다. 완료 표시(`<회신>.done`)로 깨어나야 한다.
  - codex: `codex exec`로 thread를 만들고(thread id 확보) 같은 thread를 `codex resume <id>`로 대화형 host로 띄운다.
- worker는 `collab.py start`로 띄우고 요청 한 줄만 보낸다(helper의 전용 worker 기본 인자 그대로).
- 판정: 회신 파일 생성 + `e2e-result.txt` 내용이 정확히 `ok` 한 줄 + host 화면에 `HOST-RECEIVED <회신 경로>`가 보이면 0.
  `--followup`이면 같은 worker에 `collab-worker send`로 03 요청을 보내 04 회신·`ok2`·host 수신까지 같은 기준으로 본다.
  결과 내용이 다르면 1, 회신은 있으나 host 수신이 보이지 않으면 두 화면을 출력하고 3으로 끝낸다.
  알림을 다른 프로세스에서 대신 보내지 않는다.
- `--parallel`: workspace를 이 저장소의 임시 worktree(branch `e2e/<run>`)로 만들어, 사용자가 이미 신뢰한 저장소와
  같은 조건에서 `collab.py thread`로 두 작업을 연다(같은 트리 1개, `--worktree` 1개).
  두 worker를 동시에 띄워 두 회신·두 host 수신을 확인한 뒤, 스크립트가 host 대신 각 작업을 commit하고
  worktree 작업은 `collab.py integrate`로 합친다. 원래 branch에 두 결과가 일직선으로 들어가고 worktree가 지워져야 0이다.
- 끝나면 자신이 만든 terminal/pane만 닫는다. 성공 때만 임시 workspace를 지우며 실패·--keep이면 진단과 함께 보존한다.
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
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLLAB = [sys.executable, ROOT / "skills/collaborate/scripts/collab.py"]
RUN_LOG = None


def log(msg):
    line = f"[e2e {time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if RUN_LOG is not None:
        with RUN_LOG.open("a") as stream:
            stream.write(line + "\n")


def run(cmd, **kw):
    res = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if RUN_LOG is not None:
        with RUN_LOG.open("a") as stream:
            stream.write(f"$ {shlex.join(map(str, cmd))}\n{res.stdout}\n{res.stderr}\nexit: {res.returncode}\n")
    if res.returncode != 0:
        raise RuntimeError(f"{shlex.join(map(str, cmd))} -> {res.returncode}\n{res.stdout}\n{res.stderr}")
    return res.stdout


def orca_terminal(args):
    data = json.loads(run(["orca", "terminal", *args, "--json"]))
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(f"orca 오류: {data.get('error')}")
    return data


class Term:
    """스크립트가 직접 만든 host와 기록이 있는 helper worker를 구분한다."""

    def __init__(self, env, title, ws, command, registry=None):
        self.env = env
        self.thread = None
        if env == "orca":
            out = orca_terminal(["create", "--title", title,
                                 "--command", f"cd {shlex.quote(str(ws))} && {command}"])
            self.handle = find_handle(out)
            if not self.handle:
                raise RuntimeError(f"orca handle 없음: {out}")
            if registry is not None:
                registry.append(self)
        else:
            self.handle = run(["tmux", "new-session", "-d", "-P", "-F", "#{pane_id}", "-s", "collab-" + title,
                               "-c", ws, "-x", "220", "-y", "50"]).strip()
            if registry is not None:
                registry.append(self)
            run(["tmux", "send-keys", "-t", self.handle, "-l", f"cd {shlex.quote(str(ws))} && {command}"])
            time.sleep(0.5)
            run(["tmux", "send-keys", "-t", self.handle, "Enter"])

    @classmethod
    def worker(cls, env, handle, thread):
        term = cls.__new__(cls)
        term.env, term.handle, term.thread = env, handle, thread
        return term

    def type(self, text):
        """이 스크립트가 직접 만든 host terminal에만 쓴다."""
        if self.thread is not None:
            raise RuntimeError("helper가 기록한 worker에는 직접 입력하지 않습니다")
        if self.env == "orca":
            orca_terminal(["send", "--terminal", self.handle, "--text", text, "--enter"])
        else:
            run(["tmux", "send-keys", "-t", self.handle, "-l", text])
            time.sleep(0.5)
            run(["tmux", "send-keys", "-t", self.handle, "Enter"])

    def screen(self):
        try:
            if self.thread is not None:
                return run([*COLLAB, "read", "--env", self.env, "--handle", self.handle,
                            "--thread", self.thread, "--lines", "200"])
            if self.env == "orca":
                data = orca_terminal(["read", "--terminal", self.handle, "--screen"])
                return "\n".join(data.get("result", {}).get("terminal", {}).get("tail") or [])
            return run(["tmux", "capture-pane", "-p", "-t", self.handle, "-S", "-200"])
        except (RuntimeError, ValueError) as e:
            return f"<read 실패: {e}>"

    def close(self):
        try:
            if self.thread is not None:
                run([*COLLAB, "close", "--env", self.env, "--handle", self.handle, "--thread", self.thread])
            elif self.env == "orca":
                orca_terminal(["close", "--terminal", self.handle])
            else:
                run(["tmux", "kill-pane", "-t", self.handle])
            log(f"닫음: {self.handle}")
            return True
        except (RuntimeError, ValueError) as e:
            log(f"닫기 실패: {e}")
            return False


def finish_run(ws, evidence, terms, code, keep):
    """종료 전에 화면을 남기고, 실패 시에는 진단을 포함한 workspace를 보존한다."""
    for i, term in enumerate(terms):
        try:
            (evidence / f"terminal-{i}.txt").write_text(term.screen())
        except Exception as exc:
            (evidence / f"terminal-{i}.txt").write_text(f"화면 수집 실패: {exc}")
    for term in reversed(terms):
        try:
            closed = term.close()
        except Exception as exc:
            log(f"닫기 실패: {exc}")
            closed = False
        if not closed:
            code = code or 1
    log(f"결과 코드 {code}")
    if keep or code != 0:
        log(f"보존: {ws} (진단: {evidence})")
    else:
        shutil.rmtree(ws)
        # runner.log도 workspace와 함께 삭제되었으므로 stdout에만 기록한다.
        print(f"[e2e {time.strftime('%H:%M:%S')}] 삭제: {ws}", flush=True)
    return code


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


def host_prompt_file(round_no):
    return f"host-prompt-{round_no}.md"


def host_launch_prompt(round_no):
    """터미널 한 줄 입력 한도를 넘지 않도록 회차 지시문은 파일로 전달한다."""
    return f"{host_prompt_file(round_no)} 파일을 읽고 그대로 따르세요."


HOST_LAUNCH_PROMPT = host_launch_prompt(1)


def claude_host_prompt(thread, reply):
    """판정 문자열(`HOST-RECEIVED <회신 경로>`)이 지시문 자체에 나타나지 않게 쓴다.

    지시문이 화면에 보여도 host 수신으로 오인하지 않도록, host는 wait 출력의 reply 값을 붙여 답한다.
    실제 skill처럼 회차마다 host가 직접 wait를 background로 건다.
    """
    command = shlex.join(map(str, [*COLLAB, "wait", "--thread", thread, "--reply", reply]))
    return ("# E2E host 지시\n\nE2E 연결 확인용 host 세션입니다. 다른 작업은 하지 말고 다음만 하세요.\n\n"
            f"1. Bash 도구의 run_in_background로 아래 명령을 실행하고 바로 턴을 마치세요.\n   `{command}`\n"
            "2. 그 작업이 끝났다는 알림을 받으면, 출력 JSON의 status가 done일 때 도구를 더 쓰지 말고\n"
            "   `HOST-RECEIVED` 뒤에 공백 하나와 출력 JSON의 reply 값을 붙인 한 줄만 답하세요.\n")


def claude_host_parallel_prompt(pairs):
    """여러 작업의 wait를 각각 background로 걸고, 끝날 때마다 해당 회신 경로로 수신을 답하게 한다."""
    commands = "\n".join(f"   - `{shlex.join(map(str, [*COLLAB, 'wait', '--thread', t, '--reply', r]))}`"
                         for t, r in pairs)
    return ("# E2E host 지시\n\nE2E 병렬 연결 확인용 host 세션입니다. 다른 작업은 하지 말고 다음만 하세요.\n\n"
            "1. 아래 명령을 각각 별도의 Bash 도구 run_in_background 호출로 실행하고 바로 턴을 마치세요.\n"
            f"{commands}\n"
            "2. 그중 하나가 끝났다는 알림을 받을 때마다, 출력 JSON의 status가 done이면 도구를 더 쓰지 말고\n"
            "   `HOST-RECEIVED` 뒤에 공백 하나와 출력 JSON의 reply 값을 붙인 한 줄만 답하세요.\n")


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
- comm dir: {ws / '.comm'}
- git·테스트 명령 cwd: {ws}
- commit 주체: 없음 (E2E 임시 workspace)

## baseline

파일 생성·내용 확인만 하는 연결 시험이다. 기존 실패 수정이나 다른 테스트 삭제·약화는 승인하지 않는다.

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
    # 병렬 작업은 회신 파일명이 같을 수 있으므로 thread 폴더명까지 맞춘다.
    pat = re.compile(r"HOST-RECEIVED\s+\S*" + re.escape(f"{reply.parent.name}/{reply.name}"))
    got = wait(lambda: pat.search(re.sub(r"\s*\n\s*", "", host.screen())), 300, every=5)
    if not got:
        log("host 수신이 화면에 보이지 않음.\n--- worker 화면 ---\n" + wterm.screen()
            + "\n--- host 화면 ---\n" + host.screen())
        return 3
    log(f"host 수신 확인: {got.group(0)}")
    return 0 if result_ok else 1


def run_parallel(a, ws, worker, terms, start_host):
    """같은 트리 작업과 worktree 작업을 동시에 진행하고, 합치기까지 확인한다."""
    base = run(["git", "-C", ws, "symbolic-ref", "--short", "HEAD"]).strip()
    start = run(["git", "-C", ws, "rev-parse", "HEAD"]).strip()
    tasks = []
    for topic, extra, result_name, content in (("e2e-same", [], "same-result.txt", "same"),
                                                ("e2e-tree", ["--worktree"], "tree-result.txt", "tree")):
        out = json.loads(run([*COLLAB, "thread", "--workspace", ws, "--topic", topic, *extra]))
        thread = Path(out["thread"])
        tasks.append({"thread": thread, "ws": Path(out["worker_workspace"]), "worktree": out.get("worktree"),
                      "reply": thread / f"02-{worker}-implementation.md",
                      "req": thread / f"01-{a.host}-request.md", "result": result_name, "content": content})
    host, header_for, notify_for = start_host([(t["thread"], t["reply"]) for t in tasks])
    for t in tasks:
        write_request(t["req"], t["reply"], worker, header_for(t["reply"]), notify_for(t["reply"]),
                      t["ws"], t["result"], t["content"])
    for t in tasks:
        started = json.loads(run([*COLLAB, "start", "--env", a.env, "--worker", worker, "--workspace", t["ws"],
                                  "--request", t["req"], "--title", f"e2e-{t['thread'].name}"]))
        t["term"] = Term.worker(a.env, started["handle"], t["thread"])
        terms.append(t["term"])
        log(f"worker 시작: {t['term'].handle} ({t['ws']})")
    for t in tasks:
        code = check_round(t["ws"], t["reply"], t["result"], t["content"], host, t["term"], a.timeout)
        if code:
            return code
    for t in tasks:
        if not t["term"].close():
            return 1
        terms.remove(t["term"])
        run(["git", "-C", t["ws"], "add", t["result"]])
        # 공유 저장소 설정을 바꾸지 않도록 commit 작성자는 이 명령에만 지정한다.
        run(["git", "-C", t["ws"], "-c", "user.name=e2e", "-c", "user.email=e2e@example.invalid",
             "commit", "-qm", f"e2e: {t['result']}"])
    integrated = json.loads(run([*COLLAB, "integrate", "--thread", tasks[1]["thread"]]))
    log(f"integrate: {json.dumps(integrated, ensure_ascii=False)[:300]}")
    tree = Path(tasks[1]["worktree"]["path"])
    files = run(["git", "-C", ws, "ls-tree", "--name-only", base]).split()
    merges = run(["git", "-C", ws, "rev-list", "--merges", "--count", f"{start}..{base}"]).strip()
    ok = {"same-result.txt", "tree-result.txt"} <= set(files) and merges == "0" and not tree.exists()
    log(f"main 파일 {files}, merge commit {merges}, worktree 남음 {tree.exists()} -> {'일치' if ok else '불일치'}")
    if ok and tree.parent.is_dir() and not any(tree.parent.iterdir()):
        tree.parent.rmdir()
    return 0 if ok else 1


def main():
    global RUN_LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=["orca", "tmux"], required=True)
    ap.add_argument("--host", choices=["codex", "claude"], required=True)
    ap.add_argument("--followup", action="store_true",
                    help="첫 왕복 뒤 collab-worker send로 두 번째 요청(03→04)을 같은 worker에 보낸다. "
                         "tmux에서는 그 전에 worker session에 다른 창을 열어 활성화한다")
    ap.add_argument("--parallel", action="store_true",
                    help="같은 트리 작업과 worktree 작업 두 개를 동시에 진행하고 integrate까지 확인한다")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--timeout", type=int, default=900, help="worker 회신 대기 초")
    a = ap.parse_args()
    if a.parallel and a.followup:
        ap.error("--parallel과 --followup은 함께 쓰지 않습니다")
    worker = "claude" if a.host == "codex" else "codex"
    run_id = f"{a.env}-{a.host}-{time.strftime('%H%M%S')}"
    ws = ROOT / ".e2e" / run_id
    e2e_branch = f"e2e/{run_id}"
    if a.parallel:
        (ROOT / ".e2e").mkdir(exist_ok=True)
        run(["git", "-C", ROOT, "worktree", "add", "-q", "-b", e2e_branch, ws, "HEAD"])
    else:
        ws.mkdir(parents=True)
    thread = ws / ".comm" / "20261110-e2e"
    evidence = ws / ".comm" / "e2e-evidence" if a.parallel else thread / "02-evidence"
    evidence.mkdir(parents=True)
    RUN_LOG = evidence / "runner.log"
    terms, code = [], 1
    try:
        def start_host(pairs):
            if a.host == "claude":
                if a.parallel:
                    (ws / host_prompt_file(1)).write_text(claude_host_parallel_prompt(pairs))
                else:
                    for round_no, (t, reply_path) in enumerate(pairs, 1):
                        (ws / host_prompt_file(round_no)).write_text(claude_host_prompt(t, reply_path))
                host = Term(a.env, f"e2e-host-{run_id}", ws, shlex.join(
                    ["claude", "--plugin-dir", str(ROOT), "--dangerously-skip-permissions", HOST_LAUNCH_PROMPT]), terms)
                # host가 첫 wait를 background로 띄울 시간. 완료 표시가 먼저 생겨도 wait는 즉시 끝나므로 순서는 판정에 영향이 없다.
                time.sleep(20)
                header_for = lambda r: f"Completion marker: {r}.done"
                notify_for = lambda r: shlex.join(["touch", f"{r}.done"])
            else:
                out = run(["codex", "exec", "--json", "-C", ws,
                           "E2E 연결 확인용 host 세션입니다. 'READY'라고만 답하세요. " + HOST_RULE])
                tid = next(json.loads(l)["thread_id"] for l in out.splitlines()
                           if l.startswith("{") and json.loads(l).get("type") == "thread.started")
                log(f"codex host thread: {tid}")
                host = Term(a.env, f"e2e-host-{run_id}", ws, shlex.join(["codex", "resume", tid]), terms)
                time.sleep(15)
                header_for = lambda r: f"Codex thread: {tid}"
                notify_for = lambda r: shlex.join(["codex", "queue", "--thread", tid,
                                                   "--message", f"Claude 회신 완료: {r}"])
            log(f"host 준비: {host.handle}")
            return host, header_for, notify_for

        if a.parallel:
            code = run_parallel(a, ws, worker, terms, start_host)
        else:
            replies = [thread / f"02-{worker}-implementation.md"]
            if a.followup:
                replies.append(thread / f"04-{worker}-implementation.md")
            host, header_for, notify_for = start_host([(thread, r) for r in replies])

            req = thread / f"01-{a.host}-request.md"
            reply = replies[0]
            write_request(req, reply, worker, header_for(reply), notify_for(reply), ws, "e2e-result.txt", "ok")
            started = json.loads(run([*COLLAB, "start", "--env", a.env, "--worker", worker,
                                      "--workspace", ws, "--request", req, "--title", f"e2e-worker-{run_id}"]))
            wterm = Term.worker(a.env, started["handle"], thread)
            terms.append(wterm)
            log(f"worker 시작: {wterm.handle} send={json.dumps(started.get('send_result'), ensure_ascii=False)[:300]}")
            code = check_round(ws, reply, "e2e-result.txt", "ok", host, wterm, a.timeout)

            if code == 0 and a.followup:
                time.sleep(10)  # worker가 첫 턴을 마칠 시간
                if a.env == "tmux":
                    session = run(["tmux", "display-message", "-p", "-t", wterm.handle, "#{session_name}"]).strip()
                    other = run(["tmux", "new-window", "-t", f"={session}:", "-n", "user-other", "-P", "-F", "#{pane_id}"]).strip()
                    # 이 시험이 만든 보조 pane도 직접 소유한 대상으로만 정리한다.
                    terms.append(Term.worker("tmux", other, None))
                    active = run(["tmux", "display-message", "-p", "-t", f"={session}:", "#{pane_id}"]).strip()
                    log(f"worker session 활성 pane: {active} (worker handle {wterm.handle})")
                if a.host == "claude":
                    host.type(host_launch_prompt(2))  # 스크립트가 만든 host에게만 입력한다
                    time.sleep(15)
                req2 = thread / f"03-{a.host}-request.md"
                reply2 = replies[1]
                write_request(req2, reply2, worker, header_for(reply2), notify_for(reply2), ws, "e2e-result-2.txt", "ok2")
                sent = run([*COLLAB, "send", "--env", a.env, "--handle", wterm.handle,
                            "--thread", thread, "--request", req2])
                log(f"후속 요청 전송: {' '.join(sent.split())[:300]}")
                code = check_round(ws, reply2, "e2e-result-2.txt", "ok2", host, wterm, a.timeout)
    except Exception:
        (evidence / "error.txt").write_text(traceback.format_exc())
        log("중단: " + traceback.format_exc().splitlines()[-1])
        code = 1
    finally:
        try:
            code = finish_run(ws, evidence, terms, code, a.keep)
            if a.parallel:
                if ws.exists():
                    log(f"보존된 e2e worktree·branch: {ws} ({e2e_branch}). 정리: "
                        f"git worktree remove --force {shlex.quote(str(ws))} && git branch -D {e2e_branch}")
                else:
                    subprocess.run(["git", "-C", str(ROOT), "worktree", "prune"], check=True)
                    subprocess.run(["git", "-C", str(ROOT), "branch", "-D", e2e_branch], check=True,
                                   capture_output=True)
                    print(f"[e2e {time.strftime('%H:%M:%S')}] 삭제: {e2e_branch}", flush=True)
        finally:
            RUN_LOG = None
    sys.exit(code)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        log(f"중단: {e}")
        sys.exit(1)
