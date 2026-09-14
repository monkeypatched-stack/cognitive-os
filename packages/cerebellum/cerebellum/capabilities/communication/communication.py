"""Communication Capabilities — messaging integrations."""

from __future__ import annotations

from cerebellum.capability import Capability


class EmailCapability(Capability):
    def __init__(self, smtp_host: str = "", smtp_port: int = 587):
        super().__init__(name="email")
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port

    async def execute(self, state, **kwargs):
        return {"status": "configured", "communication": "email"}


class SlackCapability(Capability):
    def __init__(self, webhook_url: str = ""):
        super().__init__(name="slack")
        self._webhook_url = webhook_url

    async def execute(self, state, **kwargs):
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self._webhook_url, json={"text": state.get("message", "")})
            return {"status": "sent" if response.status_code == 200 else "failed"}


class DiscordCapability(Capability):
    def __init__(self, webhook_url: str = ""):
        super().__init__(name="discord")
        self._webhook_url = webhook_url

    async def execute(self, state, **kwargs):
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self._webhook_url, json={"content": state.get("message", "")})
            return {"status": "sent" if response.status_code == 204 else "failed"}


class TelegramCapability(Capability):
    def __init__(self, bot_token: str = ""):
        super().__init__(name="telegram")
        self._bot_token = bot_token

    async def execute(self, state, **kwargs):
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                json={
                    "chat_id": state.get("chat_id", ""),
                    "text": state.get("message", ""),
                },
            )
            return response.json()


class MicrosoftTeamsCapability(Capability):
    def __init__(self, webhook_url: str = ""):
        super().__init__(name="microsoft_teams")
        self._webhook_url = webhook_url

    async def execute(self, state, **kwargs):
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self._webhook_url, json={"text": state.get("message", "")})
            return {"status": "sent" if response.status_code == 200 else "failed"}
