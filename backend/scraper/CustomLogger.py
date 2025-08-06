import inspect
import os
from datetime import datetime
from enum import Enum, auto


class LogLevel(Enum):
    """Enum for defining log levels."""

    DEBUG = auto()
    INFO = auto()
    WARNING = auto()
    ERROR = auto()
    FATAL = auto()

    @classmethod
    def from_string(cls, level_str: str):
        """Convert a string to a LogLevel enum member."""
        level_mapping = {
            "[DEBUG]": cls.DEBUG,
            "[INFO]": cls.INFO,
            "[WARNING]": cls.WARNING,
            "[ERROR]": cls.ERROR,
            "[FATAL]": cls.FATAL,
        }
        return level_mapping.get(
            level_str.upper(), cls.INFO
        )  # Default to INFO if not found


class CustomLogger:
    def __init__(
        self,
        log_file: str = "app.log",
        log_level: LogLevel = LogLevel.INFO,
        log_format: str = "{timestamp} - {level} - {file}:{line} - {message}",
    ) -> None:
        # Check if log_level is a valid LogLevel enum member
        if not isinstance(log_level, LogLevel):
            raise ValueError(
                f"Invalid log level: {log_level}. Must be one of {', '.join([level.name for level in LogLevel])}."
            )

        self.log_file = log_file
        self.log_level = log_level
        self.log_format = log_format
        self._setup_log_file()

    def _setup_log_file(self) -> None:
        """Ensure log file exists or create a new one."""
        if not os.path.exists(self.log_file):
            with open(self.log_file, "w", encoding="utf-8") as file:
                file.write("")  # Create the file if it doesn't exist.

    def _get_formatted_message(self, level: LogLevel, message: str) -> str:
        """Format log message with timestamp, log level, file, line number, and the custom message."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        frame = inspect.currentframe()
        # Safely traverse back three frames, checking for None
        for _ in range(3):
            if frame is not None and frame.f_back is not None:
                frame = frame.f_back
            else:
                break
        file_name = os.path.basename(
            frame.f_code.co_filename if frame is not None else "unknown"
        )  # Get the file name of the caller
        line_number = (
            frame.f_lineno if frame is not None else -1
        )  # Get the line number of the caller

        return self.log_format.format(
            timestamp=timestamp,
            level=level.name,
            message=message,
            file=file_name,
            line=line_number,
        )

    def _log(self, level: LogLevel, message: str) -> None:
        """Log a message if the log level is higher than the current set level."""
        if level.value < self.log_level.value:
            return

        formatted_message = self._get_formatted_message(level, message)

        # Print to console
        print(formatted_message)

        # Write to file with UTF-8 encoding (appending)
        with open(self.log_file, "a", encoding="utf-8") as file:
            file.write(formatted_message + "\n")

    def debug(self, message: str) -> None:
        """Log a debug message."""
        self._log(LogLevel.DEBUG, message)

    def info(self, message: str) -> None:
        """Log an informational message."""
        self._log(LogLevel.INFO, message)

    def warning(self, message: str) -> None:
        """Log a warning message."""
        self._log(LogLevel.WARNING, message)

    def error(self, message: str) -> None:
        """Log an error message."""
        self._log(LogLevel.ERROR, message)

    def fatal(self, message: str) -> None:
        """Log a fatal message."""
        self._log(LogLevel.FATAL, message)
