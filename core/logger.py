import os

def log_print(message: str, level: str="INFO"):
    print(f"[{level}] {message}")

def trigger_token_setup(platform: str):
    log_print(f"Token expirado/faltante para {platform}. Por favor configura las credenciales.", "CRITICAL")
    # In a real microservice, we might trigger a webhook or send an alert.
