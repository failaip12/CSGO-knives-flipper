import csv
from datetime import datetime
from typing import List, Tuple


def log_failed_knives(failed_knives: List[str], file_name) -> None:
    """Log the names of knives that failed to fetch into a CSV file."""
    with open(file_name + "-" + datetime.now().strftime('%Y-%m-%d') + ".csv", mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        for knife in failed_knives:
            writer.writerow([knife])  # Log the failed knife name or other relevant info

def load_failed_knives_csv(file_name: str) -> List[Tuple[str]]:
    """
    Load a CSV file into a set.

    Args:
        file_path (str): Path to the CSV file.

    Returns:
        set: A set containing rows of the CSV file as tuples.
    """
    data_set = set()
    csv_name = file_name + ".csv"
    try:
        with open(csv_name, mode='r', encoding='utf-8') as file:
            csv_reader = csv.reader(file)
            for row in csv_reader:
                data_set.add((row[0],))  # Convert each row to a tuple and add to the set
    except FileNotFoundError:
        print(f"Error: File '{csv_name}' not found.")
    except Exception as e:
        print(f"An error occurred: {e}")
    return list(data_set)