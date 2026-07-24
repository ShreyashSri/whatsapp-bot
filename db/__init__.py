"""Small PostgreSQL persistence layer for the bot's feature state."""

from .database import Database, create_database

__all__ = ["Database", "create_database"]
