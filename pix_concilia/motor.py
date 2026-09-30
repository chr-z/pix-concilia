"""Motor de conciliação: casa cada linha do extrato com um pedido.

Ordem das regras, da mais forte para a mais fraca:

1. ``txid`` / ``e2eid`` exato — o banco te deu o identificador do pedido.
2. ``txid`` presente no texto da descrição do pedido.
3. CPF/CNPJ exato e valor dentro da tolerância.
4. Chave Pix (CPF, CNPJ, telefone ou e-mail) exata.
5. Valor único: existe UM pedido aberto com exatamente esse valor e nenhum
   outro. Só concilia nesse caso — dois pedidos do mesmo valor são ambíguos e
   conciliar no chute é pior do que reportar.
6. Nada casou: reportar. Nunca inventar.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional, Sequence

from .modelo import (
    Conciliacao,
    Divergencia,
    Extrato,
    LinhaExtrato,
    Pedido,
    Status,
    TOLERANCIA_PADRAO,
    chave_pix,
    normaliza_cpf_cnpj,
)


def _mesmo_valor(a: Decimal, b: Decimal, tolerancia: Decimal) -> bool:
    return abs(a - b) <= tolerancia


def _casa_por_valor(
    valor: Decimal, pendentes: Sequence[Pedido], tolerancia: Decimal
) -> Optional[Pedido]:
    """Só devolve o pedido se o valor for unívoco entre os pendentes."""
    candidatos = [p for p in pendentes if _mesmo_valor(p.valor, valor, tolerancia)]
    if len(candidatos) == 1:
        return candidatos[0]
    return None


def conciliar(
    extrato: Extrato | Iterable,
    pedidos: Iterable[Pedido | dict],
    *,
    tolerancia: Decimal = TOLERANCIA_PADRAO,
) -> Conciliacao:
    """Cruza as linhas do extrato com os pedidos.

    ``extrato`` aceita :class:`Extrato`, uma lista de :class:`LinhaExtrato` ou
    de dicionários. ``pedidos`` aceita :class:`Pedido` ou dicionários com as
    chaves ``id`` e ``valor``.
    """
    ex = extrato if isinstance(extrato, Extrato) else Extrato(list(extrato))

    pedidos_norm = []
    for p in pedidos:
        pedidos_norm.append(p if isinstance(p, Pedido) else Pedido(**p))

    resultado = Conciliacao(tolerancia=tolerancia)
    # índice de pedidos já casados, para um pedido não receber dois Pix
    casados: set = set()

    for linha in ex.linhas:
        achado = None

        # 1. txid / e2eid
        for token in filter(None, (linha.txid, linha.e2eid)):
            alvo = str(token).strip()
            for p in pedidos_norm:
                if p.id not in casados and (
                    alvo == p.id or alvo in p.descricao
                ):
                    achado = p
                    break
            if achado:
                break

        # 2. CPF/CNPJ
        if achado is None and linha.cpf_cnpj:
            doc = normaliza_cpf_cnpj(linha.cpf_cnpj)
            for p in pedidos_norm:
                if p.cpf_cnpj and p.id not in casados and p.cpf_cnpj == doc:
                    achado = p
                    break

        # 3. chave Pix
        if achado is None and linha.chave:
            alvo = chave_pix(linha.chave)
            for p in pedidos_norm:
                if p.chave and p.id not in casados and chave_pix(p.chave) == alvo:
                    achado = p
                    break

        if achado is None:
            pendentes = [p for p in pedidos_norm if p.id not in casados]
            achado = _casa_por_valor(linha.valor, pendentes, tolerancia)

        if achado is None:
            # sem identificador algum: nem dá para tentar
            tem_sinal = bool(linha.identificador or linha.txid or linha.e2eid)
            status = Status.PEDIDO_NAO_ENCONTRADO if tem_sinal else Status.VALOR_NAO_IDENTIFICADO
            resultado.divergencias.append(
                Divergencia(
                    status=status,
                    linha=linha,
                    sugestao=(
                        "confira se o pedido existe no sistema; se existe e o valor "
                        "bate, inclua o CPF/CNPJ ou o id do pedido no extrato"
                        if tem_sinal
                        else "Pix sem CPF/CNPJ e sem valor único: confirme com o cliente "
                        "antes de dar baixa"
                    ),
                    motivo=(
                        "identificador informado não corresponde a nenhum pedido"
                        if tem_sinal
                        else "sem identificador e o valor é ambíguo entre pedidos abertos"
                    ),
                )
            )
            continue

        casados.add(achado.id)
        if _mesmo_valor(achado.valor, linha.valor, tolerancia):
            resultado.conciliados.append((linha, achado))
        else:
            resultado.divergencias.append(
                Divergencia(
                    status=Status.VALOR_DIVERGENTE,
                    linha=linha,
                    pedido=achado,
                    diferenca=linha.valor - achado.valor,
                    sugestao=(
                        "confirme se houve desconto, frete ou taxa; ajuste o pedido "
                        "ou devolva a diferença"
                    ),
                    motivo="valor recebido difere do valor do pedido",
                )
            )

    resultado.pedidos_sem_pagamento = [
        p for p in pedidos_norm if p.id not in casados
    ]
    return resultado
