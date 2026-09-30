"""Testes do pix-concilia. Executar: python3 -m unittest discover -s tests -v"""

import unittest
from decimal import Decimal

from pix_concilia import Extrato, LinhaExtrato, Pedido, Status, TOLERANCIA_PADRAO, conciliar
from pix_concilia.extrato import detectar_colunas, ler_csv
from pix_concilia.modelo import chave_pix, normaliza_cpf_cnpj, to_decimal


class TestNormalizacao(unittest.TestCase):
    def test_to_decimal_formato_br(self):
        self.assertEqual(to_decimal("1.234,56"), Decimal("1234.56"))
        self.assertEqual(to_decimal("R$ 89,90"), Decimal("89.90"))
        self.assertEqual(to_decimal(" 1.234.567,89 "), Decimal("1234567.89"))

    def test_to_decimal_formato_americano(self):
        self.assertEqual(to_decimal("1234.56"), Decimal("1234.56"))

    def test_to_decimal_aceita_float_sem_perder_centavo(self):
        # float normal seria 0.30000000000000004; o ponto e usar Decimal com
        # 2 casas, nunca Decimal(str(float)) que devolve o artefato inteiro.
        self.assertEqual(to_decimal(0.1 + 0.2), Decimal("0.30"))
        self.assertNotEqual(
            str(to_decimal(0.1 + 0.2)),
            "0.30000000000000004",
            "o artefato de float não pode vazar para o valor monetário",
        )

    def test_to_decimal_rejeita_texto(self):
        with self.assertRaises(ValueError):
            to_decimal("abc")

    def test_to_decimal_vazio(self):
        self.assertEqual(to_decimal(""), Decimal("0"))
        self.assertEqual(to_decimal(None), Decimal("0"))

    def test_cpf_cnpj_ignoram_mascara(self):
        self.assertEqual(normaliza_cpf_cnpj("123.456.789-09"), "12345678909")
        self.assertEqual(normaliza_cpf_cnpj("12.345.678/0001-95"), "12345678000195")

    def test_chave_pix_normaliza(self):
        self.assertEqual(chave_pix("123.456.789-09"), "12345678909")
        self.assertEqual(chave_pix("joao@exemplo.com.br"), "joao@exemplo.com.br")


