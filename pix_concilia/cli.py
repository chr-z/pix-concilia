"""CLI do pix-concilia."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from typing import Optional

from . import __version__
from .extrato import carregar, detectar_colunas
from .modelo import Extrato, Pedido, Status, TOLERANCIA_PADRAO
from .motor import conciliar

VERMELHO = "\033[31m"
AMARELO = "\033[33m"
VERDE = "\033[32m"
CINZA = "\033[90m"
RESET = "\033[0m"


def _cor(codigo: str, texto: str, usar_cor: bool) -> str:
    if not usar_cor:
        return texto
    if codigo == Status.CONCILIADO.value:
        return VERDE + texto + RESET
    if codigo == Status.VALOR_NAO_IDENTIFICADO.value:
        return CINZA + texto + RESET
    return VERMELHO + texto + RESET


def _detectar_delimitador(caminho: str) -> str:
    """Arquivo de pedidos brasileiro quase sempre vem com ';' justamente porque
    o valor usa vírgula como decimal. Mas o export de uma planilha costuma vir
    com ',' e ponto decimal — os dois existem e ambos são legítimos."""
    with open(caminho, encoding="utf-8", errors="replace") as f:
        primeira = f.readline()
    if ";" in primeira and "," in primeira:
        # ambos: o que se repete em todas as linhas é o separador
        return ";"
    if ";" in primeira:
        return ";"
    return ","


def _pedidos_de(args) -> list:
    pedidos = []
    if args.pedidos:
        delim = _detectar_delimitador(args.pedidos)
        with open(args.pedidos, encoding="utf-8", errors="replace") as f:
            leitor = csv.DictReader(f, delimiter=delim)
            for n, linha in enumerate(leitor, start=2):
                if not any((v or "").strip() for v in linha.values()):
                    continue
                pid = (linha.get("id") or linha.get("pedido") or "").strip()
                if not pid:
                    raise ValueError(
                        f"{args.pedidos}:{n}: linha sem 'id'. "
                        f"Colunas encontradas: {list(linha)}"
                    )
                try:
                    valor = linha.get("valor") or linha.get("amount") or "0"
                except Exception as exc:  # pragma: no cover
                    raise ValueError(f"{args.pedidos}:{n}: valor ilegível") from exc
                pedidos.append(
                    Pedido(
                        id=pid,
                        valor=valor,
                        chave=(linha.get("chave") or "").strip() or None,
                        cpf_cnpj=(linha.get("cpf_cnpj") or linha.get("documento") or "").strip() or None,
                        descricao=(linha.get("descricao") or linha.get("obs") or "").strip(),
                    )
                )
    for spec in (args.pedido or []):
        # aceita "ID=VALOR" e "ID=VALOR:chave=CPF"
        cabeca, _, resto = spec.partition("=")
        if not resto:
            raise ValueError(f"pedido inválido (use ID=VALOR): {spec!r}")
        valor_txt, _, extra = resto.partition(":")
        chave = cpf = None
        for par in extra.split("+"):
            if par.startswith("chave="):
                chave = par.split("=", 1)[1]
            elif par.startswith("cpf="):
                cpf = par.split("=", 1)[1]
        pedidos.append(
            Pedido(id=cabeca.strip(), valor=valor_txt.strip(), chave=chave, cpf_cnpj=cpf)
        )
    return pedidos


def _parse_colunas(txt: Optional[str]) -> Optional[dict]:
    if not txt:
        return None
    mapa = {}
    for parte in txt.split(","):
        if "=" in parte:
            k, _, v = parte.partition("=")
            try:
                mapa[k.strip()] = int(v)
            except ValueError:
                raise SystemExit(f"mapa de colunas inválido: {parte!r}")
    return mapa or None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="pix-concilia",
        description="Concilia Pix recebido contra o que foi vendido. Roda offline.",
    )
    p.add_argument("extrato", help="CSV do extrato, ou - para stdin")
    p.add_argument("--pedidos", help="CSV com id,valor[,chave,cpf_cnpj]")
    p.add_argument(
        "--pedido",
        action="append",
        metavar="ID=VALOR",
        help="pedido na linha de comando, repetível",
    )
    p.add_argument("--colunas", help="força o mapa: data=1,valor=3,identificador=2")
    p.add_argument(
        "--tolerancia", default=str(TOLERANCIA_PADRAO), help=f"padrão {TOLERANCIA_PADRAO}"
    )
    p.add_argument("--json", action="store_true", help="saída em JSON")
    p.add_argument("--apenas-divergencias", action="store_true")
    p.add_argument("--somente-erros", action="store_true", help="código de saída 1 se houver")
    p.add_argument("--version", action="version", version=f"pix-concilia {__version__}")
    args = p.parse_args(argv)

    cor = sys.stdout.isatty()
    try:
        tolerancia = Decimal(args.tolerancia)
    except InvalidOperation:
        p.error(f"tolerância inválida: {args.tolerancia}")

    mapa = _parse_colunas(args.colunas)
    try:
        linhas = carregar(args.extrato, mapa=mapa)
    except (OSError, ValueError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2

    if not linhas:
        print("erro: extrato sem linhas", file=sys.stderr)
        return 2

    try:
        pedidos = _pedidos_de(args)
    except (OSError, TypeError) as exc:
        print(f"erro ao ler pedidos: {exc}", file=sys.stderr)
        return 2

    if not pedidos:
        if args.json:
            print(json.dumps(
                {"resumir": conciliar(Extrato(linhas), []).resumir(),
                 "aviso": "nenhum pedido informado; nada pode ser conciliado"},
                ensure_ascii=False, indent=2))
        else:
            print("aviso: nenhum pedido informado — só posso apontar o que sobrou no extrato")
        r = conciliar(Extrato(linhas), [])
    else:
        r = conciliar(Extrato(linhas), pedidos, tolerancia=tolerancia)

    if args.json:
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(r.resumo_txt())
        if r.divergencias and not args.apenas_divergencias:
            print()
            for d in r.divergencias:
                print("  " + _cor(d.status.value, d.resumo(), cor))
        if r.pedidos_sem_pagamento and not args.apenas_divergencias:
            print()
            print("  pedidos sem pagamento:")
            for ped in r.pedidos_sem_pagamento:
                print(f"    #{ped.id}  R$ {ped.valor}  {ped.rotulo}")

    return 1 if (args.somente_erros and not r.ok) else 0


if __name__ == "__main__":
    raise SystemExit(main())
