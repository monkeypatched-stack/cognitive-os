import os
import asyncio
import uvicorn
import logging
import sys
from dotenv import load_dotenv

try:
    from services.file.src.core.config import app, logger, FORWARD_ENABLED, FORWARD_URL
except ModuleNotFoundError:
    from src.core.config import app, logger, FORWARD_ENABLED, FORWARD_URL

# Load environment variables
load_dotenv()


# ============================
# RUN SERVER
# ============================
async def run_server():
    """Run the FastAPI server with uvicorn"""
    port = int(os.getenv("PORT", "8005"))

    try:
        if FORWARD_ENABLED:
            logger.info(f"🔌 Forward URL: {FORWARD_URL}")
        logger.info("=" * 70)

        config = uvicorn.Config(
            app=app, host="0.0.0.0", port=port, log_level="info", access_log=True
        )
        server = uvicorn.Server(config)
        await server.serve()

    except Exception as e:
        logger.error(f"FAILURE: Could not start server — {e}", exc_info=True)


if __name__ == "__main__":
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("\n🛑 Stopped by user")
