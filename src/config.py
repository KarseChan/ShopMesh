"""Centralized config loader from config.yaml + .env."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "config.yaml"


def _resolve_env_vars(obj):
    """Recursively resolve ${VAR} and ${VAR:-default} placeholders."""
    if isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
        expr = obj[2:-1]
        if ":-" in expr:
            var_name, default = expr.split(":-", 1)
            return os.environ.get(var_name, default)
        return os.environ.get(expr, obj)
    elif isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj


def load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return _resolve_env_vars(cfg)


config = load_config()
