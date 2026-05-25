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
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'app.log')

    logger = logging.getLogger("app_logger")
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if setup_logging is called multiple times (e.g. during reload)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s]: %(message)s")
        
        # Console (Terminal) Handler
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(logging.INFO)
        logger.addHandler(stream_handler)
        
        # File Handler
        file_handler = logging.FileHandler(log_file, encoding="utf-8", mode="a")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)
        
        # Prevent log duplication by disabling propagation to uvicorn's root handlers
        logger.propagate = False

    logger.info("✅ Logging initialized successfully.")
    return logger