"""
Controlled deploy endpoint for the Emergent Admin API.

POST /api/emergent-admin/deploy/pull-and-restart
GET  /api/emergent-admin/deploy/status

What this does (and ONLY this):

  1. cd to the repository root (auto-detected via .git walk-up).
  2. `git fetch origin <branch>`   — branch must be in ALLOWED_GIT_BRANCHES.
  3. If working tree is dirty AND stash=false → abort.
     If dirty AND stash=true → `git stash push --include-untracked`.
  4. `git checkout <branch>` if not already on it.
  5. `git pull --ff-only origin <branch>`.
  6. `git diff --name-only <prev_sha> <new_sha>` to list changed files.
  7. `python -m py_compile <each changed *.py>` — syntax-check before serving.
  8. If restart_backend=true → supervisorctl restart backend (best-effort).
  9. Record audit log with before/after SHAs, files changed, exit codes.

Safety:
  • Token auth via X-Admin-Token (handled by Depends).
  • `confirm=true` required for POST (prevents accidental triggers).
  • Branch allowlist (policy.ALLOWED_GIT_BRANCHES) — typos blocked, no
    arbitrary refs.
  • No shell — `asyncio.create_subprocess_exec` with fixed argv.
  • Per-command timeout protection (default 90s).
  • Never runs `git reset --hard`, `git clean -fd`, force push, or any
    write to the remote.
  • Stash is opt-in. Stash branch is auto-named `emergent-admin-stash-<ts>`.
"""
from __future__ import annotations
import asyncio
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import audit_log, require_admin_token
from .policy import ALLOWED_GIT_BRANCHES, ALLOWED_SERVICES

router = APIRouter()

DEFAULT_GIT_TIMEOUT = 90
PY_COMPILE_TIMEOUT  = 30
RESTART_TIMEOUT     = 30


