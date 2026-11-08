"""Versioned, strict project configuration without invoking external tools."""

from .project import ConfigError, ProjectConfig, load_project

__all__ = ["ConfigError", "ProjectConfig", "load_project"]
