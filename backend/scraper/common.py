import csv

from CustomLogger import CustomLogger


def log_failed_knives(
    failed_knives: list[str], file_name, logger: CustomLogger
) -> None:
    """Log the names of knives that failed to fetch into a CSV file."""
    try:
        with open(file_name, mode="a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            for knife in failed_knives:
                writer.writerow(
                    [knife]
                )  # Log the failed knife name or other relevant info
    except FileNotFoundError:
        logger.error(f"File '{file_name}' not found.")
    except Exception:
        logger.exception(f"Could not write '{file_name}'.")


def load_failed_knives_csv(file_name: str, logger: CustomLogger) -> list[str]:
    """
    Load a CSV file into a set.

    Args:
        file_path (str): Path to the CSV file.

    Returns:
        set: A set containing rows of the CSV file as tuples.
    """
    data_set = set()
    try:
        with open(file_name, mode="r", encoding="utf-8") as file:
            csv_reader = csv.reader(file)
            for row in csv_reader:
                data_set.add(row[0])  # Convert each row to a tuple and add to the set
    except FileNotFoundError:
        logger.error(f"File '{file_name}' not found.")
    except Exception:
        logger.exception(f"Could not read '{file_name}'.")
    return list(data_set)
