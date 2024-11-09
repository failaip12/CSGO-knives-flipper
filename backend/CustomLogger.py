from datetime import datetime

class CustomLogger:
    LOG_LEVELS = {
        "[DEBUG]": 10,
        "[INFO]": 20,
        "[WARNING]": 30,
        "[ERROR]": 40,
        "[CRITICAL]": 50
    }

    def __init__(self, log_file="app.log", log_level="[INFO]"):
        self.log_file = log_file
        self.log_level = log_level
        self._setup_log_file()

    def _setup_log_file(self):
        # Ensure log file is created with UTF-8 encoding
        with open(self.log_file, 'w', encoding='utf-8') as file:
            file.write("")

    def _log(self, level, message):
        # Check if the message level is high enough to log
        if CustomLogger.LOG_LEVELS[level] < CustomLogger.LOG_LEVELS[self.log_level]:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"{timestamp} - {level} - {message}\n"

        # Print to console
        print(formatted_message, end="")

        # Write to file with UTF-8 encoding
        with open(self.log_file, 'a', encoding='utf-8') as file:
            file.write(formatted_message)

    def set_log_level(self, level):
        if level in CustomLogger.LOG_LEVELS:
            self.log_level = level
        else:
            raise ValueError(f"Invalid log level: {level}")


    def debug(self, message):
        self._log("[DEBUG]", message)
    
    def info(self, message):
        self._log("[INFO]", message)

    def warning(self, message):
        self._log("[WARNING]", message)

    def error(self, message):
        self._log("[ERROR]", message)
    
    def critical(self, message):
        self._log("[CRITICAL]", message)