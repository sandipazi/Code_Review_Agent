import os

def read_file(file_path: str) -> str:
    """Read the content of a local file."""
    if not os.path.exists(file_path):
        return f"Error: File {file_path} does not exist."
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"
