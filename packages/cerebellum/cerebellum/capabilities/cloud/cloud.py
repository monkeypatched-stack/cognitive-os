"""Cloud Capabilities — cloud provider integrations."""

from __future__ import annotations

from cerebellum.capability import Capability


class AWSCapability(Capability):
    def __init__(self, region: str = "us-east-1"):
        super().__init__(name="aws")
        self._region = region

    async def execute(self, state, **kwargs):
        return {"status": "configured", "cloud": "aws", "region": self._region}


class AzureCapability(Capability):
    def __init__(self, subscription_id: str = ""):
        super().__init__(name="azure")
        self._subscription_id = subscription_id

    async def execute(self, state, **kwargs):
        return {"status": "configured", "cloud": "azure"}


class GoogleCloudCapability(Capability):
    def __init__(self, project_id: str = ""):
        super().__init__(name="google_cloud")
        self._project_id = project_id

    async def execute(self, state, **kwargs):
        return {"status": "configured", "cloud": "google_cloud"}


class CloudflareCapability(Capability):
    def __init__(self, api_token: str = ""):
        super().__init__(name="cloudflare")
        self._api_token = api_token

    async def execute(self, state, **kwargs):
        return {"status": "configured", "cloud": "cloudflare"}


class DigitalOceanCapability(Capability):
    def __init__(self, api_token: str = ""):
        super().__init__(name="digitalocean")
        self._api_token = api_token

    async def execute(self, state, **kwargs):
        return {"status": "configured", "cloud": "digitalocean"}
