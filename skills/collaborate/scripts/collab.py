#!/usr/bin/env python3
"""협업 thread를 만들고 전용 worker를 시작·대조·대기·종료한다(collaborate skill helper).

host agent가 `python3 <skill>/scripts/collab.py <command>`로 실행한다. 사용자가 직접 실행할 필요는 없다.
worker 입력은 항상 "<요청서 절대경로> — <RULES.md 절대경로> 준수" 한 줄이다.
전송 접수·turn 시작 관찰은 완료 판정이 아니다. helper는 자동 재전송·종료하지 않는다.
"""
import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
RULES = SKILL_DIR / "RULES.md"
TEMPLATES = SKILL_DIR / "templates"
TMUX_PREFIX = "collab-"
DONE_SUFFIX = ".done"
RECORD_NAME = "worker-record.json"
HISTORY_DIR = "worker-record-history"
WORKER_DEFAULT_ARGS = {
    "claude": ["--dangerously-skip-permissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
}


class WorkerError(Exception):
    def __init__(self, message, code=2, data=None):
        super().__init__(message)
        self.code = code
        self.data = data


def fail(message, code=2, data=None):
    raise WorkerError(message, code, data)


def request_line(request):
    return f"{request} — {RULES} 준수"


def self_command(*args):
    return shlex.join(["python3", str(Path(__file__).resolve()), *args])


def check_thread(thread):
    p = Path(thread)
    if not p.is_absolute() or not p.is_dir():
        fail(f"thread는 존재하는 절대경로 폴더여야 합니다: {thread}")
    return p.resolve()


def check_request(request):
    p = Path(request)
    if not p.is_absolute() or not p.is_file():
        fail(f"요청서는 존재하는 절대경로여야 합니다: {request}")
    if "\n" in str(p) or "\r" in str(p):
        fail("요청서 경로에는 줄바꿈을 쓸 수 없습니다")
    p = p.resolve()
    check_thread(p.parent)
    return str(p)


def run(cmd, dry=False):
    if dry:
        print("+ " + shlex.join(cmd))
        return None
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        data = None
        try:
            data = json.loads(result.stdout)
        except (ValueError, TypeError):
            pass
        fail(f"명령 실패({result.returncode}): {shlex.join(cmd)}\n"
             f"{(result.stderr or result.stdout).strip()}", 1, data)
    return result.stdout


def orca_json(cmd, dry=False):
    out = run(cmd, dry)
    if out is None:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        fail(f"orca JSON 해석 실패: {out[:500]}", 1)
    if not isinstance(data, dict):
        fail("orca 응답이 JSON object가 아닙니다", 1, data)
    if data.get("ok") is False:
        fail(f"orca 오류: {json.dumps(data.get('error'), ensure_ascii=False)}", 1, data)
    return data


def walk(data):
    yield data
    if isinstance(data, dict):
        for value in data.values():
            yield from walk(value)
    elif isinstance(data, list):
        for value in data:
            yield from walk(value)


def find_key(data, key):
    return next((part[key] for part in walk(data)
                 if isinstance(part, dict) and isinstance(part.get(key), str)), None)


def record_path(thread):
    return Path(thread) / RECORD_NAME


def save_record(record):
    path = record_path(record["thread"])
    if path.is_symlink():
        fail(f"worker 기록은 symlink일 수 없습니다: {path}")
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".worker-record-", delete=False) as stream:
            temp = Path(stream.name)
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def new_record(thread, env, handle, worker, workspace, request):
    return {"version": 1, "thread": str(Path(thread).resolve()), "env": env,
            "handle": handle, "worker": worker, "workspace": str(Path(workspace).resolve()),
            "request": request, "identity": None, "stage": "created", "status": "open",
            "transmission": {"attempted": False, "input_accepted": False, "turn_started": False}}