class TestRegrasDeConciliacao(unittest.TestCase):
    def test_casa_por_cpf(self):
        ex = Extrato([LinhaExtrato("29/09/2026", "150,00", cpf_cnpj="123.456.789-09")])
        pedidos = [Pedido("P1", "150,00", cpf_cnpj="12345678909")]
        r = conciliar(ex, pedidos)
        self.assertTrue(r.ok)
        self.assertEqual(len(r.conciliados), 1)
        self.assertEqual(r.divergencias, [])

    def test_casa_por_txid(self):
        ex = Extrato([LinhaExtrato("29/09/2026", "99,90", txid="PED-777")])
        r = conciliar(ex, [Pedido("PED-777", "99,90")])
        self.assertTrue(r.ok)

    def test_casa_por_txid_dentro_da_descricao(self):
        ex = Extrato([LinhaExtrato("29/09/2026", "99,90", txid="PED-777")])
        pedidos = [Pedido("PED-777", "99,90", descricao="Pedido PED-777 pago via Pix")]
        self.assertTrue(conciliar(ex, pedidos).ok)

    def test_casa_por_chave_pix(self):
        ex = Extrato([LinhaExtrato("29/09/2026", "50,00", chave="11999999999")])
        r = conciliar(ex, [Pedido("P1", "50,00", chave="(11) 99999-9999")])
        self.assertTrue(r.ok)

    def test_casa_por_valor_univoco(self):
        """Um Pix de 150 bate com o único pedido de 150, mesmo sem CPF/CNPJ."""
        ex = Extrato([LinhaExtrato("29/09/2026", "150,00")])
        pedidos = [Pedido("P1", "150,00"), Pedido("P2", "80,00")]
        r = conciliar(ex, pedidos)
        self.assertEqual(len(r.conciliados), 1)
        self.assertEqual(r.conciliados[0][1].id, "P1")
        # P2 continua aberto: nenhum Pix cobriu os 80. Isso NÃO é erro —
        # é o retrato honesto do dia, e r.ok precisa refletir isso.
        self.assertEqual([p.id for p in r.pedidos_sem_pagamento], ["P2"])
        self.assertFalse(r.ok)
        self.assertEqual(r.divergencias, [])

    def test_ok_quando_tudo_bate(self):
        ex = Extrato([LinhaExtrato("29/09/2026", "150,00")])
        r = conciliar(ex, [Pedido("P1", "150,00")])
        self.assertTrue(r.ok)

    def test_valor_ambiguo_nao_concilia_no_chute(self):
        """Dois pedidos do mesmo valor e um Pix: NÃO pode escolher um."""
        ex = Extrato([LinhaExtrato("29/09/2026", "150,00")])
        pedidos = [Pedido("P1", "150,00"), Pedido("P2", "150,00")]
        r = conciliar(ex, pedidos)
        self.assertFalse(r.ok)
        self.assertEqual(len(r.conciliados), 0, "não pode escolher um dos dois no chute")
        self.assertEqual(len(r.pedidos_sem_pagamento), 2)
        # sem identificador e valor ambíguo -> não identificado (não achou pedido)
        self.assertEqual(len(r.divergencias), 1)
        self.assertIs(r.divergencias[0].status, Status.VALOR_NAO_IDENTIFICADO)

    def test_valor_diferente_para_divergencia(self):
        ex = Extrato([LinhaExtrato("29/09/2026", "150,00", cpf_cnpj="12345678909")])
        r = conciliar(ex, [Pedido("P1", "120,00", cpf_cnpj="12345678909")])
        self.assertFalse(r.ok)
        d = r.divergencias[0]
        self.assertIs(d.status, Status.VALOR_DIVERGENTE)
        self.assertEqual(d.diferenca, Decimal("30.00"))
        self.assertTrue(d.sugestao)

    def test_um_pedido_nao_recebe_dois_pix(self):
        ex = Extrato([
            LinhaExtrato("29/09/2026", "150,00", cpf_cnpj="12345678909"),
            LinhaExtrato("29/09/2026", "150,00", cpf_cnpj="12345678909"),
        ])
        r = conciliar(ex, [Pedido("P1", "150,00", cpf_cnpj="12345678909")])
        self.assertEqual(len(r.conciliados), 1)
        self.assertEqual(len(r.divergencias), 1)

    def test_pix_sem_identificador_e_valor_ambiguo(self):
        ex = Extrato([LinhaExtrato("29/09/2026", "70,00")])
        pedidos = [Pedido("P1", "70,00"), Pedido("P2", "70,00")]
        r = conciliar(ex, pedidos)
        self.assertTrue(
            any(d.status is Status.VALOR_NAO_IDENTIFICADO for d in r.divergencias)
        )

    def test_tolerancia_de_um_centavo_nao_e_erro(self):
        ex = Extrato([LinhaExtrato("29/09/2026", "150,01", cpf_cnpj="12345678909")])
        r = conciliar(ex, [Pedido("P1", "150,00", cpf_cnpj="12345678909")])
        self.assertTrue(r.ok, "1 centavo de diferença não pode gerar divergência")

    def test_dois_centavos_e_divergencia(self):
        ex = Extrato([LinhaExtrato("29/09/2026", "150,02", cpf_cnpj="12345678909")])
        r = conciliar(ex, [Pedido("P1", "150,00", cpf_cnpj="12345678909")])
        self.assertFalse(r.ok)

    def test_tolerancia_configuravel(self):
        ex = Extrato([LinhaExtrato("29/09/2026", "150,05", cpf_cnpj="12345678909")])
        r = conciliar(
            ex, [Pedido("P1", "150,00", cpf_cnpj="12345678909")],
            tolerancia=Decimal("0.10"),
        )
        self.assertTrue(r.ok)

    def test_pedidos_via_dicionario(self):
        ex = Extrato([{"data": "29/09/2026", "valor": "150,00", "cpf_cnpj": "12345678909"}])
        r = conciliar(ex, [{"id": "P1", "valor": "150,00", "cpf_cnpj": "12345678909"}])
        self.assertTrue(r.ok)

    def test_extrato_vazio(self):
        r = conciliar(Extrato([]), [Pedido("P1", "10,00")])
        self.assertEqual(len(r.pedidos_sem_pagamento), 1)
        self.assertTrue(r.ok is False)

    def test_resumir_tem_as_chaves(self):
        r = conciliar(
            Extrato([LinhaExtrato("29/09/2026", "10,00")]),
            [Pedido("P1", "10,00")],
        )
        s = r.resumir()
        for chave in ("ok", "divergente", "nao_encontrado", "valor_nao_identificado",
                      "pedidos_sem_pagamento", "diferenca_total"):
            self.assertIn(chave, s)

    def test_to_dict_e_serializavel(self):
        import json
        r = conciliar(
            Extrato([LinhaExtrato("29/09/2026", "10,00", cpf_cnpj="999")]),
            [Pedido("P1", "12,00", cpf_cnpj="999")],
        )
        json.dumps(r.to_dict())  # nao pode levantar


