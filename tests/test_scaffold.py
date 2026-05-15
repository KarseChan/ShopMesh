"""T0.1 scaffold verification tests."""
import importlib
import os
from pathlib import Path


def test_project_structure():
    """All required directories and __init__.py files exist."""
    root = Path(__file__).resolve().parent.parent
    expected_dirs = [
        "src",
        "src/agents",
        "src/tools",
        "src/graph",
        "src/models",
        "src/db",
        "src/retrieval",
        "src/memory",
        "src/security",
        "src/resilience",
        "src/skills",
        "src/observability",
        "src/router",
        "tests",
        "data",
        "scripts",
        "evals",
        "docs",
    ]
    for d in expected_dirs:
        assert (root / d).is_dir(), f"Missing directory: {d}"
        if d.startswith("src") or d == "tests":
            assert (root / d / "__init__.py").exists(), f"Missing __init__.py in {d}"


def test_config_files_exist():
    """Required config files exist."""
    root = Path(__file__).resolve().parent.parent
    for f in ["requirements.txt", "pyproject.toml", ".env.example", "config.yaml", ".gitignore"]:
        assert (root / f).exists(), f"Missing file: {f}"


def test_config_yaml_loads():
    """config.yaml is valid YAML."""
    import yaml

    root = Path(__file__).resolve().parent.parent
    with open(root / "config.yaml") as fh:
        cfg = yaml.safe_load(fh)
    assert "llm" in cfg
    assert "embedding" in cfg
    assert "vector_db" in cfg
    assert "database" in cfg
    assert cfg["llm"]["default"]["provider"] == "ollama"


def test_core_imports():
    """Core dependencies can be imported."""
    modules = [
        "fastapi",
        "uvicorn",
        "langgraph",
        "httpx",
        "pydantic",
        "sqlmodel",
        "structlog",
        "yaml",
    ]
    for mod in modules:
        importlib.import_module(mod)
