"""Model files the app downloads into ``models/`` (TZ 4.14): ``resources/models.toml``."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from subvoicer.config.resources import read_toml


class ModelFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    file: str  # relative to models/
    url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0)

    def path(self, models_dir: Path) -> Path:
        return models_dir / self.file

    def present(self, models_dir: Path) -> bool:
        """Downloaded already: the hash is checked once, right after the download; at start
        the size is enough (hashing 100 MB on every start costs a second)."""
        path = self.path(models_dir)
        return path.is_file() and path.stat().st_size == self.size


def model_files() -> list[ModelFile]:
    return [ModelFile.model_validate(item) for item in read_toml("models.toml")["model"]]


def missing(models_dir: Path, files: list[ModelFile] | None = None) -> list[ModelFile]:
    return [m for m in (files if files is not None else model_files()) if not m.present(models_dir)]


def model_path(models_dir: Path, model_id: str) -> Path:
    for model in model_files():
        if model.id == model_id:
            return model.path(models_dir)
    raise KeyError(model_id)
