"""Códigos de saída do ``emporio``."""

from __future__ import annotations

from typing import Final

OK: Final = 0
UNEXPECTED: Final = 1  # erro inesperado (só encerra o programa no modo ``-m``)
USAGE: Final = 2  # flag inválida (o argparse sai com 2 sozinho)
CONFIG: Final = 3  # modelo, chave, ``--now`` ou dados inválidos
UNAVAILABLE: Final = 4  # provedor fora do ar; o turno não foi gravado (só no modo ``-m``)
CASSETTE: Final = 5  # cassete ausente ou incompatível com o que o agente pede
INTERRUPTED: Final = 130  # Ctrl-C
