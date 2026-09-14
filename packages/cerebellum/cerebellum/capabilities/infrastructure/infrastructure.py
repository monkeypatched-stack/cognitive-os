"""Infrastructure Capabilities — container and orchestration integrations."""

from __future__ import annotations

from cerebellum.capability import Capability


class DockerCapability(Capability):
    def __init__(self, socket: str = "unix:///var/run/docker.sock"):
        super().__init__(name="docker")
        self._socket = socket

    async def execute(self, state, **kwargs):
        return {"status": "configured", "infrastructure": "docker"}


class KubernetesCapability(Capability):
    def __init__(self, kubeconfig: str = ""):
        super().__init__(name="kubernetes")
        self._kubeconfig = kubeconfig

    async def execute(self, state, **kwargs):
        return {"status": "configured", "infrastructure": "kubernetes"}


class HelmCapability(Capability):
    def __init__(self, kubeconfig: str = ""):
        super().__init__(name="helm")
        self._kubeconfig = kubeconfig

    async def execute(self, state, **kwargs):
        return {"status": "configured", "infrastructure": "helm"}
