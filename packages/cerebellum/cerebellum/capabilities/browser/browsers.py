"""Browser Capabilities — web browser integrations."""

from __future__ import annotations

from cerebellum.capability import Capability


class PlaywrightCapability(Capability):
    def __init__(self):
        super().__init__(name="playwright")

    async def execute(self, state, **kwargs):
        return {"status": "configured", "browser": "playwright"}


class SeleniumCapability(Capability):
    def __init__(self):
        super().__init__(name="selenium")

    async def execute(self, state, **kwargs):
        return {"status": "configured", "browser": "selenium"}


class PuppeteerCapability(Capability):
    def __init__(self):
        super().__init__(name="puppeteer")

    async def execute(self, state, **kwargs):
        return {"status": "configured", "browser": "puppeteer"}