class TestLeituraDeExtrato(unittest.TestCase):
    def test_detecta_cabecalho_padrao(self):
        mapa = detectar_colunas(["Data", "Valor", "CPF/CNPJ", "Descrição"])
        self.assertEqual(mapa["data"], 0)
        self.assertEqual(mapa["valor"], 1)
        self.assertEqual(mapa["identificador"], 2)

    def test_detecta_cabecalho_bancario_real(self):
        mapa = detectar_colunas(
            ["Data do Lançamento", "Histórico", "Chave Pix", "Valor Pago"]
        )
        self.assertEqual(mapa["data"], 0)
        self.assertEqual(mapa["descricao"], 1)
        self.assertEqual(mapa["chave"], 2)
        self.assertEqual(mapa["valor"], 3)

    def test_cabecalho_desconhecido_falha_alto(self):
        with self.assertRaises(ValueError) as ctx:
            detectar_colunas(["col1", "col2"])
        self.assertIn("não reconheci", str(ctx.exception))

    def test_le_csv_pontvirgula_e_decimal_br(self):
        texto = (
            "Data;Descricao;Chave Pix;CPF/CNPJ;Valor\n"
            "29/09/2026;PAGAMENTO RECEBIDO;joao@exemplo.com;123.456.789-09;1.234,56\n"
        )
        linhas = ler_csv(texto)
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0].valor, Decimal("1234.56"))
        self.assertEqual(linhas[0].cpf_cnpj, "12345678909")
        self.assertEqual(linhas[0].chave, "joao@exemplo.com")

    def test_le_csv_aceita_mapa_forcado(self):
        # colunas: 0=data, 1=ruído, 2=valor
        texto = "29/09/2026;ruído;10,50\n"
        linhas = ler_csv(texto, mapa={"data": 0, "valor": 2}, delimitador=";")
        self.assertEqual(linhas[0].valor, Decimal("10.50"))
        self.assertEqual(linhas[0].data, "29/09/2026")

    def test_le_csv_pula_linha_vazia(self):
        texto = "Data;Valor\n29/09/2026;10,00\n\n; \n30/09/2026;20,00\n"
        self.assertEqual(len(ler_csv(texto)), 2)

    def test_le_csv_remove_bom(self):
        texto = "\ufeffData;Valor\n29/09/2026;10,00\n"
        self.assertEqual(len(ler_csv(texto)), 1)

    def test_linha_curta_falha_alto(self):
        """Linha mais curta que o cabeçalho não pode virar R$ 0,00 silencioso."""
        texto = "Data;Descricao;Chave Pix;CPF/CNPJ;Valor\n29/09/2026;PAGAMENTO;PAGO\n"
        with self.assertRaises(ValueError) as ctx:
            ler_csv(texto)
        self.assertIn("--colunas", str(ctx.exception))

    def test_linha_completa_usa_a_coluna_valor(self):
        texto = "Data;Descricao;Chave Pix;CPF/CNPJ;Valor\n29/09/2026;PAGAMENTO;;111.222.333-44;150,00\n"
        linhas = ler_csv(texto)
        self.assertEqual(linhas[0].valor, Decimal("150.00"))
        self.assertEqual(linhas[0].cpf_cnpj, "11122233344")

    def test_extrato_total(self):
        ex = Extrato([LinhaExtrato("1", "10,00"), LinhaExtrato("2", "20,50")])
        self.assertEqual(ex.total, Decimal("30.50"))
        self.assertEqual(len(ex), 2)

    def test_linha_invalida_falha(self):
        with self.assertRaises(TypeError):
            Extrato([123])


