"""Software Engineering Repository Agents — persistence for domain entities."""

from __future__ import annotations
import logging, os
from pathlib import Path
from typing import Any
from domains.package import RepositoryAgent

logger = logging.getLogger("domain.software_engineering.repositories")


class PullRequestRepositoryAgent(RepositoryAgent):
    """Manages pull request persistence via GitHub API or local cache."""

    def __init__(self, github_capability=None) -> None:
        super().__init__(name="pull_request_repository")
        self._github = github_capability

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        self._query_count += 1
        if decision.get("action") == "cache_hit":
            return {"repository": self.name, "action": "cache_hit", "source": "local"}
        if self._github:
            return {
                "repository": self.name,
                "action": "github_api",
                "query_count": self._query_count,
            }
        return {
            "repository": self.name,
            "action": "not_found",
            "query_count": self._query_count,
        }


class TestResultRepositoryAgent(RepositoryAgent):
    """Manages test result persistence — reads pytest output, stores results."""

    def __init__(self) -> None:
        super().__init__(name="test_result_repository")

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        self._query_count += 1
        return {
            "repository": self.name,
            "action": decision.get("action", "query"),
            "query_count": self._query_count,
        }


class CodeRepositoryAgent(RepositoryAgent):
    """Manages code/file persistence — wraps git filesystem operations."""

    def __init__(self) -> None:
        super().__init__(name="code_repository")

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        self._query_count += 1
        repo = Path(os.environ.get("MONKEYBRAIN_REPO", "."))
        return {
            "repository": self.name,
            "action": decision.get("action"),
            "repo_root": str(repo),
            "query_count": self._query_count,
        }
