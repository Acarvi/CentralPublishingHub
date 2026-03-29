import os
from dotenv import load_dotenv

# Provide a method to strictly load env vars
load_dotenv()

def get_env_or_raise(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        # User requirement: "Si no se detectan las claves, el sistema debe lanzar un error descriptivo."
        raise ValueError(f"Falta variable de entorno obligatoria: {key}")
    return val

def get_env_optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

# Hub paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")

if not os.path.exists(CONFIG_DIR): os.makedirs(CONFIG_DIR)
if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR)
