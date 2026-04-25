# app.py
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
import os
# -------------------------------
# STEP 1️⃣ Setup logging function
# -------------------------------
def setup_logging() -> logging.Logger:
    """Configures logging and returns a shared logger instance."""
    
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')
    log_file = os.path.join(log_dir, 'app.log')

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
        handlers=[
            logging.StreamHandler(),  # Console
            logging.FileHandler(log_file, encoding="utf-8", mode="a")
        ]
    )

    logger = logging.getLogger("app_logger")
    logger.info("✅ Logging initialized successfully.")
    return logger