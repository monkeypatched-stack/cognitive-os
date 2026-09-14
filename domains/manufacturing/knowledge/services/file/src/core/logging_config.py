import logging

from services.common.logging import configure_service_logging


def setup_logging() -> logging.Logger:
    return configure_service_logging("file")


logger = setup_logging()
