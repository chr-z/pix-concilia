"""Modelo de dados e tipos da conciliação de Pix."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional

# Tolerância padrão em BRL. Pix é centavo-exato, mas taxa/arranjo do banco
# às vezes devolve centavos a mais ou a menos. 0,01 evita alarme falso;
# acima disso já é erro de conciliação de verdade.
TOLERANCIA_PADRAO = Decimal("0.01")

_CPFS = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_CNPJS = re.compile(r"\d{2}\.?\d{3}\.?\d{3}\/?\d{4}-?\d{2}")
_CELULAR = re.compile(r"(\d{2})\s*9?\s*(\d{4})-?(\d{4})")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


class Status(str, Enum):
    """Situação de uma linha de extrato depois da conciliação."""

    CONCILIADO = "conciliado"
    VALOR_DIVERGENTE = "valor_divergente"
    PEDIDO_NAO_ENCONTRADO = "pedido_nao_encontrado"
    VALOR_NAO_IDENTIFICADO = "valor_nao_identificado"


def so_digitos(valor: object) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def chave_pix(valor: object) -> str:
    """Chave Pix é sempre comparável como dígitos crus (CPF, CNPJ, telefone, email, aleatória)."""
    bruto = str(valor or "").strip()
    if "@" in bruto:
        normalizado = unicodedata.normalize("NFKD", bruto.lower())
        sem_acento = "".join(c for c in normalizado if not unicodedata.combining(c))
        return sem_acento.strip()
    digitos = so_digitos(bruto)
    if digitos:
        return digitos
    normalizado = unicodedata.normalize("NFKD", bruto.lower())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def normaliza_cpf_cnpj(valor: object) -> str:
    """Deixa o documento em 11 ou 14 dígitos, para casar CPF com CNPJ com segurança."""
    return so_digitos(valor)


def to_decimal(valor: object) -> Decimal:
    """Aceita 1.234,56 / 1234.56 / 1234,56 e devolve Decimal. Nunca float."""
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        # float já perdeu o centavo antes de chegar aqui (0.1+0.2 = 0.30000000000000004).
        # str() não salva: devolve o artefato inteiro. Arredonda para 2 casas, que é
        # a precisão do dinheiro brasileiro. Nenhum valor em reais tem mais que isso.
        return Decimal(f"{valor:.2f}")
    texto = str(valor or "").strip()
    if not texto:
        return Decimal("0")
    texto = texto.replace("R$", "").replace(" ", "").replace("\xa0", "")
    if "," in texto and "." in texto:
        # formato BR: ponto é milhar, vírgula é decimal
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return Decimal(texto)
    except InvalidOperation as exc:
        raise ValueError(f"valor não numérico: {valor!r}") from exc


@dataclass
class Pedido:
    """O que foi vendido e por quanto se espera receber."""

    id: str
    valor: Decimal | object
    chave: Optional[str] = None
    cpf_cnpj: Optional[str] = None
    descricao: str = ""
    data: Optional[str] = None

    def __post_init__(self) -> None:
        self.id = str(self.id)
        self.valor = to_decimal(self.valor)
        if self.chave:
            self.chave = chave_pix(self.chave)
        if self.cpf_cnpj:
            self.cpf_cnpj = normaliza_cpf_cnpj(self.cpf_cnpj)

    @property
    def rotulo(self) -> str:
        return self.descricao or f"pedido {self.id}"


@dataclass
class LinhaExtrato:
    """Uma entrada do extrato bancário."""

    data: str
    valor: Decimal
    identificador: str = ""
    chave: Optional[str] = None
    cpf_cnpj: Optional[str] = None
    descricao: str = ""
    e2eid: Optional[str] = None
    txid: Optional[str] = None

    def __post_init__(self) -> None:
        self.valor = to_decimal(self.valor)
        if self.chave:
            self.chave = chave_pix(self.chave)
        if self.cpf_cnpj:
            self.cpf_cnpj = normaliza_cpf_cnpj(self.cpf_cnpj)
        # garante que os dois campos casem mesmo se o chamador usar um só
        if not self.identificador and (self.chave or self.cpf_cnpj):
            self.identificador = self.cpf_cnpj or self.chave or ""


@dataclass
class Divergencia:
    """Uma linha que não fechou. Sempre diz o que fazer."""

    status: Status
    linha: LinhaExtrato
    pedido: Optional[Pedido] = None
    diferenca: Optional[Decimal] = None
    motivo: str = ""
    sugestao: str = ""

    def resumo(self) -> str:
        base = f"[{self.status.value}] {self.linha.descricao or self.linha.data}"
        if self.pedido:
            base += f" -> {self.pedido.rotulo} (#{self.pedido.id})"
        if self.diferenca is not None:
            base += f" | diferença R$ {self.diferenca}"
        return base + (f" | {self.sugestao}" if self.sugestao else "")

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "data": self.linha.data,
            "valor": str(self.linha.valor),
            "descricao": self.linha.descricao,
            "identificador": self.linha.identificador,
            "pedido_id": self.pedido.id if self.pedido else None,
            "valor_esperado": str(self.pedido.valor) if self.pedido else None,
            "diferenca": str(self.diferenca) if self.diferenca is not None else None,
            "motivo": self.motivo,
            "sugestao": self.sugestao,
        }


@dataclass
class Conciliacao:
    """Resultado da conciliação."""

    conciliados: list = field(default_factory=list)
    divergencias: list = field(default_factory=list)
    pedidos_sem_pagamento: list = field(default_factory=list)
    tolerancia: Decimal = TOLERANCIA_PADRAO

    @property
    def ok(self) -> bool:
        """Tudo bateu: nenhuma divergência e nenhum pedido sem pagamento."""
        return not self.divergencias and not self.pedidos_sem_pagamento

    def resumir(self) -> dict:
        nao_encontrado = sum(
            1 for d in self.divergencias if d.status is Status.PEDIDO_NAO_ENCONTRADO
        )
        nao_identificado = sum(
            1 for d in self.divergencias if d.status is Status.VALOR_NAO_IDENTIFICADO
        )
        total = sum(d.diferenca or Decimal("0") for d in self.divergencias
                    if d.status is Status.VALOR_DIVERGENTE)
        return {
            "ok": len(self.conciliados),
            "divergente": sum(1 for d in self.divergencias
                              if d.status is Status.VALOR_DIVERGENTE),
            "nao_encontrado": nao_encontrado,
            "valor_nao_identificado": f"{nao_identificado}",
            "pedidos_sem_pagamento": len(self.pedidos_sem_pagamento),
            "diferenca_total": f"{total}",
        }

    def resumo_txt(self) -> str:
        r = self.resumir()
        partes = [
            f"conciliados: {r['ok']}",
            f"divergentes: {r['divergente']}",
            f"sem pedido correspondente: {r['nao_encontrado']}",
            f"sem identificador: {r['valor_nao_identificado']}",
            f"pedidos não pagos: {r['pedidos_sem_pagamento']}",
        ]
        if r["diferenca_total"] not in ("0", "0.00", "-0"):
            partes.append(f"diferença total: R$ {r['diferenca_total']}")
        return " | ".join(partes)

    def to_dict(self) -> dict:
        return {
            "resumir": self.resumir(),
            "tolerancia": str(self.tolerancia),
            "divergencias": [d.to_dict() for d in self.divergencias],
            "pedidos_sem_pagamento": [
                {"id": p.id, "valor": str(p.valor), "rotulo": p.rotulo}
                for p in self.pedidos_sem_pagamento
            ],
        }


@dataclass
class Extrato:
    """Lista de linhas de extrato. Aceita CSV, dicionários ou LinhaExtrato."""

    linhas: list

    def __post_init__(self) -> None:
        normalizado = []
        for item in self.linhas:
            if isinstance(item, LinhaExtrato):
                normalizado.append(item)
            elif isinstance(item, dict):
                normalizado.append(LinhaExtrato(**item))
            else:
                raise TypeError(f"linha de extrato inválida: {type(item)}")
        self.linhas = normalizado

    def __len__(self) -> int:
        return len(self.linhas)

    def __iter__(self):
        return iter(self.linhas)

    @property
    def total(self) -> Decimal:
        return sum((l.valor for l in self.linhas), Decimal("0"))
