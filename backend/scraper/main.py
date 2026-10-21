from CustomLogger import CustomLogger, LogLevel
from scraper import update_all_knife_data

if __name__ == "__main__":
    logger = CustomLogger(log_file="knives_scraper.log", log_level=LogLevel.INFO)

    update_all_knife_data("failed_knives.csv", logger)
