"""``emporio-ui``: abre a interface web igual em Linux, macOS e Windows (``uv run emporio-ui``).

Se ainda não existe ``.env``, cria um a partir do ``.env.example`` e para: o cliente preenche a
chave e o modelo e roda o mesmo comando de novo.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

APP = Path(__file__).with_name("app.py")
ENV, EXAMPLE = Path(".env"), Path(".env.example")


def ensure_env(env: Path = ENV, example: Path = EXAMPLE) -> bool:
    """``True`` se o ``.env`` já existia; senão o cria do exemplo (quando há) e devolve ``False``."""
    if env.exists():
        return True
    if example.exists():
        shutil.copyfile(example, env)
    return False


def main(argv: Sequence[str] | None = None) -> int:
    if not ensure_env():
        print(
            "Criei o arquivo .env. Abra-o, preencha OPENAI_API_KEY e LLM_MODEL "
            "e rode o comando de novo."
        )
        return 1
    extra = list(sys.argv[1:] if argv is None else argv)
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(APP), *extra])  # noqa: S603
