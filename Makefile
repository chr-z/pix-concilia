.PHONY: test demo clean

PY ?= python3

test:
	$(PY) -m unittest discover -s tests -v

demo:
	@printf 'Data;Descricao;Chave Pix;CPF/CNPJ;Valor\n' > /tmp/pc-extrato.csv
	@printf '29/09/2026;PAGAMENTO RECEBIDO;;111.222.333-44;150,00\n' >> /tmp/pc-extrato.csv
	@printf '29/09/2026;PAGAMENTO RECEBIDO;;555.666.777-88;89,90\n' >> /tmp/pc-extrato.csv
	@printf '29/09/2026;PAGAMENTO RECEBIDO;;999.888.777-66;1.234,56\n' >> /tmp/pc-extrato.csv
	@printf '29/09/2026;PAGAMENTO RECEBIDO;;;49,90\n' >> /tmp/pc-extrato.csv
	@printf '29/09/2026;PAGAMENTO RECEBIDO;;123.456.789-09;70,00\n' >> /tmp/pc-extrato.csv
	@printf 'id;valor;cpf_cnpj;descricao\n' > /tmp/pc-pedidos.csv
	@printf 'P1001;150,00;11122233344;Plano Pro\n' >> /tmp/pc-pedidos.csv
	@printf 'P1002;89,90;55566677788;Plano Basico\n' >> /tmp/pc-pedidos.csv
	@printf 'P1003;1300,00;99988877766;Consultoria\n' >> /tmp/pc-pedidos.csv
	@printf 'P1004;49,90;;Upgrade avulso\n' >> /tmp/pc-pedidos.csv
	@printf 'P1005;45,00;;Plano Eco\n' >> /tmp/pc-pedidos.csv
	$(PY) -m pix_concilia /tmp/pc-extrato.csv --pedidos /tmp/pc-pedidos.csv

clean:
	find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