# ── helpers ──────────────────────────────────────────────────────
def _find_repo_root() -> Optional[str]:
    """Walk up from this file until we hit a directory containing `.git`."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists():
            return str(parent)
    return None


async def _run(argv: List[str], *, cwd: str, timeout: int = DEFAULT_GIT_TIMEOUT
                ) -> Dict[str, Any]:
    """Execute argv with fixed args, capture stdout+stderr, enforce timeout.

    2026-05-21 — explicit PATH so git/ssh/supervisorctl can be located even
    when the parent process was launched with a scrubbed venv PATH (which
    is the default for FastAPI services started via supervisor).
    """
    env = dict(os.environ)
    env.setdefault("PATH",
                     "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    # If PATH is set but missing system bins, prepend them.
    if "/usr/bin" not in env["PATH"].split(":"):
        env["PATH"] = ("/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:"
                          "/sbin:/bin:" + env["PATH"])
    # Force batch-mode + identity-file-friendly ssh for git fetch over SSH.
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes "
                                          "-o StrictHostKeyChecking=accept-new")
    started = datetime.now(timezone.utc)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            rc = proc.returncode
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"argv": argv, "exit_code": -1,
                     "output": "(timed out after %ds)" % timeout,
                     "duration_s": timeout, "timed_out": True}
    except FileNotFoundError as e:
        return {"argv": argv, "exit_code": -1,
                 "output": f"binary not found: {e!r}", "duration_s": 0.0,
                 "missing_binary": True}
    out = (stdout or b"").decode("utf-8", "replace")
    return {
        "argv": argv,
        "exit_code": rc,
        "output": out[-8000:],   # cap each step's output at 8 kB
        "duration_s": (datetime.now(timezone.utc) - started).total_seconds(),
    }


async def _git(args: List[str], *, cwd: str,
                timeout: int = DEFAULT_GIT_TIMEOUT) -> Dict[str, Any]:
    git = shutil.which("git") or "/usr/bin/git"
    # 2026-05-21 — when PATH is scrubbed, ssh needs an absolute path; otherwise
    # `git fetch` over an SSH remote fails with "cannot run ssh".
    if "GIT_SSH_COMMAND" not in os.environ:
        for ssh_cand in ("/usr/bin/ssh", "/usr/local/bin/ssh"):
            if os.path.isfile(ssh_cand):
                os.environ["GIT_SSH_COMMAND"] = (
                    f"{ssh_cand} -o BatchMode=yes "
                    "-o StrictHostKeyChecking=accept-new")
                break
    return await _run([git, *args], cwd=cwd, timeout=timeout)


# ── /status ──────────────────────────────────────────────────────
@router.get("/status")
async def deploy_status(request: Request, auth=Depends(require_admin_token)):
    """Read-only view of repo + backend service state."""
    repo = _find_repo_root()
    payload: Dict[str, Any] = {
        "ok": repo is not None,
        "repo_root": repo,
        "allowed_branches": sorted(ALLOWED_GIT_BRANCHES),
    }
    if not repo:
        await audit_log(request, action="deploy_status",
                          params={}, response_summary={"repo": None}, **auth)
        return payload

    branch = await _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, timeout=10)
    commit = await _git(["rev-parse", "HEAD"], cwd=repo, timeout=10)
    commit_short = await _git(["log", "-1", "--pretty=%h %s (%ar)"], cwd=repo, timeout=10)
    dirty = await _git(["status", "--porcelain"], cwd=repo, timeout=10)
    remote = await _git(["ls-remote", "origin", "refs/heads/" + sorted(ALLOWED_GIT_BRANCHES)[0]],
                          cwd=repo, timeout=15)

    # backend status (best effort)
    sctl = _which_supervisorctl()
    if sctl and "backend" in ALLOWED_SERVICES:
        svc = await _run([sctl, "status", "backend"], cwd=repo, timeout=15)
    else:
        svc = {"output": "(supervisorctl unavailable on this host)",
                "exit_code": -1}

    payload.update({
        "current_branch": (branch["output"] or "").strip(),
        "current_commit": (commit["output"] or "").strip(),
        "current_commit_summary": (commit_short["output"] or "").strip(),
        "is_dirty": bool((dirty["output"] or "").strip()),
        "dirty_files_preview": (dirty["output"] or "").strip().splitlines()[:20],
        "remote_head_for_allowed_branch": (remote["output"] or "").strip(),
        "backend_service_status": svc["output"][-500:],
    })
    await audit_log(request, action="deploy_status", params={},
                      response_summary={"branch": payload["current_branch"],
                                         "dirty": payload["is_dirty"]}, **auth)
    return payload


# ── /pull-and-restart ────────────────────────────────────────────
class PullBody(BaseModel):
    branch: str = "newestbuild"
    stash: bool = False
    restart_backend: bool = True
    confirm: bool = False
    py_compile_changed: bool = True


@router.post("/pull-and-restart")
async def pull_and_restart(body: PullBody, request: Request,
                              auth=Depends(require_admin_token)):
    if not body.confirm:
        raise HTTPException(400, "set confirm=true to deploy")
    if body.branch not in ALLOWED_GIT_BRANCHES:
        raise HTTPException(
            403,
            f"branch '{body.branch}' not in allowlist "
            f"{sorted(ALLOWED_GIT_BRANCHES)}")
    repo = _find_repo_root()
    if not repo:
        raise HTTPException(500, "could not locate repository root (.git)")

    steps: List[Dict[str, Any]] = []
    started_at = datetime.now(timezone.utc)

    def _step(name: str, result: Dict[str, Any]) -> Dict[str, Any]:
        result["step"] = name
        steps.append(result)
        return result

    # 1. record current HEAD
    head_before = await _git(["rev-parse", "HEAD"], cwd=repo, timeout=10)
    _step("rev-parse_HEAD_before", head_before)
    prev_sha = (head_before["output"] or "").strip()

    # 2. dirty check
    dirty_check = await _git(["status", "--porcelain"], cwd=repo, timeout=10)
    _step("status_porcelain", dirty_check)
    is_dirty = bool((dirty_check["output"] or "").strip())

    if is_dirty and not body.stash:
        await audit_log(request, action="deploy_pull",
                          params={"branch": body.branch, "stash": False},
                          response_summary={
                              "aborted": "dirty_working_tree",
                              "dirty_files": dirty_check["output"][:1000]},
                          **auth)
        return {
            "ok": False, "aborted": "dirty_working_tree",
            "message": "Working tree has uncommitted changes. "
                         "Re-call with stash=true to stash before pulling.",
            "dirty_files": dirty_check["output"][:2000],
            "steps": steps,
        }

    if is_dirty and body.stash:
        stash_name = f"emergent-admin-stash-{started_at.strftime('%Y%m%dT%H%M%SZ')}"
        st = await _git(["stash", "push", "--include-untracked", "-m", stash_name],
                          cwd=repo, timeout=30)
        _step(f"stash_{stash_name}", st)

    # 3. fetch
    fetch = await _git(["fetch", "origin", body.branch], cwd=repo, timeout=60)
    _step("git_fetch", fetch)
    if fetch["exit_code"] != 0:
        await audit_log(request, action="deploy_pull",
                          params={"branch": body.branch},
                          response_summary={"aborted": "fetch_failed"}, **auth)
        return {"ok": False, "aborted": "fetch_failed", "steps": steps}

    # 4. checkout (idempotent — git is fine being told to checkout the
    # branch you're already on)
    co = await _git(["checkout", body.branch], cwd=repo, timeout=30)
    _step("git_checkout", co)
    if co["exit_code"] != 0:
        await audit_log(request, action="deploy_pull",
                          params={"branch": body.branch},
                          response_summary={"aborted": "checkout_failed"}, **auth)
        return {"ok": False, "aborted": "checkout_failed", "steps": steps}

    # 5. ff-only pull
    pull = await _git(["pull", "--ff-only", "origin", body.branch],
                        cwd=repo, timeout=120)
    _step("git_pull_ff_only", pull)
    if pull["exit_code"] != 0:
        await audit_log(request, action="deploy_pull",
                          params={"branch": body.branch},
                          response_summary={
                              "aborted": "ff_only_pull_failed",
                              "git_output_tail": pull["output"][-1000:]},
                          **auth)
        return {"ok": False, "aborted": "ff_only_pull_failed",
                 "hint": "Non-fast-forward merge required — investigate manually.",
                 "steps": steps}

    # 6. new HEAD + changed files
    head_after = await _git(["rev-parse", "HEAD"], cwd=repo, timeout=10)
    _step("rev-parse_HEAD_after", head_after)
    new_sha = (head_after["output"] or "").strip()

    if prev_sha and new_sha and prev_sha == new_sha:
        await audit_log(request, action="deploy_pull",
                          params={"branch": body.branch},
                          response_summary={"no_op": True, "sha": new_sha},
                          **auth)
        return {"ok": True, "no_op": True, "sha": new_sha,
                 "message": "Already at the latest commit; nothing to do.",
                 "steps": steps}

    diff = await _git(["diff", "--name-only", f"{prev_sha}..{new_sha}"],
                        cwd=repo, timeout=20) if prev_sha else \
            {"output": "", "exit_code": 0}
    _step("git_diff_name_only", diff)
    changed_files = [ln.strip() for ln in (diff["output"] or "").splitlines()
                       if ln.strip()]
    changed_py = [f for f in changed_files if f.endswith(".py")]

    # 7. py_compile changed files (does NOT execute; pure parse-check)
    compile_results: List[Dict[str, Any]] = []
    compile_failed = False
    if body.py_compile_changed and changed_py:
        for f in changed_py:
            full = os.path.join(repo, f)
            if not os.path.isfile(full):
                compile_results.append({"file": f, "skip": "missing"})
                continue
            r = await _run([sys.executable, "-m", "py_compile", full],
                            cwd=repo, timeout=PY_COMPILE_TIMEOUT)
            compile_results.append({"file": f,
                                       "exit_code": r["exit_code"],
                                       "output": r["output"][-500:]})
            if r["exit_code"] != 0:
                compile_failed = True
        _step("py_compile", {"results": compile_results,
                                "failed": compile_failed,
                                "files_checked": len(compile_results)})

    if compile_failed:
        await audit_log(request, action="deploy_pull",
                          params={"branch": body.branch},
                          response_summary={
                              "aborted": "py_compile_failed",
                              "prev_sha": prev_sha, "new_sha": new_sha},
                          **auth)
        return {
            "ok": False, "aborted": "py_compile_failed",
            "message": "Pulled new code but py_compile FAILED on at least "
                         "one file. Backend NOT restarted.",
            "prev_sha": prev_sha, "new_sha": new_sha,
            "changed_files": changed_files,
            "compile_results": compile_results,
            "steps": steps,
        }

    # 8. best-effort restart
    restart_result: Dict[str, Any] = {"attempted": False}
    if body.restart_backend:
        sctl = _which_supervisorctl()
        if not sctl:
            restart_result = {"attempted": True,
                                "skipped": "supervisorctl_not_available"}
        else:
            r = await _run([sctl, "restart", "backend"], cwd=repo,
                            timeout=RESTART_TIMEOUT)
            restart_result = {"attempted": True,
                                "exit_code": r["exit_code"],
                                "output_tail": r["output"][-1000:]}
        _step("restart_backend", restart_result)

    finished_at = datetime.now(timezone.utc)
    summary = {
        "prev_sha": prev_sha, "new_sha": new_sha,
        "changed_file_count": len(changed_files),
        "changed_python_count": len(changed_py),
        "compile_failed": compile_failed,
        "restart_attempted": restart_result.get("attempted"),
        "restart_exit_code": restart_result.get("exit_code"),
        "duration_s": (finished_at - started_at).total_seconds(),
    }
    await audit_log(request, action="deploy_pull",
                      params={"branch": body.branch, "stash": body.stash,
                                "restart_backend": body.restart_backend},
                      response_summary=summary, **auth)

    return {
        "ok": True,
        "prev_sha": prev_sha, "new_sha": new_sha,
        "branch": body.branch,
        "changed_files": changed_files,
        "compile_results": compile_results,
        "restart": restart_result,
        "summary": summary,
        "steps": steps,
    }
