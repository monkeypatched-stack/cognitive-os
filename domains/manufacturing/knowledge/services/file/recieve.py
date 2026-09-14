import asyncio
import base64
import io
import json
from pypdf import PdfReader
import websockets

WS_URL = "ws://172.19.0.6:6789"  # change if needed


async def extract_text_from_pdf(message: str):
    try:
        res = json.loads(message)

        print("📄 PDF file received, extracting text...")

        pdf_base64 = res["data"]
        pdf_bytes = base64.b64decode(pdf_base64)

        pdf_file = io.BytesIO(pdf_bytes)
        pdf_reader = PdfReader(pdf_file)

        text = ""
        for page_num, page in enumerate(pdf_reader.pages):
            text += f"\n--- Page {page_num + 1} ---\n"
            extracted = page.extract_text()
            if extracted:
                text += extracted
        return text

    except Exception as e:
        print("❌ Failed to extract PDF text:", e)


async def receive_messages():
    print(f"🔌 Connecting to {WS_URL} ...")

    async with websockets.connect(WS_URL) as ws:
        print("✅ Connected. Waiting for messages...\n")

        try:
            async for message in ws:
                if isinstance(message, bytes):
                    print(f"📦 Binary message received ({len(message)} bytes)")
                else:
                    print("💬 Text message received")

                    # Try to process as PDF payload
                    text = await extract_text_from_pdf(message)

        except websockets.exceptions.ConnectionClosed as e:
            print(f"❌ Connection closed: {e}")


asyncio.run(receive_messages())