class TestCenarioFimDeDia(unittest.TestCase):
    """O dia real: 5 vendas, 4 Pix caíram, 1 pago a menos, 1 Pix solto."""

    def setUp(self):
        self.extrato = Extrato([
            LinhaExtrato("29/09/2026", "150,00", cpf_cnpj="111.222.333-44", txid="P1001"),
            LinhaExtrato("29/09/2026", "89,90", cpf_cnpj="555.666.777-88", txid="P1002"),
            LinhaExtrato("29/09/2026", "1.234,56", cpf_cnpj="999.888.777-66", txid="P1003"),
            LinhaExtrato("29/09/2026", "49,90", txid="P1004"),
            LinhaExtrato("29/09/2026", "70,00", cpf_cnpj="123.456.789-09"),
        ])
        self.pedidos = [
            Pedido("P1001", "150,00", cpf_cnpj="11122233344"),
            Pedido("P1002", "89,90", cpf_cnpj="55566677788"),
            Pedido("P1003", "1.300,00", cpf_cnpj="99988877766"),  # pago a menos
            Pedido("P1004", "49,90"),
            Pedido("P1005", "45,00"),  # ninguém pagou
        ]

    def test_resumo_do_dia(self):
        r = conciliar(self.extrato, self.pedidos)
        s = r.resumir()
        # 3 fecham limpo (P1001, P1002, P1004). P1003 casou pelo documento mas
        # o valor veio 65,44 a menos -> divergência. P1005 ninguém pagou.
        # O Pix solto de 70,00 não vira conciliação por sorteio.
        self.assertEqual(s["ok"], 3)
        self.assertEqual(s["divergente"], 1)
        self.assertEqual(s["nao_encontrado"], 1)
        self.assertEqual(s["pedidos_sem_pagamento"], 1)
        self.assertFalse(r.ok)

    def test_divergencia_aponta_o_pedido(self):
        r = conciliar(self.extrato, self.pedidos)
        div = [d for d in r.divergencias if d.status is Status.VALOR_DIVERGENTE]
        self.assertEqual(len(div), 1)
        self.assertEqual(div[0].pedido.id, "P1003")
        self.assertEqual(div[0].diferenca, Decimal("-65.44"))

    def test_pix_solto_nao_e_conciliado_por_chute(self):
        """O Pix de 70,00 não casa com nenhum pedido: precisa aparecer na lista."""
        r = conciliar(self.extrato, self.pedidos)
        soltos = [d for d in r.divergencias if d.status is Status.PEDIDO_NAO_ENCONTRADO]
        self.assertEqual(len(soltos), 1)
        self.assertEqual(soltos[0].linha.valor, Decimal("70.00"))
        self.assertTrue(soltos[0].sugestao)

    def test_pedido_nao_pago_e_apontado(self):
        r = conciliar(self.extrato, self.pedidos)
        ids = [p.id for p in r.pedidos_sem_pagamento]
        self.assertEqual(ids, ["P1005"])

    def test_resumo_txt_e_legivel(self):
        r = conciliar(self.extrato, self.pedidos)
        txt = r.resumo_txt()
        self.assertIn("conciliados: 3", txt)
        self.assertIn("divergentes: 1", txt)


if __name__ == "__main__":
    unittest.main()
