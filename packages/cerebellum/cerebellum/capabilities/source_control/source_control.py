"""Source Control Capabilities — Git platform integrations."""

from __future__ import annotations

from cerebellum.capability import Capability


class GitHubCapability(Capability):
    """GitHub integration via GitHub REST API v3.

    Operations:
      create_repo         — create a new repo under the authenticated user or an org
      delete_repo         — delete a repo (requires delete_repo OAuth scope)
      get_repo            — get repo metadata
      list_repos          — list repos for the authenticated user
      create_issue        — open a new issue
      get_issue           — fetch one issue
      list_pull_requests  — list open PRs
      create_pull_request — open a new PR
      get_authenticated_user — return the authenticated user's login/id
    """

    def __init__(self, token: str = ""):
        super().__init__(name="github")
        self._token = token or __import__("os").environ.get("GITHUB_TOKEN", "")
        self._base_url = "https://api.github.com"

    def _headers(self) -> dict:
        return {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def execute(self, state, **kwargs):
        import httpx

        if not self._token:
            return {"status": "error", "error": "GITHUB_TOKEN not set"}

        operation = state.get("operation", "list_repos")
        headers = self._headers()

        try:
            return await self._dispatch(operation, state, headers)
        except httpx.HTTPError as exc:
            # Every other capability in providers.py wraps its network calls
            # (see NANDACapability._discover/_route) — this one didn't, so a
            # real GitHub outage/timeout/DNS failure raised uncaught instead
            # of degrading to a structured error like every sibling call.
            return {"status": "error", "error": f"GitHub request failed: {exc}"}

    async def _dispatch(self, operation: str, state: dict, headers: dict):
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            # ── Repo management ──────────────────────────────────────────────

            if operation == "create_repo":
                org = state.get("org", "")
                url = f"{self._base_url}/orgs/{org}/repos" if org else f"{self._base_url}/user/repos"
                body = {
                    "name": state.get("name", ""),
                    "description": state.get("description", ""),
                    "private": state.get("private", False),
                    "auto_init": state.get("auto_init", True),
                    "default_branch": state.get("default_branch", "main"),
                }
                if state.get("gitignore_template"):
                    body["gitignore_template"] = state["gitignore_template"]
                if state.get("license_template"):
                    body["license_template"] = state["license_template"]
                response = await client.post(url, headers=headers, json=body)
                data = response.json()
                if response.status_code in (200, 201):
                    return {
                        "status": "created",
                        "repo": data.get("full_name"),
                        "clone_url": data.get("clone_url"),
                        "ssh_url": data.get("ssh_url"),
                        "html_url": data.get("html_url"),
                        "default_branch": data.get("default_branch"),
                        "private": data.get("private"),
                    }
                return {
                    "status": "error",
                    "http_status": response.status_code,
                    "error": data.get("message", str(data)),
                }

            elif operation == "delete_repo":
                owner = state.get("owner", "")
                repo = state.get("repo", "")
                response = await client.delete(f"{self._base_url}/repos/{owner}/{repo}", headers=headers)
                if response.status_code == 204:
                    return {"status": "deleted", "repo": f"{owner}/{repo}"}
                data = response.json() if response.content else {}
                return {
                    "status": "error",
                    "http_status": response.status_code,
                    "error": data.get("message", ""),
                }

            elif operation == "get_repo":
                owner = state.get("owner", "")
                repo = state.get("repo", "")
                response = await client.get(f"{self._base_url}/repos/{owner}/{repo}", headers=headers)
                return response.json()

            elif operation == "list_repos":
                params = {
                    "type": state.get("type", "owner"),
                    "sort": state.get("sort", "updated"),
                    "per_page": state.get("per_page", 30),
                }
                response = await client.get(f"{self._base_url}/user/repos", headers=headers, params=params)
                return response.json()

            elif operation == "get_authenticated_user":
                response = await client.get(f"{self._base_url}/user", headers=headers)
                return response.json()

            # ── Issues ───────────────────────────────────────────────────────

            elif operation == "get_issue":
                owner = state.get("owner", "")
                repo = state.get("repo", "")
                issue_number = state.get("issue_number", "")
                response = await client.get(
                    f"{self._base_url}/repos/{owner}/{repo}/issues/{issue_number}",
                    headers=headers,
                )
                return response.json()

            elif operation == "create_issue":
                owner = state.get("owner", "")
                repo = state.get("repo", "")
                response = await client.post(
                    f"{self._base_url}/repos/{owner}/{repo}/issues",
                    headers=headers,
                    json=state.get("issue", {}),
                )
                return response.json()

            # ── Pull requests ────────────────────────────────────────────────

            elif operation == "list_pull_requests":
                owner = state.get("owner", "")
                repo = state.get("repo", "")
                response = await client.get(f"{self._base_url}/repos/{owner}/{repo}/pulls", headers=headers)
                return response.json()

            elif operation == "create_pull_request":
                owner = state.get("owner", "")
                repo = state.get("repo", "")
                response = await client.post(
                    f"{self._base_url}/repos/{owner}/{repo}/pulls",
                    headers=headers,
                    json={
                        "title": state.get("title", ""),
                        "head": state.get("head", ""),
                        "base": state.get("base", "main"),
                        "body": state.get("body", ""),
                        "draft": state.get("draft", False),
                    },
                )
                data = response.json()
                if response.status_code in (200, 201):
                    return {
                        "status": "created",
                        "pr_number": data.get("number"),
                        "html_url": data.get("html_url"),
                        "state": data.get("state"),
                    }
                return {
                    "status": "error",
                    "http_status": response.status_code,
                    "error": data.get("message", str(data)),
                }

            return {"status": "unknown_operation", "operation": operation}


class GitLabCapability(Capability):
    """GitLab integration via GitLab API v4."""

    def __init__(self, token: str = "", base_url: str = "https://gitlab.com/api/v4"):
        super().__init__(name="gitlab")
        self._token = token
        self._base_url = base_url

    async def execute(self, state, **kwargs):
        import httpx

        operation = state.get("operation", "list_projects")
        headers = {"PRIVATE-TOKEN": self._token}
        async with httpx.AsyncClient(timeout=30.0) as client:
            if operation == "list_projects":
                response = await client.get(f"{self._base_url}/projects", headers=headers)
                return response.json()
            elif operation == "get_merge_request":
                project_id = state.get("project_id", "")
                mr_iid = state.get("mr_iid", "")
                response = await client.get(
                    f"{self._base_url}/projects/{project_id}/merge_requests/{mr_iid}",
                    headers=headers,
                )
                return response.json()
            return {"status": "unknown_operation"}


class BitbucketCapability(Capability):
    """Bitbucket integration via Bitbucket REST API 2.0."""

    def __init__(self, username: str = "", app_password: str = ""):
        super().__init__(name="bitbucket")
        self._username = username
        self._app_password = app_password
        self._base_url = "https://api.bitbucket.org/2.0"

    async def execute(self, state, **kwargs):
        import httpx

        operation = state.get("operation", "list_repos")
        async with httpx.AsyncClient(timeout=30.0) as client:
            if operation == "list_repos":
                response = await client.get(
                    f"{self._base_url}/repositories/{self._username}",
                    auth=(self._username, self._app_password),
                )
                return response.json()
            return {"status": "unknown_operation"}


class AzureDevOpsCapability(Capability):
    """Azure DevOps integration via Azure DevOps REST API."""

    def __init__(self, organization: str = "", token: str = ""):
        super().__init__(name="azure_devops")
        self._organization = organization
        self._token = token
        self._base_url = f"https://dev.azure.com/{organization}"

    async def execute(self, state, **kwargs):
        import httpx

        operation = state.get("operation", "list_projects")
        async with httpx.AsyncClient(timeout=30.0) as client:
            if operation == "list_projects":
                response = await client.get(
                    f"{self._base_url}/_apis/projects",
                    headers={"Authorization": f"Basic {self._token}"},
                )
                return response.json()
            return {"status": "unknown_operation"}
