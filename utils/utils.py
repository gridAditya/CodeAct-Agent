import os
import uuid
from pathlib import Path
from colorama import Fore, Style

from config import logging_config
import logging

def read_file_content(file_path: str)->str:
    """
    Read entire file content using UTF-8 encoding with proper error handling.
    
    Args:
        file_path (str): Absolute Path to the file to read
        
    Returns:
        Optional[str]: File content as string, or None if error occurred
    """
    
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()
        logging.info(f"Successfully read file: {file_path}")
        return content

def generate_unique_id():
    """
    Generate a unique ID using UUID4.
    Returns:
        str: A unique identifier string.
    """
    return str(uuid.uuid4())

def ensure_directory(path: str) -> bool:
    """
    Ensure that the given directory exists.
    If it does not exist, create it.

    Args:
        path (str): The directory path to check/create.

    Returns:
        bool: True if the directory exists or was created successfully,
              False if there was an error.
    """
    try:
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)  # safely creates nested dirs too
        return True
    except OSError as e:
        return False

def to_absolute_path(rel_path: str) -> str:
    """
    Convert a (possibly relative) path to an absolute path.
    Expands user (~) and resolves "."/"..".
    Raises FileNotFoundError if the path (after expansion) does not exist.
    """
    p = Path(rel_path).expanduser()
    try:
        # strict=True -> raise FileNotFoundError if path doesn't exist
        return str(p.resolve(strict=True))
    except FileNotFoundError:
        raise FileNotFoundError(f"Path does not exist: {rel_path!r}")