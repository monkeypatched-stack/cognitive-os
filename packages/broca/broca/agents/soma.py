"""Soma Git Agents — ETASS agents that execute soma CLI commands against the spec repo.

Every agent is driven by etass/workloads/spec_lifecycle.yaml.
No hardcoded prompts; reasoning about commit messages, conflicts, and branch
strategy is delegated to _llm_from_spec().

Agents:
  SomaGitAgent       — universal passthrough: any soma subcommand
  SpecStatusAgent    — soma status → structured working-tree summary
  SpecCheckoutAgent  — soma checkout (creates branch if -b flag used)
  SpecAddAgent       — soma add (stage files)
  SpecCommitAgent    — soma add + commit; LLM generates message if omitted
  SpecPushAgent      — soma push; retries with --set-upstream on first push
  SpecPullAgent      — soma pull with rebase
  SpecSyncAgent      — full lifecycle: pull → add → commit → push
  SpecBranchAgent    — soma branch (list / create / delete)
  SpecDiffAgent      — soma diff → structured change summary
  SpecStashAgent     — soma stash push/pop
  SpecMergeAgent     — soma merge with conflict detection
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.soma")


# Resolve soma binary: project venv → running interpreter's Scripts/bin → PATH
def _find_soma_bin() -> str:
    import sys as _sys

    candidates = [
        Path(__file__).parents[4] / ".venv" / "bin" / "soma",  # project root .venv (unix)
        Path(__file__).parents[4] / ".venv" / "Scripts" / "soma.exe",  # project root .venv (windows)
        Path(_sys.executable).parent / "soma",  # active interpreter bin dir
        Path(_sys.executable).parent / "soma.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "soma"


_SOMA_BIN = _find_soma_bin()


def _spec_repo() -> Path:
    env = os.environ.get("MONKEYBRAIN_SPEC_REPO", "")
    if env:
        return Path(env).expanduser().resolve()
    config_file = Path(__file__).parents[4] / ".soma" / "config.json"
    if config_file.exists():
        try:
            import json

            cfg = json.loads(config_file.read_text())
            if cfg.get("spec_repo"):
                return Path(cfg["spec_repo"]).expanduser().resolve()
        except Exception:
            pass
    return Path(__file__).parents[4]


def _soma(
    *args: str,
    spec_repo: str = "",
    timeout: int = 60,
) -> tuple[int, str, str]:
    """Run a soma command. Returns (exit_code, stdout, stderr)."""
    env = os.environ.copy()
    if spec_repo:
        env["MONKEYBRAIN_SPEC_REPO"] = spec_repo
    elif "MONKEYBRAIN_SPEC_REPO" not in env:
        env["MONKEYBRAIN_SPEC_REPO"] = str(_spec_repo())

    cmd = [_SOMA_BIN, *[str(a) for a in args]]
    logger.debug("[soma] %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


async def _soma_async(*args: str, spec_repo: str = "", timeout: int = 60) -> tuple[int, str, str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _soma(*args, spec_repo=spec_repo, timeout=timeout))


def _outcome(code: int, stdout: str, stderr: str, command: str) -> dict[str, Any]:
    return {
        "command": command,
        "exit_code": code,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "success": code == 0,
    }


# ---------------------------------------------------------------------------
# Universal passthrough
# ---------------------------------------------------------------------------


class SomaGitAgent(BaseETASSAgent):
    agent_type = "soma_git"
    description = "Universal soma/git command executor against the configured spec repo"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        command = context.get("command", "status")
        args = context.get("args", [])
        spec_repo = context.get("spec_repo", "")

        code, stdout, stderr = await _soma_async(command, *args, spec_repo=spec_repo)
        payload = _outcome(code, stdout, stderr, f"soma {command} {' '.join(str(a) for a in args)}")
        self._reward(code == 0)
        return self._result(
            payload=payload,
            observations=[stdout[:400] if code == 0 else stderr[:400]],
        )


# ---------------------------------------------------------------------------
# Specific workflow agents
# ---------------------------------------------------------------------------


class SpecStatusAgent(BaseETASSAgent):
    agent_type = "spec_status"
    description = "soma status — returns structured working-tree summary for the spec repo"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        spec_repo = context.get("spec_repo", "")
        code, stdout, stderr = await _soma_async("status", "--short", spec_repo=spec_repo)

        lines = stdout.strip().splitlines()
        modified = [line[3:] for line in lines if line.startswith(" M") or line.startswith("M ")]
        untracked = [line[3:] for line in lines if line.startswith("??")]
        staged = [line[3:] for line in lines if line[0] in ("A", "M", "D", "R") and not line.startswith("??")]

        clean = not lines
        self._reward(True)
        return self._result(
            payload={
                "clean": clean,
                "modified": modified,
                "untracked": untracked,
                "staged": staged,
                "total_changes": len(lines),
            },
            observations=[f"{len(lines)} changes" if lines else "working tree clean"],
        )


class SpecCheckoutAgent(BaseETASSAgent):
    agent_type = "spec_checkout"
    description = "soma checkout — switch or create branches in the spec repo"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        branch = context.get("branch", "main")
        create = context.get("create", False)
        spec_repo = context.get("spec_repo", "")

        args = ["-b", branch] if create else [branch]
        code, stdout, stderr = await _soma_async("checkout", *args, spec_repo=spec_repo)

        success = code == 0
        self._reward(success)
        return self._result(
            payload={
                "branch": branch,
                "created": create and success,
                "exit_code": code,
                "output": (stdout or stderr).strip(),
            },
            observations=[
                (f"{'created and ' if create else ''}checked out branch: {branch}" if success else stderr.strip())
            ],
        )


class SpecAddAgent(BaseETASSAgent):
    agent_type = "spec_add"
    description = "soma add — stage spec files for commit"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        files = context.get("files", ["."])
        spec_repo = context.get("spec_repo", "")

        if isinstance(files, str):
            files = [files]

        code, stdout, stderr = await _soma_async("add", *files, spec_repo=spec_repo)
        self._reward(code == 0)
        return self._result(
            payload={"staged": files, "exit_code": code},
            observations=[f"staged {len(files)} path(s)" if code == 0 else stderr.strip()],
        )


class SpecCommitAgent(BaseETASSAgent):
    agent_type = "spec_commit"
    description = "soma add + commit — stages all spec changes and commits; LLM generates message if not provided"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        spec_repo = context.get("spec_repo", "")
        message = context.get("message", "")
        files = context.get("files", ["."])
        if isinstance(files, str):
            files = [files]

        # Stage
        await _soma_async("add", *files, spec_repo=spec_repo)

        # Check if there is anything staged
        code, status_out, _ = await _soma_async("status", "--short", spec_repo=spec_repo)
        staged_lines = [line for line in status_out.splitlines() if line and line[0] not in (" ", "?")]
        if not staged_lines:
            self._reward(True, 0.8)
            return self._result(
                payload={"status": "nothing_to_commit"},
                observations=["working tree clean — nothing to commit"],
            )

        # Generate commit message via spec if not provided
        if not message:
            try:
                changes_summary = "\n".join(status_out.splitlines()[:20])
                message = await self._llm_from_spec(
                    "spec_lifecycle",
                    goal_override=(
                        f"Generate a concise, conventional-commit style git commit message "
                        f"(one line, ≤72 chars, no quotes) for these spec changes:\n\n{changes_summary}"
                    ),
                    system_override="Reply with ONLY the commit message text. No explanation.",
                    max_tokens=80,
                )
                message = message.strip().strip('"').strip("'").splitlines()[0][:72]
            except Exception:
                message = "feat: update ETASS specs"

        code, stdout, stderr = await _soma_async("commit", "-m", message, spec_repo=spec_repo)
        success = code == 0
        self._reward(success)
        return self._result(
            payload={
                "status": "committed" if success else "failed",
                "message": message,
                "exit_code": code,
                "output": (stdout or stderr).strip(),
            },
            observations=[f"committed: {message}" if success else stderr.strip()],
        )


class SpecPushAgent(BaseETASSAgent):
    agent_type = "spec_push"
    description = "soma push — push spec repo to remote; auto-sets upstream on first push"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        spec_repo = context.get("spec_repo", "")
        remote = context.get("remote", "origin")
        branch = context.get("branch", "")

        args = [remote, branch] if branch else [remote]
        code, stdout, stderr = await _soma_async("push", *args, spec_repo=spec_repo)

        # Retry with --set-upstream on first push failure
        if code != 0 and "no upstream" in stderr.lower():
            # Determine current branch
            _, br_out, _ = await _soma_async("rev-parse", "--abbrev-ref", "HEAD", spec_repo=spec_repo)
            current = br_out.strip() or "main"
            code, stdout, stderr = await _soma_async("push", "--set-upstream", remote, current, spec_repo=spec_repo)

        success = code == 0
        self._reward(success)
        return self._result(
            payload={
                "status": "pushed" if success else "failed",
                "remote": remote,
                "exit_code": code,
                "output": (stdout or stderr).strip(),
            },
            observations=[f"pushed to {remote}" if success else stderr.strip()],
        )


class SpecPullAgent(BaseETASSAgent):
    agent_type = "spec_pull"
    description = "soma pull --rebase — sync spec repo from remote"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        spec_repo = context.get("spec_repo", "")
        remote = context.get("remote", "origin")
        rebase = context.get("rebase", True)

        args = ["--rebase"] if rebase else []
        code, stdout, stderr = await _soma_async("pull", remote, *args, spec_repo=spec_repo)
        success = code == 0
        self._reward(success)
        return self._result(
            payload={
                "status": "updated" if success else "failed",
                "remote": remote,
                "output": (stdout or stderr).strip(),
            },
            observations=[(stdout or stderr).strip()[:300]],
        )


class SpecSyncAgent(BaseETASSAgent):
    agent_type = "spec_sync"
    description = "Full spec lifecycle: pull → add → commit (LLM message) → push"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        spec_repo = context.get("spec_repo", "")
        message = context.get("message", "")
        files = context.get("files", ["."])

        steps: list[dict] = []

        # 1. Pull
        code, out, err = await _soma_async("pull", "origin", "--rebase", spec_repo=spec_repo)
        steps.append({"step": "pull", "success": code == 0, "output": (out or err).strip()[:200]})
        if code != 0 and "nothing to pull" not in (out + err).lower():
            logger.warning("[spec_sync] pull failed: %s", err)

        # 2. Status check
        _, status_out, _ = await _soma_async("status", "--short", spec_repo=spec_repo)
        has_changes = bool(status_out.strip())

        if not has_changes:
            self._reward(True, 0.8)
            return self._result(
                payload={"status": "already_synced", "steps": steps},
                observations=["spec repo already up to date — nothing to commit"],
            )

        # 3. Add
        if isinstance(files, str):
            files = [files]
        code, out, err = await _soma_async("add", *files, spec_repo=spec_repo)
        steps.append({"step": "add", "success": code == 0})

        # 4. Generate commit message if not provided
        if not message:
            try:
                changes_summary = "\n".join(status_out.splitlines()[:20])
                message = await self._llm_from_spec(
                    "spec_lifecycle",
                    goal_override=(
                        f"Generate a concise conventional-commit message (≤72 chars, no quotes) "
                        f"for these ETASS spec changes:\n\n{changes_summary}"
                    ),
                    system_override="Reply with ONLY the commit message. No explanation.",
                    max_tokens=80,
                )
                message = message.strip().strip('"').strip("'").splitlines()[0][:72]
            except Exception:
                message = "feat: sync ETASS specs"

        # 5. Commit
        code, out, err = await _soma_async("commit", "-m", message, spec_repo=spec_repo)
        committed = code == 0
        steps.append({"step": "commit", "success": committed, "message": message})

        if not committed and "nothing to commit" in (out + err).lower():
            self._reward(True, 0.8)
            return self._result(
                payload={"status": "nothing_to_commit", "steps": steps},
                observations=["nothing to commit after add"],
            )

        # 6. Push
        code, out, err = await _soma_async("push", "origin", spec_repo=spec_repo)
        if code != 0 and "no upstream" in err.lower():
            _, br_out, _ = await _soma_async("rev-parse", "--abbrev-ref", "HEAD", spec_repo=spec_repo)
            code, out, err = await _soma_async(
                "push",
                "--set-upstream",
                "origin",
                br_out.strip() or "main",
                spec_repo=spec_repo,
            )
        steps.append({"step": "push", "success": code == 0, "output": (out or err).strip()[:200]})

        success = all(s["success"] for s in steps if s["step"] in ("commit", "push"))
        self._reward(success)
        return self._result(
            payload={
                "status": "synced" if success else "partial",
                "message": message,
                "steps": steps,
            },
            observations=[f"synced: {message}" if success else "partial sync — check steps"],
        )


class SpecBranchAgent(BaseETASSAgent):
    agent_type = "spec_branch"
    description = "soma branch — list, create, or delete branches in the spec repo"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        action = context.get("action", "list")  # list | create | delete
        branch = context.get("branch", "")
        spec_repo = context.get("spec_repo", "")

        if action == "list":
            code, out, err = await _soma_async("branch", "-a", spec_repo=spec_repo)
            branches = [b.strip().lstrip("* ") for b in out.splitlines() if b.strip()]
            self._reward(code == 0)
            return self._result(
                payload={"branches": branches},
                observations=[f"{len(branches)} branches"],
            )

        if action == "create":
            code, out, err = await _soma_async("checkout", "-b", branch, spec_repo=spec_repo)
        elif action == "delete":
            code, out, err = await _soma_async("branch", "-d", branch, spec_repo=spec_repo)
        else:
            self._reward(False, 0.3)
            return self._result(payload={"error": f"unknown action: {action}"})

        success = code == 0
        self._reward(success)
        return self._result(
            payload={
                "action": action,
                "branch": branch,
                "success": success,
                "output": (out or err).strip(),
            },
            observations=[(out or err).strip()[:200]],
        )


class SpecDiffAgent(BaseETASSAgent):
    agent_type = "spec_diff"
    description = "soma diff — returns structured change summary for spec repo"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        spec_repo = context.get("spec_repo", "")
        ref = context.get("ref", "HEAD")  # compare against this ref
        files = context.get("files", [])

        args = ["--stat", ref, "--"] + files if files else ["--stat", ref]
        code, stat_out, _ = await _soma_async("diff", *args, spec_repo=spec_repo)

        # Full diff (capped)
        full_args = [ref, "--"] + files if files else [ref]
        _, full_out, _ = await _soma_async("diff", *full_args, spec_repo=spec_repo)

        self._reward(True)
        return self._result(
            payload={
                "stat": stat_out.strip(),
                "diff": full_out.strip()[:3000],
                "ref": ref,
            },
            observations=[stat_out.strip()[:300] or "no changes"],
        )


class SpecStashAgent(BaseETASSAgent):
    agent_type = "spec_stash"
    description = "soma stash — stash or restore spec changes"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        action = context.get("action", "push")  # push | pop | list | drop
        message = context.get("message", "")
        spec_repo = context.get("spec_repo", "")

        if action == "push":
            args = ["push", "-m", message] if message else ["push"]
        elif action == "pop":
            args = ["pop"]
        elif action == "list":
            args = ["list"]
        elif action == "drop":
            args = ["drop"]
        else:
            args = [action]

        code, out, err = await _soma_async("stash", *args, spec_repo=spec_repo)
        success = code == 0
        self._reward(success)
        return self._result(
            payload={
                "action": action,
                "success": success,
                "output": (out or err).strip(),
            },
            observations=[(out or err).strip()[:200]],
        )


class SpecRepoInitAgent(BaseETASSAgent):
    """Initialize or create a new spec repository, optionally creating it on GitHub first.

    Context:
      path: str            — local path for the repo (absolute or relative to cwd)
      initial_branch: str  — default branch name (default: main)
      remote: str          — remote URL to add as origin (if github_create=True this is
                             set automatically from the API response)
      set_active: bool     — if True, saves path to .soma/config.json (default: True)

      # GitHub repo creation (optional)
      github_create: bool  — if True, call GitHub API to create the remote repo first
      github_name: str     — repo name on GitHub (default: basename of path)
      github_org: str      — create under this org instead of the authenticated user
      github_private: bool — make the repo private (default: False)
      github_description: str
    """

    agent_type = "spec_repo_init"
    description = "soma repo create — init local git repo, optionally create on GitHub, wire remote"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        path = context.get("path", "")
        if not path:
            self._reward(False, 0.3)
            return self._result(
                payload={"error": "path is required"},
                observations=["missing required context key: path"],
            )

        initial_branch = context.get("initial_branch", "main")
        remote = context.get("remote", "")
        set_active = context.get("set_active", True)

        # Optionally create the repo on GitHub before doing git init
        github_result = None
        if context.get("github_create", False):
            github_result = await self._github_create_repo(context, path)
            if github_result.get("status") == "created":
                remote = remote or github_result.get("clone_url", "")
            else:
                self._reward(False, 0.4)
                return self._result(
                    payload={
                        "error": "GitHub repo creation failed",
                        "github": github_result,
                    },
                    observations=[github_result.get("error", "unknown error")],
                )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: self._create_repo(path, initial_branch, remote, set_active))
        if github_result:
            result["github"] = github_result
        success = result["success"]
        self._reward(success)
        return self._result(
            payload=result,
            observations=[result.get("message", "")],
        )

    async def _github_create_repo(self, context: dict, path: str) -> dict:
        import os as _os
        from cerebellum.capabilities.source_control.source_control import (
            GitHubCapability,
        )

        cap = GitHubCapability(token=_os.environ.get("GITHUB_TOKEN", ""))
        repo_name = context.get("github_name", "") or Path(path).expanduser().resolve().name
        return await cap.execute(
            {
                "operation": "create_repo",
                "name": repo_name,
                "org": context.get("github_org", ""),
                "private": context.get("github_private", False),
                "description": context.get("github_description", "MonkeyBrain spec repository"),
                "auto_init": False,  # we init locally and push
                "default_branch": context.get("initial_branch", "main"),
            }
        )

    def _create_repo(self, path: str, initial_branch: str, remote: str, set_active: bool) -> dict:
        import json as _json

        repo_path = Path(path).expanduser()
        if not repo_path.is_absolute():
            repo_path = Path.cwd() / repo_path
        repo_path = repo_path.resolve()

        already_existed = (repo_path / ".git").exists()
        repo_path.mkdir(parents=True, exist_ok=True)

        steps = []
        if not already_existed:
            # git init
            r = subprocess.run(
                ["git", "init", "--initial-branch", initial_branch],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                subprocess.run(["git", "init"], cwd=repo_path, capture_output=True)
                subprocess.run(
                    ["git", "checkout", "-b", initial_branch],
                    cwd=repo_path,
                    capture_output=True,
                )
            steps.append({"step": "git_init", "success": True})

            # Scaffold
            (repo_path / ".gitignore").write_text("*.pyc\n__pycache__/\n.env\n.DS_Store\n")
            (repo_path / "README.md").write_text(f"# {repo_path.name}\n\nMonkeyBrain spec repository.\n")
            subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True)
            r = subprocess.run(
                ["git", "commit", "-m", "chore: initial spec repo"],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            steps.append({"step": "initial_commit", "success": r.returncode == 0})

        # Add remote
        if remote:
            r = subprocess.run(
                ["git", "remote", "add", "origin", remote],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            added = r.returncode == 0 or "already exists" in r.stderr
            steps.append({"step": "add_remote", "success": added, "remote": remote})

            r = subprocess.run(
                ["git", "push", "--set-upstream", "origin", initial_branch],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            steps.append(
                {
                    "step": "push",
                    "success": r.returncode == 0,
                    "output": (r.stdout or r.stderr).strip()[:200],
                }
            )

        # Save as active spec repo
        if set_active:
            config_file = _spec_repo().parent / ".soma" / "config.json"
            # fall back to walking up from cwd
            config_file = Path(__file__).parents[4] / ".soma" / "config.json"
            config_file.parent.mkdir(parents=True, exist_ok=True)
            cfg: dict = {}
            if config_file.exists():
                try:
                    cfg = _json.loads(config_file.read_text())
                except Exception:
                    pass
            cfg["spec_repo"] = str(repo_path)
            config_file.write_text(_json.dumps(cfg, indent=2))
            steps.append({"step": "set_active", "success": True, "path": str(repo_path)})

        return {
            "success": all(s["success"] for s in steps),
            "path": str(repo_path),
            "already_existed": already_existed,
            "steps": steps,
            "message": f"spec repo {'configured' if already_existed else 'created'}: {repo_path}",
        }


class SpecMergeAgent(BaseETASSAgent):
    agent_type = "spec_merge"
    description = "soma merge — merge branches in spec repo; detects conflicts"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> Any:
        branch = context.get("branch", "main")
        strategy = context.get("strategy", "--no-ff")  # --ff-only | --no-ff | --squash
        spec_repo = context.get("spec_repo", "")

        code, out, err = await _soma_async("merge", strategy, branch, spec_repo=spec_repo)
        combined = (out + err).lower()
        conflict = "conflict" in combined or code != 0

        self._reward(not conflict)
        return self._result(
            payload={
                "branch": branch,
                "merged": code == 0,
                "conflict": conflict,
                "output": (out or err).strip()[:400],
            },
            observations=["merge conflict detected" if conflict else f"merged {branch}"],
        )
