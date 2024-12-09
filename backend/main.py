import time
from CustomLogger import CustomLogger, LogLevel
from selenium_scraper import initialize_driver, steam_login, update_all_knife_data

from seleniumwire2 import webdriver
if __name__ == "__main__":
    logger = CustomLogger(log_file="knives.log", log_level=LogLevel.INFO)
    
    #names = load_failed_knives_csv("fk")
    #process_knives(names, "new_fk")
    #update_all_knife_data('failed_knives', logger)
    #connection, cursor = connect_to_db('localhost', 'knives', 3306, 'root', '', logger)
    #knife_name = "★ StatTrak™ Huntsman Knife | Case Hardened (Field-Tested)"
    #driver, user_data_dir = initialize_driver(False, logger)
    #chrome_driver = steam_login()
    #time.sleep(100000)
    #knife_info = safe_get_knife_info((knife_name, ), driver, cursor, connection, 6, logger)
    #driver.quit()
    #shutil.rmtree(user_data_dir)
    #update_all_knife_data("'2024-11-03'")
    #get_knife_info("★ StatTrak™ Flip Knife | Bright Water (Battle-Scarred)")
    # Update Knife List
    # knife_names = get_knife_list(driver)
    # add_new_knives_to_db(knife_names, cursor)
    # exit()
