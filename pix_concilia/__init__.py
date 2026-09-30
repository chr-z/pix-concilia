"""pix-concilia — conciliação de Pix recebido contra o que foi vendido.

O problema que resolve
---------------------
Empresa brasileira vende por Pix. O cliente paga, o banco notifica, mas a
conciliação entre "o que caiu" e "o que foi vendido" é feita na mão: abrir
planilha, colar extrato, cruzar com pedidos. Quando dá diferença, alguém
descobre dias depois.

Esta biblioteca faz a conciliação no seu servidor. Sem cadastro, sem cloud,
sem enviar dado de cliente para lugar nenhum.

    >>> from pix_concilia import conciliar
    >>> r = conciliar(extrato, pedidos)
    >>> r.resumir()
    {'ok': 2, 'divergente': 1, ...}

Determinístico, sem dependência, sem rede. Feito para rodar no fim do dia.
"""

from .modelo import (
    Conciliacao,
    Divergencia,
    Extrato,
    LinhaExtrato,
    Pedido,
    Status,
    TOLERANCIA_PADRAO,
)
from .motor import conciliar

__version__ = "1.0.0"

__all__ = [
    "conciliar",
    "Conciliacao",
    "Divergencia",
    "Extrato",
    "LinhaExtrato",
    "Pedido",
    "Status",
    "TOLERANCIA_PADRAO",
    "__version__",
]
