import csv
from datetime import datetime
import os
import random
import shutil
import string
import tempfile
from typing import List, Tuple

from CustomLogger import CustomLogger


def log_failed_knives(failed_knives: List[str], file_name, logger: CustomLogger) -> None:    
    """Log the names of knives that failed to fetch into a CSV file."""
    try:
        with open(file_name, mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            for knife in failed_knives:
                writer.writerow([knife])  # Log the failed knife name or other relevant info
    except FileNotFoundError:
        logger.error(f"File '{file_name}' not found.")
    except Exception as e:
        logger.error(f"An error occurred: {e}")


def load_failed_knives_csv(file_name: str, logger: CustomLogger) -> List[Tuple[str]]:
    """
    Load a CSV file into a set.

    Args:
        file_path (str): Path to the CSV file.

    Returns:
        set: A set containing rows of the CSV file as tuples.
    """
    data_set = set()
    try:
        with open(file_name, mode='r', encoding='utf-8') as file:
            csv_reader = csv.reader(file)
            for row in csv_reader:
                data_set.add(row[0])  # Convert each row to a tuple and add to the set
    except FileNotFoundError:
        logger.error(f"File '{file_name}' not found.")
    except Exception as e:
        logger.error(f"An error occurred: {e}")
    return list(data_set)

def copy_user_data_dir(source_dir: str, logger: CustomLogger, dir_name: str) -> str:
    """
    Copy the user data directory to a unique directory for each thread.
    Ensures that the directory name is unique to avoid conflicts.
    """
    while True:
        # Generate a truly unique temporary directory using random suffix
        unique_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        temp_dir = os.path.join(tempfile.gettempdir(), f"{dir_name}_{unique_suffix}")
        
        # Ensure the directory doesn't already exist
        if not os.path.exists(temp_dir):
            break  # We found a unique directory, break the loop

    # Now create the directory
    os.makedirs(temp_dir)
    # Copy the contents of the source directory to the unique directory
    try:
        shutil.copytree(source_dir, temp_dir, dirs_exist_ok=True)
    except Exception as e:
        logger.error(f"Error copying directory: {e}")
        shutil.rmtree(temp_dir)  # Cleanup in case of failure
        raise e

    return temp_dir