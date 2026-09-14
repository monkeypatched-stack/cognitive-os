"""Robotics Capabilities — industrial protocol integrations."""

from __future__ import annotations

from cerebellum.capability import Capability


class MQTTCapability(Capability):
    """MQTT message broker integration."""

    def __init__(self, broker_url: str = "mqtt://localhost:1883", client_id: str = ""):
        super().__init__(name="mqtt")
        self._broker_url = broker_url
        self._client_id = client_id

    async def execute(self, state, **kwargs):
        topic = state.get("topic", "")
        payload = state.get("payload", "")
        operation = state.get("operation", "publish")

        try:
            import paho.mqtt.client as mqtt

            client = mqtt.Client(client_id=self._client_id)

            if operation == "publish":
                client.connect(self._broker_url.replace("mqtt://", "").split(":")[0])
                client.publish(topic, payload)
                client.disconnect()
                return {"status": "published", "topic": topic}
            elif operation == "subscribe":
                return {
                    "status": "subscribed",
                    "topic": topic,
                    "message": "Use callback for async subscription",
                }
        except Exception as e:
            return {"error": str(e), "success": False}

        return {"status": "configured", "protocol": "mqtt"}


class OPCUACapability(Capability):
    """OPC-UA industrial protocol integration."""

    def __init__(self, endpoint: str = "opc.tcp://localhost:4840"):
        super().__init__(name="opcua")
        self._endpoint = endpoint

    async def execute(self, state, **kwargs):
        node_id = state.get("node_id", "")
        operation = state.get("operation", "read")

        try:
            from asyncua import Client

            client = Client(url=self._endpoint)
            await client.connect()

            if operation == "read":
                node = await client.get_node(node_id)
                value = await node.read_value()
                await client.disconnect()
                return {"value": value, "node_id": node_id}
            elif operation == "write":
                node = await client.get_node(node_id)
                await node.write_value(state.get("value"))
                await client.disconnect()
                return {"status": "written", "node_id": node_id}
        except Exception as e:
            return {"error": str(e), "success": False}

        return {"status": "configured", "protocol": "opcua"}


class ModbusCapability(Capability):
    """Modbus industrial protocol integration."""

    def __init__(self, host: str = "localhost", port: int = 502):
        super().__init__(name="modbus")
        self._host = host
        self._port = port

    async def execute(self, state, **kwargs):
        try:
            from pymodbus.client import ModbusTcpClient

            client = ModbusTcpClient(self._host, port=self._port)
            client.connect()

            address = state.get("address", 0)
            count = state.get("count", 1)
            result = client.read_holding_registers(address, count)

            client.close()
            return {"registers": result.registers, "address": address}
        except Exception as e:
            return {"error": str(e), "success": False}


class CANBusCapability(Capability):
    """CAN Bus industrial protocol integration."""

    def __init__(self, interface: str = "can0"):
        super().__init__(name="can_bus")
        self._interface = interface

    async def execute(self, state, **kwargs):
        return {
            "status": "configured",
            "protocol": "can_bus",
            "interface": self._interface,
        }


class EdgeXFoundryCapability(Capability):
    """EdgeX Foundry IoT platform integration."""

    def __init__(self, api_url: str = "http://localhost:48082"):
        super().__init__(name="edgex_foundry")
        self._api_url = api_url

    async def execute(self, state, **kwargs):
        import httpx

        operation = state.get("operation", "list_devices")
        async with httpx.AsyncClient(timeout=30.0) as client:
            if operation == "list_devices":
                response = await client.get(f"{self._api_url}/api/v3/device")
                return response.json()
            elif operation == "get_device":
                device_id = state.get("device_id", "")
                response = await client.get(f"{self._api_url}/api/v3/device/{device_id}")
                return response.json()
            return {"status": "unknown_operation"}