def read_saved_record(thread, env=None, handle=None):
    """소유권만 검사한다. 종료 확인 복구에는 live identity가 필요하지 않다."""
    thread = Path(thread).resolve()
    path = record_path(thread)
    if path.is_symlink():
        fail(f"worker 기록은 symlink일 수 없습니다: {path}")
    try:
        raw = path.read_bytes()
        record = json.loads(raw)
    except (OSError, ValueError) as error:
        fail(f"worker 생성 기록을 읽을 수 없습니다: {path}: {error}")
    if (not isinstance(record, dict) or record.get("version") != 1
            or record.get("thread") != str(thread)
            or record.get("env") not in {"orca", "tmux"}
            or not isinstance(record.get("handle"), str) or not record["handle"]
            or env is not None and record.get("env") != env
            or handle is not None and record.get("handle") != handle):
        fail("thread·환경·handle이 worker 생성 기록과 일치하지 않습니다")
    return record, raw


def recovery_hint(thread, env, handle):
    command = self_command("mark-closed", "--env", env, "--handle", handle, "--thread", str(thread),
                           "--closed-by-user", "--reason", "<직접 종료를 확인한 사유>")
    return ("원래 worker의 종료를 직접 확인하세요. 남아 있으면 Orca/tmux에서 직접 닫은 뒤 "
            "기록만 정리하세요(terminal을 자동 조작하지 않음): " + command)


def load_record(thread, env, handle):
    record, _ = read_saved_record(thread, env, handle)
    if record.get("status") == "closed":
        fail("이미 닫힌 worker 기록입니다")
    if not isinstance(record.get("identity"), dict) or not record["identity"]:
        fail("worker 실행 인스턴스 기록이 없어 안전하게 확인할 수 없습니다\n"
             + recovery_hint(thread, env, handle))
    return record


