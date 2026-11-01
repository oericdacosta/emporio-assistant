"""Onde ficam os dados da loja (CSVs, manual de políticas e regras)."""

from __future__ import annotations

import os
from pathlib import Path


def default_data_dir() -> Path:
    """Pasta com os CSVs: variável de ambiente, depois o repositório, depois o pacote instalado."""
    if configured := os.environ.get("EMPORIO_DATA_DIR"):
        return Path(configured)
    here = Path(__file__).resolve()
    checkout = here.parents[2] / "data" / "raw"
    return checkout if checkout.is_dir() else here.parent / "_data" / "raw"
