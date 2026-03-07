from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class LogCleanupService:
    def __init__(self, log_dir: str | Path = "logs", max_size_mb: int = 10) -> None:
        self.log_dir = Path(log_dir)
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.cleanup_percent = 0.3  # Remove oldest 30%

    async def check_and_cleanup(self) -> None:
        """Check all log files and clean up if needed."""
        if not self.log_dir.exists():
            return

        for log_file in self.log_dir.rglob("*.log*"):
            if log_file.is_file():
                try:
                    await self._cleanup_single_file(log_file)
                except Exception as exc:
                    logger.error("Error cleaning up %s: %s", log_file, exc)

    async def _cleanup_single_file(self, file_path: Path) -> None:
        """Clean up a single log file if it exceeds size limit."""
        size = file_path.stat().st_size

        if size <= self.max_size_bytes:
            return

        logger.info(
            "Log file %s is %.2fMB, cleaning up...",
            file_path.name,
            size / 1024 / 1024,
        )

        with file_path.open("r", encoding="utf-8", errors="ignore") as file_obj:
            lines = file_obj.readlines()

        total_lines = len(lines)
        keep_lines = int(total_lines * (1 - self.cleanup_percent))

        if keep_lines < 10:
            keep_lines = min(total_lines, 10)

        new_lines = lines[-keep_lines:]

        with file_path.open("w", encoding="utf-8") as file_obj:
            file_obj.writelines(new_lines)

        new_size = file_path.stat().st_size
        logger.info(
            "Cleaned %s: %.2fMB -> %.2fMB",
            file_path.name,
            size / 1024 / 1024,
            new_size / 1024 / 1024,
        )

    async def cleanup_old_files(self, days: int = 30) -> None:
        """Delete log files older than specified days."""
        if not self.log_dir.exists():
            return

        cutoff = datetime.now() - timedelta(days=days)

        for log_file in self.log_dir.rglob("*.log*"):
            if not log_file.is_file():
                continue

            mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
            if mtime < cutoff:
                try:
                    log_file.unlink()
                    logger.info("Deleted old log: %s", log_file.name)
                except Exception as exc:
                    logger.error("Failed to delete old log %s: %s", log_file, exc)
