import os
import yaml
from pathlib import Path

def load_config():
    base_path = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_path, '..', 'config', 'secrets.yaml')
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)


def get_data_path(filename: str) -> Path:
    """
    Returns the full path to a file 
    """
    base_dir = Path(__file__).resolve().parents[1]  # Go to project root
    return base_dir / "database" / "datas" / filename


def save_debug_csv(df, name, folder="debug"):
    base_dir = os.path.dirname(os.path.abspath(__file__))  # location of utils.py
    target_folder = os.path.join(base_dir, folder)
    os.makedirs(target_folder, exist_ok=True)
    path = os.path.join(target_folder, f"{name}.csv")
    df.to_csv(path)
    print(f"Saved debug file: {path}")