def archive_record(thread, raw):
    """원본 bytes를 exclusive 파일에 보존한다. history의 symlink를 따라가지 않는다."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    thread_fd = os.open(thread, directory_flags)
    history_fd = None
    try:
        try:
            os.mkdir(HISTORY_DIR, mode=0o700, dir_fd=thread_fd)
        except FileExistsError:
            pass
        history_fd = os.open(HISTORY_DIR, directory_flags, dir_fd=thread_fd)
        for _ in range(16):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            name = f"{stamp}-{secrets.token_hex(6)}.json"
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=history_fd)
            except FileExistsError:
                continue
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            return str(Path(thread) / HISTORY_DIR / name)
        fail("worker 기록 이력 파일명이 충돌했습니다. 기존 이력은 보존했습니다", 1)
    finally:
        if history_fd is not None:
            os.close(history_fd)
        os.close(thread_fd)


def orca_identity(data):
    terminal = (data.get("result") or {}).get("terminal") or {}
    identity = {key: terminal.get(key) for key in
                ("ptyId", "incarnationId", "executionHostId", "worktreeId", "worktreePath")}
    identity["runtimeId"] = (data.get("_meta") or {}).get("runtimeId")
    if any(not isinstance(value, str) or not value for value in identity.values()):
        fail("Orca의 실행 인스턴스 식별 정보가 부족합니다. 원본 metadata를 확인하세요", 1, data)
    return identity


def reject_self(env, handle, identity=None):
    if env == "orca":
        own_handle = os.environ.get("ORCA_TERMINAL_HANDLE")
        if own_handle and handle == own_handle:
            fail("호출자 자신의 Orca terminal은 worker로 조작하지 않습니다")
        if identity:
            for name in ("ORCA_PTY_ID", "ORCA_TERMINAL_PTY_ID"):
                if os.environ.get(name) == identity.get("ptyId"):
                    fail("호출자 자신의 Orca PTY는 worker로 조작하지 않습니다")
    elif os.environ.get("TMUX_PANE") == handle and identity:
        caller = os.environ.get("TMUX", "").rsplit(",", 2)
        if len(caller) == 3 and caller[:2] == [identity.get("socketPath"), identity.get("serverPid")]:
            fail("호출자 자신의 tmux pane은 worker로 조작하지 않습니다")


def tmux_identity(handle):
    if not re.fullmatch(r"%\d+", handle):
        fail(f"tmux handle은 pane id(%N)여야 합니다: {handle}")
    fmt = "#{session_id}\t#{session_name}\t#{session_created}\t#{pane_pid}\t#{pid}\t#{socket_path}"
    parts = run(["tmux", "display-message", "-p", "-t", handle, fmt]).strip().split("\t")
    if len(parts) != 6 or not parts[1].startswith(TMUX_PREFIX):
        fail(f"전용 collab- tmux session의 pane이 아닙니다: {handle}")
    identity = dict(zip(("sessionId", "session", "sessionCreated", "panePid", "serverPid", "socketPath"), parts))
    identity["serverStarted"] = run(["ps", "-o", "lstart=", "-p", identity["serverPid"]]).strip()
    if not identity["serverStarted"]:
        fail("tmux server 실행 인스턴스를 확인할 수 없습니다", 1)
    return identity


def verify_worker(thread, env, handle, dry=False):
    record = load_record(thread, env, handle)
    reject_self(env, handle, record["identity"])
    try:
        if env == "orca":
            command = ["orca", "terminal", "show", "--terminal", handle, "--json"]
            data = orca_json(command, dry)
            if dry:
                return record
            if (data.get("result") or {}).get("terminal", {}).get("handle") != handle:
                fail("Orca terminal handle이 조회 결과와 일치하지 않습니다")
            current = orca_identity(data)
        else:
            if dry:
                return record
            current = tmux_identity(handle)
        if current != record["identity"]:
            fail("worker 실행 인스턴스가 바뀌었습니다. 기록과 다른 terminal/process는 조작하지 않습니다")
    except WorkerError as error:
        fail(str(error) + "\n" + recovery_hint(thread, env, handle), error.code, error.data)
    except OSError as error:
        fail(str(error) + "\n" + recovery_hint(thread, env, handle), 1)
    return record


def receipt_summary(data, handle, line, seconds=10):
    dictionaries = [part for part in walk(data) if isinstance(part, dict)]
    stages = {stage for part in dictionaries if isinstance(part.get("stages"), list)
              for stage in part["stages"] if isinstance(stage, str)}
    def observed(keys, names):
        values = [part[key] for part in dictionaries for key in keys if isinstance(part.get(key), bool)]
        if True in values or stages.intersection(names):
            return True
        if False in values or stages:
            return False
        return None
    accepted = observed(("accepted", "inputAccepted", "input_accepted"), {"input_accepted"})
    started = observed(("turnStarted", "turn_started", "turnStartObserved", "turn_start_observed"),
                       {"turn_started", "turn_start_observed"})
    retry = next((part[key] for part in dictionaries for key in ("retryRequestId", "requestId", "request_id")
                  if isinstance(part.get(key), str) and part[key]), None)
    warning_retry = None
    warnings = [part[key] for part in dictionaries for key in ("warning", "warnings") if key in part]
    for warning in walk(warnings):
        if isinstance(warning, str):
            match = re.search(r"--retry-request\s+([A-Za-z0-9_-]+)", warning)
            if match:
                warning_retry = match.group(1)
                break
    retry = retry or warning_retry
    observation = find_key(data, "observation")
    provider = find_key(data, "provider")
    unsafe = observation in {"incarnation_replaced", "unsupported"} or provider in {"old-host", "unsupported"}
    safe_retry = accepted is not False and not unsafe and (
        observation == "supported" or warning_retry == retry and retry is not None)
    command = None
    if started is not True and retry and safe_retry:
        command = shlex.join(["orca", "terminal", "send", "--terminal", handle, "--text", line,
                              "--enter", "--retry-request", retry, "--wait-submit", str(seconds), "--json"])
    return {"input_accepted": accepted, "turn_started": started, "confirmation_command": command,
            "note": ("turn 시작 관찰은 완료가 아닙니다" if started else
                     "turn 시작 미관찰/미확인. 자동 재전송하지 않았습니다. retry ID 확인 명령만 수동 실행하세요"
                     if command else "turn 시작 미관찰/미확인. 안전한 retry 안내 또는 request ID가 없어 확인 명령을 만들지 않았습니다. 원본 receipt를 확인하고 재전송하지 마세요")}


def send_orca(handle, line, dry=False):
    return orca_json(["orca", "terminal", "send", "--terminal", handle, "--text", line,
                      "--enter", "--wait-submit", "10", "--json"], dry)


def tmux_type(target, text, dry=False):
    run(["tmux", "send-keys", "-t", target, "-l", text], dry)
    if not dry:
        time.sleep(0.5)
    run(["tmux", "send-keys", "-t", target, "Enter"], dry)


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def update_sent(record, sent, line):
    summary = receipt_summary(sent, record["handle"], line) if record["env"] == "orca" else {
        "input_accepted": None, "turn_started": None, "confirmation_command": None,
        "note": "tmux 입력을 보냈습니다. turn 시작·완료는 관찰하지 않습니다"}
    record.update(stage="sent", status="open", send_result=sent, send_summary=summary)
    record["transmission"] = {"attempted": True, "input_accepted": summary["input_accepted"],
                              "turn_started": summary["turn_started"]}
    save_record(record)
    return summary


def mark_sending(record):
    record["stage"] = "sending"
    record["transmission"] = {"attempted": True, "input_accepted": None, "turn_started": None}
    save_record(record)


def cmd_start(a):
    ws = Path(a.workspace)
    if not ws.is_absolute() or not ws.is_dir():
        fail(f"workspace는 존재하는 절대경로여야 합니다: {a.workspace}")
    ws = ws.resolve()
    request = check_request(a.request)
    thread = Path(request).parent
    previous_raw = None
    if record_path(thread).exists() or record_path(thread).is_symlink():
        previous, previous_raw = read_saved_record(thread)
        if previous.get("status") != "closed":
            close_command = self_command("close", "--env", previous["env"],
                                         "--handle", previous["handle"], "--thread", str(thread))
            fail("이 thread에 아직 닫히지 않은 worker 기록이 이미 있습니다. 먼저 close하세요: "
                 + close_command)
    cli = shutil.which(a.worker)
    if not cli:
        fail(f"worker CLI를 찾을 수 없습니다: {a.worker}")
    extra = WORKER_DEFAULT_ARGS[a.worker] + (a.worker_args[1:] if a.worker_args[:1] == ["--"] else a.worker_args)
    line = request_line(request)
    record = None
    stage = "creating"
    handle = None
    archived_record = None
    try:
        if previous_raw is not None and not a.dry_run:
            stage = "archiving"
            archived_record = archive_record(thread, previous_raw)
        stage = "creating"
        if a.env == "orca":
            command = f"cd {shlex.quote(str(ws))} && {shlex.join([a.worker, *extra])}"
            create = ["orca", "terminal", "create", "--title", a.title, "--command", command, "--json"]
            if a.orca_worktree:
                create[3:3] = ["--worktree", a.orca_worktree]
            created = orca_json(create, a.dry_run)
            handle = find_key(created, "handle") if not a.dry_run else "<handle>"
            if not handle:
                fail("orca 응답에서 terminal handle을 찾지 못했습니다", 1, created)
            stage = "created"
            if not a.dry_run:
                reject_self(a.env, handle)
                record = new_record(thread, a.env, handle, a.worker, ws, request)
                save_record(record)
            stage = "identifying"
            metadata = orca_json(["orca", "terminal", "show", "--terminal", handle, "--json"], a.dry_run)
            if record:
                if (metadata.get("result") or {}).get("terminal", {}).get("handle") != handle:
                    fail("생성한 terminal과 조회된 handle이 다릅니다", 1, metadata)
                record["identity"] = orca_identity(metadata)
                reject_self(a.env, handle, record["identity"])
                record["stage"] = "waiting"
                save_record(record)
            stage = "waiting"
            orca_json(["orca", "terminal", "wait", "--terminal", handle, "--for", "tui-idle",
                       "--timeout-ms", str(a.ready_timeout * 1000), "--json"], a.dry_run)
            stage = "sending"
            if record:
                # wait 동안 terminal이 교체됐을 가능성도 전송 직전에 확인한다.
                verify_worker(thread, a.env, handle)
                mark_sending(record)
            sent = send_orca(handle, line, a.dry_run)
            result = {"env": a.env, "handle": handle, "sent": line, "send_result": sent}
        else:
            slug = lambda text: re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")[:24]
            name = "-".join(part for part in ["collab", slug(ws.name), slug(a.title), secrets.token_hex(3)] if part)
            pane = run(["tmux", "new-session", "-d", "-P", "-F", "#{pane_id}", "-s", name,
                        "-c", str(ws), "-x", "220", "-y", "50"], a.dry_run)
            handle = pane.strip() if pane else "<pane>"
            stage = "created"
            if not a.dry_run:
                reject_self(a.env, handle)
                record = new_record(thread, a.env, handle, a.worker, ws, request)
                save_record(record)
                stage = "identifying"
                record["identity"] = tmux_identity(handle)
                reject_self(a.env, handle, record["identity"])
                save_record(record)
                mark_sending(record)
            stage = "sending"
            # exec가 끝나면 pane도 끝나므로 worker 종료 후 남은 shell에 후속 입력을 보내지 않는다.
            tmux_type(handle, f"cd {shlex.quote(str(ws))} && exec {shlex.join([cli, *extra, line])}", a.dry_run)
            sent = None
            result = {"env": a.env, "handle": handle, "session": name, "sent": line,
                      "attach": f"tmux attach -t {shlex.quote('=' + name)}"}
        if record:
            result["send_summary"] = update_sent(record, sent, line)
            result["record"] = str(record_path(thread))
            if archived_record:
                result["archived_record"] = archived_record
            emit(result)
    except (WorkerError, OSError) as error:
        if handle is None and isinstance(error, WorkerError):
            handle = find_key(error.data, "handle")
        if record is None and handle and not a.dry_run:
            record = new_record(thread, a.env, handle, a.worker, ws, request)
        failure = {"ok": False, "env": a.env, "stage": stage, "handle": handle,
                   "transmission": record["transmission"] if record else {
                       "attempted": False, "input_accepted": False, "turn_started": False},
                   "error": str(error), "automatic_cleanup": False, "automatic_resend": False}
        if archived_record:
            failure["archived_record"] = archived_record
        if isinstance(error, WorkerError) and error.data is not None:
            failure["error_result"] = error.data
        if record:
            if not record.get("identity"):
                failure["recovery"] = recovery_hint(thread, a.env, handle)
                record["recovery"] = failure["recovery"]
            record.update(stage=stage, status="failed", error=str(error))
            if isinstance(error, WorkerError) and error.data is not None:
                record["error_result"] = error.data
            try:
                save_record(record)
                failure["record"] = str(record_path(thread))
            except (OSError, WorkerError) as save_error:
                failure["record_error"] = str(save_error)
                try:
                    persisted, _ = read_saved_record(thread, a.env, handle)
                    usable_record = persisted.get("status") != "closed"
                except WorkerError:
                    usable_record = False
                if not usable_record:
                    failure["recovery"] = (
                        "이 worker의 기록을 저장하지 못했습니다. 원래 worker를 Orca/tmux에서 직접 "
                        "확인하고 닫은 뒤 기록 경로의 쓰기 오류를 해결하고 start를 다시 실행하세요")
        emit(failure)
        raise


def cmd_send(a):
    request = check_request(a.request)
    thread = Path(request).parent
    if a.thread and check_thread(a.thread) != thread:
        fail("--thread와 요청서 thread가 다릅니다")
    record = verify_worker(thread, a.env, a.handle, a.dry_run)
    line = request_line(request)
    if not a.dry_run:
        record["request"] = request
        mark_sending(record)
    try:
        if a.env == "orca":
            sent = send_orca(a.handle, line, a.dry_run)
        else:
            tmux_type(a.handle, line, a.dry_run)
            sent = None
        if not a.dry_run:
            summary = update_sent(record, sent, line)
            emit({"env": a.env, "handle": a.handle, "sent": line,
                  "send_result": sent, "send_summary": summary})
    except (WorkerError, OSError) as error:
        if not a.dry_run:
            record.update(status="failed", error=str(error))
            save_record(record)
            emit({"ok": False, "stage": "sending", "handle": a.handle,
                  "transmission": record["transmission"], "error": str(error),
                  "automatic_resend": False})
        raise


def cmd_read(a):
    thread = check_thread(a.thread)
    verify_worker(thread, a.env, a.handle)
    if a.env == "orca":
        data = orca_json(["orca", "terminal", "read", "--terminal", a.handle, "--screen", "--json"])
        terminal = (data or {}).get("result", {}).get("terminal", {})
        print("\n".join((terminal.get("tail") or [])[-a.lines:]))
    else:
        print(run(["tmux", "capture-pane", "-p", "-t", a.handle, "-S", f"-{a.lines}"]), end="")


def cmd_close(a):
    thread = check_thread(a.thread)
    record = verify_worker(thread, a.env, a.handle, a.dry_run)
    if a.env == "orca":
        orca_json(["orca", "terminal", "close", "--terminal", a.handle, "--json"], a.dry_run)
    else:
        run(["tmux", "kill-pane", "-t", a.handle], a.dry_run)
    if not a.dry_run:
        record.update(status="closed", stage="closed")
        save_record(record)
        emit({"env": a.env, "closed": a.handle})


def cmd_mark_closed(a):
    """사용자의 수동 종료 확인만 기록한다. terminal 조회·조작은 하지 않는다."""
    if not a.closed_by_user or not a.reason or not a.reason.strip():
        fail("수동 종료 확인 --closed-by-user와 비어 있지 않은 --reason이 필요합니다")
    thread = check_thread(a.thread)
    record, _ = read_saved_record(thread, a.env, a.handle)
    if record.get("status") == "closed":
        fail("이미 닫힌 worker 기록입니다. 기존 종료 사유를 보존합니다")
    reason = a.reason.strip()
    record.update(status="closed", stage="closed", closure={"method": "user-confirmed", "reason": reason,
                  "confirmed_at": datetime.now(timezone.utc).isoformat()})
    save_record(record)
    emit({"env": a.env, "marked_closed": a.handle, "record": str(record_path(thread)),
          "terminal_modified": False, "reason": reason})


def git_exclude(path):
    """path가 git 저장소 안이고 아직 무시되지 않으면 그 저장소의 .git/info/exclude에 추가한다."""
    try:
        top = subprocess.run(["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True)
    except OSError:
        return None
    if top.returncode:
        return None
    top = Path(top.stdout.strip()).resolve()
    if path == top:
        return None
    rel = path.relative_to(top).as_posix()
    if subprocess.run(["git", "-C", str(top), "check-ignore", "-q", "--no-index", rel]).returncode == 0:
        return None
    exclude = subprocess.run(["git", "-C", str(top), "rev-parse", "--git-path", "info/exclude"],
                             capture_output=True, text=True, check=True).stdout.strip()
    exclude = (top / exclude) if not Path(exclude).is_absolute() else Path(exclude)
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a") as stream:
        stream.write(f"/{rel}\n")
    return str(exclude)


def cmd_thread(a):
    ws = Path(a.workspace)
    if not ws.is_absolute() or not ws.is_dir():
        fail(f"workspace는 존재하는 절대경로여야 합니다: {a.workspace}")
    comm = Path(a.comm_dir) if a.comm_dir else ws.resolve() / ".comm"
    if not comm.is_absolute():
        fail(f"comm dir은 절대경로여야 합니다: {a.comm_dir}")
    topic = re.sub(r"[^\w-]+", "-", a.topic).strip("-")
    if not topic:
        fail(f"thread 주제가 비어 있습니다: {a.topic!r}")
    thread = comm / f"{datetime.now().strftime('%Y%m%d')}-{topic}"
    if thread.exists() and not a.existing:
        fail(f"thread가 이미 있습니다. 이어 쓰려면 --existing을 붙이세요: {thread}")
    thread.mkdir(parents=True, exist_ok=True)
    comm = comm.resolve()
    excluded = git_exclude(comm)
    emit({"thread": str(thread.resolve()), "comm_dir": str(comm), "git_exclude": excluded,
          "rules": str(RULES), "templates": str(TEMPLATES)})


def worker_alive(record):
    try:
        verify_worker(record["thread"], record["env"], record["handle"])
        return True, None
    except (WorkerError, OSError) as error:
        return False, str(error)


def cmd_wait(a):
    """완료 표시(<회신>.done)가 생길 때까지 기다린다. Claude host가 background로 실행한다."""
    thread = check_thread(a.thread)
    reply = Path(a.reply)
    if not reply.is_absolute() or reply.parent.resolve() != thread:
        fail(f"회신 경로는 thread 안의 절대경로여야 합니다: {a.reply}")
    done = reply.parent.resolve() / (reply.name + DONE_SUFFIX)
    started = time.monotonic()
    last_check = started
    failures = 0
    while True:
        if done.exists():
            emit({"status": "done", "reply": str(reply), "reply_exists": reply.exists(),
                  "marker": str(done)})
            return 0
        now = time.monotonic()
        if a.timeout and now - started >= a.timeout:
            emit({"status": "timeout", "reply": str(reply), "reply_exists": reply.exists(),
                  "waited_seconds": int(now - started)})
            return 4
        if a.check_worker and now - last_check >= a.check_every:
            last_check = now
            try:
                record, _ = read_saved_record(thread)
            except WorkerError:
                record = None
            if record and record.get("status") != "closed":
                alive, error = worker_alive(record)
                failures = 0 if alive else failures + 1
                # 순간적인 조회 실패로 깨우지 않도록 연속 2회 실패해야 알린다.
                if failures >= 2:
                    emit({"status": "worker-unavailable", "reply": str(reply),
                          "reply_exists": reply.exists(), "handle": record["handle"], "error": error})
                    return 3
        time.sleep(a.interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("thread", help="comm dir 아래 새 thread 폴더를 만들고 RULES·템플릿 경로를 알려 준다")
    p.add_argument("--workspace", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--comm-dir", help="기본값 <workspace>/.comm")
    p.add_argument("--existing", action="store_true", help="같은 날짜·주제 thread가 있으면 그대로 쓴다")
    p.set_defaults(fn=cmd_thread)
    p = sub.add_parser("wait", help="<회신>.done 완료 표시를 기다린다(Claude host가 background로 실행)")
    p.add_argument("--thread", required=True)
    p.add_argument("--reply", required=True)
    p.add_argument("--interval", type=float, default=5)
    p.add_argument("--timeout", type=float, default=0, help="초, 0이면 무제한")
    p.add_argument("--check-every", type=float, default=60, help="worker terminal 생존 확인 간격(초)")
    p.add_argument("--no-check-worker", dest="check_worker", action="store_false")
    p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("start", help="전용 worker를 만들고 thread에 생성 기록을 남긴다")
    p.add_argument("--env", choices=["orca", "tmux"], required=True)
    p.add_argument("--worker", choices=["claude", "codex"], required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--request", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--orca-worktree")
    p.add_argument("--ready-timeout", type=int, default=90)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("worker_args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_start)
    for name, function in (("send", cmd_send), ("read", cmd_read), ("close", cmd_close)):
        p = sub.add_parser(name)
        p.add_argument("--env", choices=["orca", "tmux"], required=True)
        p.add_argument("--handle", required=True)
        p.add_argument("--thread", required=name != "send", help="worker 생성 기록이 있는 thread 절대경로")
        if name == "send":
            p.add_argument("--request", required=True)
        if name == "read":
            p.add_argument("--lines", type=int, default=80)
        else:
            p.add_argument("--dry-run", action="store_true")
        p.set_defaults(fn=function)
    p = sub.add_parser("mark-closed", help="사용자가 직접 닫은 worker의 기록만 closed로 표시한다")
    p.add_argument("--env", choices=["orca", "tmux"], required=True)
    p.add_argument("--handle", required=True)
    p.add_argument("--thread", required=True)
    p.add_argument("--closed-by-user", action="store_true", required=True,
                   help="사용자가 원래 worker의 종료를 직접 확인했음")
    p.add_argument("--reason", required=True, help="수동 종료를 확인한 사유(필수)")
    p.set_defaults(fn=cmd_mark_closed)
    args = parser.parse_args()
    try:
        return args.fn(args) or 0
    except WorkerError as error:
        print(f"collab: {error}", file=sys.stderr)
        return error.code
    except OSError as error:
        print(f"collab: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
