import asyncio
import websockets
import json
import base64
import os

WS_URL = "ws://172.19.0.2:6789"
FILE_PATH = "test.txt"  # change if needed


async def send_file():
    if not os.path.exists(FILE_PATH):
        print("File not found:", FILE_PATH)
        return

    with open(FILE_PATH, "rb") as f:
        file_bytes = f.read()

    payload = {
        "type": "file",
        "fileName": os.path.basename(FILE_PATH),
        "data": base64.b64encode(file_bytes).decode("utf-8"),
    }

    async with websockets.connect(WS_URL) as ws:
        print("📡 Connected (file mode)")
        await ws.send(json.dumps(payload))
        print("📤 File sent — closing connection")

        # Small delay helps ensure packet is flushed before close
        await asyncio.sleep(0.1)
        await ws.close()


async def send_text():
    payload = {"type": "text", "message": "Hello from WebSocket client 👋"}

    async with websockets.connect(WS_URL) as ws:
        print("📡 Connected (text mode)")
        await ws.send(json.dumps(payload))
        print("💬 Message sent — closing connection")

        await asyncio.sleep(0.1)
        await ws.close()


async def main():
    await send_text()
    # await send_file()  # uncomment if you want file test too


asyncio.run(main())
