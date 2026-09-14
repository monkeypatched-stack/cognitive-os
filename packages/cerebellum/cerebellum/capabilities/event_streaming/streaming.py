"""Event Streaming Capabilities — message broker integrations."""

from __future__ import annotations

from cerebellum.capability import Capability


class KafkaCapability(Capability):
    def __init__(self, bootstrap_servers: str = "localhost:9092"):
        super().__init__(name="kafka")
        self._bootstrap_servers = bootstrap_servers

    async def execute(self, state, **kwargs):
        return {"status": "configured", "streaming": "kafka"}


class RabbitMQCapability(Capability):
    def __init__(self, url: str = "amqp://localhost"):
        super().__init__(name="rabbitmq")
        self._url = url

    async def execute(self, state, **kwargs):
        return {"status": "configured", "streaming": "rabbitmq"}


class NATSCapability(Capability):
    def __init__(self, url: str = "nats://localhost:4222"):
        super().__init__(name="nats")
        self._url = url

    async def execute(self, state, **kwargs):
        return {"status": "configured", "streaming": "nats"}


class RedisStreamsCapability(Capability):
    def __init__(self, url: str = "redis://localhost:6379"):
        super().__init__(name="redis_streams")
        self._url = url

    async def execute(self, state, **kwargs):
        return {"status": "configured", "streaming": "redis_streams"}
