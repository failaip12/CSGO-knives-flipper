import asyncio
from CustomLogger import CustomLogger, LogLevel
from playwright_scraper import update_all_knife_data

if __name__ == "__main__":
    logger = CustomLogger(log_file="knives_playwright.log", log_level=LogLevel.INFO)
    
    # This will run the asynchronous scraper
    asyncio.run(update_all_knife_data("failed_knives.csv", logger))