from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Severity(str, Enum):
    critical = "critical"
    major = "major"
    minor = "minor"
    nit = "nit"

    @property
    def rank(self) -> int:
        return {"critical": 4, "major": 3, "minor": 2, "nit": 1}[self.value]


class Category(str, Enum):
    bug = "bug"
    security = "security"
    performance = "performance"
    maintainability = "maintainability"
    style = "style"
    testing = "testing"


SEVERITY_ALIASES = {
    "high": "major",
    "blocker": "critical",
    "error": "major",
    "medium": "minor",
    "warning": "minor",
    "low": "nit",
    "info": "nit",
    "suggestion": "nit",
    "trivial": "nit",
}
CATEGORY_ALIASES = {
    "correctness": "bug",
    "logic": "bug",
    "error-handling": "bug",
    "reliability": "bug",
    "perf": "performance",
    "readability": "maintainability",
    "design": "maintainability",
    "naming": "style",
    "docs": "style",
    "documentation": "style",
    "test": "testing",
    "tests": "testing",
}


class Finding(BaseModel):
    path: str
    line: int = Field(ge=1)
    severity: Severity
    category: Category
    title: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=3, max_length=2000)
    suggestion: str | None = Field(default=None, max_length=3000)
    source: str = "llm"  # "llm" or "rule:<id>"

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, value):
        if isinstance(value, Enum):
            return value
        value = str(value).strip().lower()
        return SEVERITY_ALIASES.get(value, value)

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, value):
        if isinstance(value, Enum):
            return value
        value = str(value).strip().lower()
        return CATEGORY_ALIASES.get(value, value)

    @field_validator("suggestion", mode="before")
    @classmethod
    def _suggestion(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None
