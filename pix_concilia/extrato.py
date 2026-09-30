"""Leitura de extrato: CSV, XLSX-lite e auto-detecção de layout de banco.

Extrato de Pix no Brasil não tem formato padrão. Cada banco exporta colunas
com nomes diferentes. Em vez de adivinhar, este módulo tenta casar o cabeçalho
com um conjunto de apelidos conhecidos e **falha alto** se não reconhecer nada —
um extrato lido errado e tratado como conciliado é pior que nenhum.
"""

from __future__ import annotations

import csv
import io
import re
from decimal import Decimal
from typing import Iterable, Optional

from .modelo import LinhaExtrato, to_decimal

# apelidos de coluna, sem acento e em minúsculo
APELIDOS = {
    "data": {"data", "data mov", "datamovimento", "data movimento", "data pagto",
             "data pagamento", "data da transacao", "dt", "date", "data lancamento"},
    "valor": {"valor", "valor pago", "valor pagamento", "valor liquidado", "montante",
              "valor pix", "valor da transacao", "vlr"},
    "identificador": {"cpf cnpj", "cpf/cnpj", "cpfcnpj", "documento", "doc",
                      "identificador", "identificacao"},
    "chave": {"chave", "chave pix", "chavepix", "chave de acesso", "chavedestino",
              "destinatario", "chave recebedor"},
    "descricao": {"descricao", "historico", "histórico", "lancamento", "movimentacao",
                  "historico completo", "descricao do lancamento", "observacao"},
    "e2eid": {"e2eid", "e2e id", "endtoendid", "end to end id", "e2e"},
    "txid": {"txid", "tx id", "transactionid", "transaction id", "identificador da transacao"},
}

def _norm(chave: str) -> str:
    """minúsculas, sem acento, espaços colapsados — para comparar cabeçalhos."""
    import unicodedata
    n = unicodedata.normalize("NFKD", (chave or "").lower())
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", n).strip()


# os apelidos também precisam ser normalizados: "Histórico" e "Historico" são
# o mesmo cabeçalho, e todo extrato de banco brasileiro usa acento em algum campo.
_APELIDO_INV = {k: {_norm(a) for a in v} for k, v in APELIDOS.items()}


def detectar_colunas(cabecalho: Iterable[str]) -> dict:
    """Mapeia cabeçalho -> nome canônico. Levanta ValueError se faltar data ou valor.

    Compara sempre em minúsculas e sem acento: "Histórico", "Historico" e
    "HISTORICO" são o mesmo cabeçalho. Todo extrato de banco brasileiro usa
    acento em pelo menos um campo, então normalizar antes de casar não é
    detalhe — sem isso o validador rejeita o próprio arquivo que veio do banco.
    """
    cabecalho = [str(c) for c in cabecalho]
    mapa: dict = {}
    for idx, bruto in enumerate(cabecalho):
        n = _norm(bruto)
        if not n:
            continue
        for canonico, apelidos in _APELIDO_INV.items():
            if canonico in mapa:
                continue
            # exato primeiro: "valor" não pode casar com "valor pago" por engano
            if n in apelidos:
                mapa[canonico] = idx
                break
    # segunda passada: substring, só para o que sobrou. Extrato de banco tem
    # nomes de coluna que nenhum catálogo antecipa ("Data do Lançamento
    # operation", "Histórico da operação"), e rejeitar o arquivo por causa de
    # um sufixo desconhecido é pior do que casar pelo termo principal.
    for idx, bruto in enumerate(cabecalho):
        n = _norm(bruto)
        if not n:
            continue
        for canonico, apelidos in _APELIDO_INV.items():
            if canonico in mapa:
                continue
            for apelido in apelidos:
                if len(apelido) >= 4 and apelido in n:
                    mapa[canonico] = idx
                    break
    faltando = [c for c in ("data", "valor") if c not in mapa]
    if faltando:
        raise ValueError(
            "não reconheci as colunas "
            + ", ".join(faltando)
            + f". Cabeçalho lido: {list(cabecalho)}. "
            "Use --colunas data=1,valor=3,identificador=2 para forçar."
        )
    return mapa


