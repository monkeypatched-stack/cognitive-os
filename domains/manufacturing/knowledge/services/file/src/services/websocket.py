import os
import json
import base64
import logging
from typing import Optional, Dict, Any

try:
    import websockets
except ImportError:
    raise ImportError(
        "websockets library not found. Install with: pip install websockets"
    )

log = logging.getLogger(__name__)


async def send_file_to_bridge(
    file_path: str,
    ws_uri: Optional[str] = None,
    timeout: float = 30.0,
    send_as_binary: bool = False,
) -> Dict[str, Any]:
    """
    Send a file to the WebSocket-to-NATS bridge.

    Args:
        file_path: Path to the file to send
        ws_uri: WebSocket URI (default: ws://websocket-server:6789)
                Use 'ws://localhost:6789' for local testing
        timeout: Connection and send timeout in seconds
        send_as_binary: If True, send as binary frame; if False, send as base64 JSON

    Returns:
        Dict with server response, e.g.:
        {
            "status": "queued",
            "len": 12345,
            "saved_file": "invoice.pdf"
        }

    Raises:
        FileNotFoundError: If file doesn't exist
        websockets.exceptions.WebSocketException: On connection/send errors
        asyncio.TimeoutError: If operation times out
        json.JSONDecodeError: If server response is invalid
    """
    # Default to Docker service name, not localhost
    if ws_uri is None:
        ws_uri = os.getenv("WEBSOCKET_BRIDGE_URI", "ws://websocket-server:6789")

    # Validate file exists
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)

    log.info("Sending file to WebSocket bridge: %s (%d bytes)", file_name, file_size)
    log.debug("WebSocket URI: %s", ws_uri)
    log.debug("Send mode: %s", "binary" if send_as_binary else "base64-JSON")

    try:
        # Connect to WebSocket server with timeout
        async with websockets.connect(ws_uri, open_timeout=timeout) as websocket:
            log.debug("✓ Connected to WebSocket server")

            # Read file contents
            with open(file_path, "rb") as f:
                file_data = f.read()

            # Send file based on mode
            if send_as_binary:
                # Send as raw binary frame
                log.debug("Sending binary frame...")
                await websocket.send(file_data)

            else:
                # Send as base64-encoded JSON
                log.debug("Encoding file as base64...")
                encoded_data = base64.b64encode(file_data).decode("utf-8")

                payload = {"fileName": file_name, "data": encoded_data}

                log.debug("Sending JSON payload...")
                await websocket.send(json.dumps(payload))

            log.debug("Waiting for server acknowledgment...")

            # Wait for server acknowledgment
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                log.error("Timeout waiting for server response")
                raise

            # Parse response
            try:
                ack = json.loads(response)
                log.debug("Server response: %s", ack)
            except json.JSONDecodeError as e:
                log.error("Invalid JSON response from server: %s", response)
                raise

            # Check response status
            if ack.get("status") == "queued":
                log.info("✓ File queued successfully: %s", file_name)
            elif ack.get("status") == "nack":
                reason = ack.get("reason", "unknown")
                log.warning("✗ File rejected by server: %s", reason)
            else:
                log.warning("⚠ Unexpected server response: %s", ack)

            return ack

    except websockets.exceptions.InvalidURI as e:
        log.error("Invalid WebSocket URI: %s", ws_uri)
        raise

    except websockets.exceptions.WebSocketException as e:
        log.error("WebSocket error: %s", e)
        raise

    except Exception as e:
        log.error("Unexpected error sending file: %s", e)
        raise


async def send_multiple_files(
    file_paths: list,
    ws_uri: Optional[str] = None,
    timeout: float = 30.0,
    send_as_binary: bool = False,
) -> Dict[str, Any]:
    """
    Send multiple files to the WebSocket bridge (reusing connection).

    Args:
        file_paths: List of file paths to send
        ws_uri: WebSocket URI
        timeout: Timeout per file
        send_as_binary: Send mode

    Returns:
        Dict mapping file paths to their responses:
        {
            "file1.pdf": {"status": "queued", ...},
            "file2.png": {"status": "nack", ...}
        }
    """
    if ws_uri is None:
        ws_uri = os.getenv("WEBSOCKET_BRIDGE_URI", "ws://websocket-server:6789")

    results = {}

    log.info("Sending %d files to WebSocket bridge", len(file_paths))

    try:
        async with websockets.connect(ws_uri, open_timeout=timeout) as websocket:
            log.debug("✓ Connected to WebSocket server")

            for file_path in file_paths:
                try:
                    # Validate file
                    if not os.path.isfile(file_path):
                        log.warning("Skipping non-existent file: %s", file_path)
                        results[file_path] = {
                            "status": "error",
                            "reason": "file_not_found",
                        }
                        continue

                    file_name = os.path.basename(file_path)

                    # Read and send file
                    with open(file_path, "rb") as f:
                        file_data = f.read()

                    if send_as_binary:
                        await websocket.send(file_data)
                    else:
                        payload = {
                            "fileName": file_name,
                            "data": base64.b64encode(file_data).decode("utf-8"),
                        }
                        await websocket.send(json.dumps(payload))

                    # Wait for ACK
                    response = await asyncio.wait_for(websocket.recv(), timeout=timeout)

                    ack = json.loads(response)
                    results[file_path] = ack

                    log.info("✓ Sent %s: %s", file_name, ack.get("status"))

                except Exception as e:
                    log.error("Error sending %s: %s", file_path, e)
                    results[file_path] = {"status": "error", "reason": str(e)}

            log.info(
                "Sent %d/%d files successfully",
                sum(1 for r in results.values() if r.get("status") == "queued"),
                len(file_paths),
            )

            return results

    except Exception as e:
        log.error("Connection error: %s", e)
        raise


# Synchronous wrapper for non-async contexts
def send_file_sync(
    file_path: str,
    ws_uri: Optional[str] = None,
    timeout: float = 30.0,
    send_as_binary: bool = False,
) -> Dict[str, Any]:
    """
    Synchronous wrapper for send_file_to_bridge.

    Use this in non-async code (e.g., Flask routes, scripts).
    """
    import asyncio

    try:
        # Try to get existing event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're already in an async context - can't use run()
            raise RuntimeError(
                "send_file_sync() cannot be called from async context. "
                "Use 'await send_file_to_bridge()' instead."
            )
    except RuntimeError:
        pass

    # Run in new event loop
    return asyncio.run(
        send_file_to_bridge(
            file_path=file_path,
            ws_uri=ws_uri,
            timeout=timeout,
            send_as_binary=send_as_binary,
        )
    )


if __name__ == "__main__":
    """Example usage and testing."""
    import sys
    import asyncio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Example: Send a test file
    async def test():
        # For local testing, use localhost
        # For Docker, use service name
        test_uri = "ws://localhost:6789"  # Change as needed

        # Create a test file
        test_file = "/tmp/test_upload.txt"
        with open(test_file, "w") as f:
            f.write("Hello from WebSocket client!\n")

        print(f"Sending test file: {test_file}")

        try:
            # Test base64-JSON mode
            result = await send_file_to_bridge(
                test_file, ws_uri=test_uri, send_as_binary=False
            )
            print(f"Result: {result}")

        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)

    asyncio.run(test())
