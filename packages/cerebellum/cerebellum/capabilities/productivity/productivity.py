"""Productivity Capabilities — calendar, project management, documentation integrations."""

from __future__ import annotations

from cerebellum.capability import Capability


class GoogleCalendarCapability(Capability):
    """Google Calendar integration via Google Calendar API v3."""

    def __init__(self, api_key: str = "", calendar_id: str = "primary"):
        super().__init__(name="google_calendar")
        self._api_key = api_key
        self._calendar_id = calendar_id
        self._base_url = "https://www.googleapis.com/calendar/v3"

    async def execute(self, state, **kwargs):
        import httpx

        operation = state.get("operation", "list")
        async with httpx.AsyncClient(timeout=30.0) as client:
            if operation == "list":
                response = await client.get(
                    f"{self._base_url}/calendars/{self._calendar_id}/events",
                    params={
                        "key": self._api_key,
                        "maxResults": state.get("max_results", 10),
                    },
                )
                return response.json()
            elif operation == "create":
                response = await client.post(
                    f"{self._base_url}/calendars/{self._calendar_id}/events",
                    params={"key": self._api_key},
                    json=state.get("event", {}),
                )
                return response.json()
            return {"status": "unknown_operation"}


class JiraCapability(Capability):
    """Jira integration via Jira REST API v2/v3."""

    def __init__(self, base_url: str = "", email: str = "", api_token: str = ""):
        super().__init__(name="jira")
        self._base_url = base_url
        self._email = email
        self._api_token = api_token

    async def execute(self, state, **kwargs):
        import httpx

        operation = state.get("operation", "search")
        async with httpx.AsyncClient(timeout=30.0) as client:
            if operation == "search":
                response = await client.get(
                    f"{self._base_url}/rest/api/3/search",
                    params={
                        "jql": state.get("jql", ""),
                        "maxResults": state.get("max_results", 10),
                    },
                    auth=(self._email, self._api_token),
                )
                return response.json()
            elif operation == "create":
                response = await client.post(
                    f"{self._base_url}/rest/api/3/issue",
                    json=state.get("issue", {}),
                    auth=(self._email, self._api_token),
                )
                return response.json()
            return {"status": "unknown_operation"}


class NotionCapability(Capability):
    """Notion integration via Notion API v1."""

    def __init__(self, api_key: str = ""):
        super().__init__(name="notion")
        self._api_key = api_key
        self._base_url = "https://api.notion.com/v1"

    async def execute(self, state, **kwargs):
        import httpx

        operation = state.get("operation", "query")
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Notion-Version": "2022-06-28",
            }
            if operation == "query":
                response = await client.post(
                    f"{self._base_url}/databases/{state.get('database_id', '')}/query",
                    headers=headers,
                    json=state.get("filter", {}),
                )
                return response.json()
            elif operation == "create_page":
                response = await client.post(
                    f"{self._base_url}/pages",
                    headers=headers,
                    json=state.get("page", {}),
                )
                return response.json()
            return {"status": "unknown_operation"}


class TrelloCapability(Capability):
    """Trello integration via Trello REST API."""

    def __init__(self, api_key: str = "", token: str = ""):
        super().__init__(name="trello")
        self._api_key = api_key
        self._token = token
        self._base_url = "https://api.trello.com/1"

    async def execute(self, state, **kwargs):
        import httpx

        operation = state.get("operation", "get_cards")
        params = {"key": self._api_key, "token": self._token}
        async with httpx.AsyncClient(timeout=30.0) as client:
            if operation == "get_cards":
                response = await client.get(
                    f"{self._base_url}/boards/{state.get('board_id', '')}/cards",
                    params=params,
                )
                return response.json()
            elif operation == "create_card":
                params.update(state.get("card", {}))
                response = await client.post(f"{self._base_url}/cards", params=params)
                return response.json()
            return {"status": "unknown_operation"}


class ClickUpCapability(Capability):
    """ClickUp integration via ClickUp API v2."""

    def __init__(self, api_key: str = ""):
        super().__init__(name="clickup")
        self._api_key = api_key
        self._base_url = "https://api.clickup.com/api/v2"

    async def execute(self, state, **kwargs):
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self._base_url}/team/{state.get('team_id', '')}/task",
                headers={"Authorization": self._api_key},
            )
            return response.json()


class AsanaCapability(Capability):
    """Asana integration via Asana REST API."""

    def __init__(self, access_token: str = ""):
        super().__init__(name="asana")
        self._access_token = access_token
        self._base_url = "https://app.asana.com/api/1.0"

    async def execute(self, state, **kwargs):
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self._base_url}/projects/{state.get('project_id', '')}/tasks",
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            return response.json()