def _detectar_delimitador_e_decimal(texto: str) -> tuple:
    """Descobre o separador e se o decimal é vírgula.

    Sem amostra, isto é uma adivinhação — e adivinhar errado num extrato
    financeiro é pior do que pedir para o usuário informar. Por isso a regra é
    conservadora: só assumo decimal-vírgula quando o cabeçalho tem ponto-e-vírgula
    (que nenhum CSV americano usa como separador de campo).
    """
    linhas = [l for l in texto.splitlines() if l.strip()]
    if not linhas:
        return ",", True
    cab = linhas[0]
    ponto_virgula = cab.count(";")
    virgula = cab.count(",")

    if ponto_virgula and ponto_virgula >= virgula:
        return ";", True
    if virgula:
        # separador é vírgula. Se algum valor tem vírgula seguida de 2 dígitos
        # e nada antes, pode ser decimal brasileiro num CSV malformado.
        amostra = "\n".join(linhas[1:4])
        if re.search(r"\d,\d{2}\b", amostra) and not re.search(r"\d\.\d{2}\b", amostra):
            return ",", True
        return ",", False
    return ",", True


def ler_csv(
    texto: str,
    *,
    mapa: Optional[dict] = None,
    delimitador: str = None,
    decimal_virgula=None,
) -> list[LinhaExtrato]:
    """Lê um extrato CSV. Detecta ';' vs ',' e o separador decimal."""
    texto = texto.lstrip("\ufeff")
    mapa_passado = mapa is not None
    if delimitador is None or decimal_virgula is None:
        d_auto, dv_auto = _detectar_delimitador_e_decimal(texto)
        delimitador = delimitador or d_auto
        decimal_virgula = dv_auto if decimal_virgula is None else decimal_virgula

    leitor = csv.reader(io.StringIO(texto), delimiter=delimitador)
    linhas = list(leitor)
    if not linhas:
        return []

    mapa, inicio = (mapa, 0) if mapa_passado else (detectar_colunas(linhas[0]), 1)
    cabecalho = linhas[0] if inicio else None
    out = []
    for n_linha, linha in enumerate(linhas[inicio:], start=inicio + 1):
        if not any((c or "").strip() for c in linha):
            continue
        def pega(canonico, padrao=""):
            i = mapa.get(canonico)
            if i is None or i >= len(linha):
                return padrao
            return (linha[i] or padrao).strip()

        # Linha mais curta que o cabeçalho é arquivo de banco com coluna a
        # menos — e adivinhar onde está o valor pode ler um Pix como R$ 0,00.
        # Falhar alto é o comportamento certo aqui: silenciosamente zerar um
        # pagamento é exatamente o bug que a ferramenta existe para evitar.
        # Só vale quando existe cabeçalho; com --colunas quem chama sabe as
        # posições e uma linha curta é válida por definição.
        if cabecalho and len(linha) < len(cabecalho):
            raise ValueError(
                f"linha {n_linha} tem {len(linha)} campos e o cabeçalho tem "
                f"{len(cabecalho)}: o valor pode estar numa coluna a menos. "
                "Use --colunas data=1,valor=3 para indicar as posições."
            )

        valor_txt = pega("valor").replace("R$", "").replace(" ", "")
        if decimal_virgula and "." in valor_txt and "," in valor_txt:
            valor_txt = valor_txt.replace(".", "").replace(",", ".")
        elif not decimal_virgula and "," in valor_txt and "." not in valor_txt:
            valor_txt = valor_txt.replace(",", ".")

        # A coluna de documento alimenta cpf_cnpj, não identificador. Enfiar tudo em
        # identificador deixava cpf_cnpj sempre None, e a conciliação por documento
        # (que é a mais confiável que existe) nunca rodava.
        doc = pega("identificador")
        chave_txt = pega("chave") or None

        out.append(
            LinhaExtrato(
                data=pega("data"),
                valor=to_decimal(valor_txt),
                identificador=doc,
                cpf_cnpj=doc or None,
                chave=chave_txt,
                descricao=pega("descricao"),
                e2eid=pega("e2eid") or None,
                txid=pega("txid") or None,
            )
        )
    return out


def carregar(caminho: str, *, mapa: Optional[dict] = None) -> list[LinhaExtrato]:
    """Lê um extrato de arquivo (CSV)."""
    if caminho == "-":
        import sys
        texto = sys.stdin.read()
    else:
        with open(caminho, encoding="utf-8", errors="replace") as f:
            texto = f.read()
    return ler_csv(texto, mapa=mapa)
