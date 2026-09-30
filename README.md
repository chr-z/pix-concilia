# pix-concilia

Concilia o **Pix que caiu na conta** contra **o que foi vendido**. Roda offline, no
seu servidor, sem cadastro e sem enviar dado de cliente para lugar nenhum.

## O problema

Empresa brasileira vende por Pix. O cliente paga, o banco notifica, e a conciliação
entre "o que caiu" e "o que foi vendido" é feita na mão: abrir planilha, colar o
extrato, cruzar com os pedidos. Quando dá diferença, alguém descobre dias depois.

Gateway de Pix até documenta o buraco: *"No automatic reconciliation: you receive
'loose Pix' without knowing which order."* — o Pix chega, mas você não sabe a qual
pedido ele pertence.

## Para quem é

- Quem vende por Pix e quer saber, todo dia, o que entrou e o que falta
- Quem já faz conciliação manual e erra
- Quem manda Pix como forma de pagamento e precisa dar baixa com segurança

## Instalação

Requer **Python 3.8+**. Zero dependências (só stdlib).

```bash
pip install pix-concilia
```

## Uso

```bash
# um extrato contra um arquivo de pedidos
pix-concilia extrato.csv --pedidos pedidos.csv

# em CI: sai com código 1 se houver divergência
pix-concilia extrato.csv --pedidos pedidos.csv --somente-erros

# saída em JSON
pix-concilia extrato.csv --pedidos pedidos.csv --json
```

Formato do extrato (detecta `;` e `,`, com ou sem acento no cabeçalho):

```csv
Data;Descricao;Chave Pix;CPF/CNPJ;Valor
29/09/2026;PAGAMENTO RECEBIDO;;111.222.333-44;150,00
```

Formato dos pedidos:

```csv
id;valor;cpf_cnpj;descricao
P1001;150,00;11122233344;Plano Pro
```

## API

```python
from pix_concilia import conciliar, Extrato, LinhaExtrato, Pedido

extrato = Extrato([
    LinhaExtrato("29/09/2026", "150,00", cpf_cnpj="11122233344"),
])
pedidos = [Pedido("P1001", "150,00", cpf_cnpj="11122233344")]

r = conciliar(extrato, pedidos)
r.ok          # True se tudo bateu
r.resumir()   # {'ok': 1, 'divergente': 0, ...}
r.to_dict()   # serializável para JSON
```

## Como a conciliação decide

Cada linha do extrato é casada com um pedido pela regra mais forte que funcionar:

1. **`txid` / `e2eid` exato** — o banco te deu o identificador do pedido
2. **`txid` no texto da descrição** do pedido
3. **CPF/CNPJ exato** com valor dentro da tolerância
4. **Chave Pix exata** (CPF, CNPJ, telefone ou e-mail, sem máscara)
5. **Valor único** — só se existe *um* pedido aberto com aquele valor
6. **Nada casou: reporta.** Nunca concilia no chute

Um pedido nunca recebe dois Pix. Dois pedidos do mesmo valor com um Pix é ambíguo por
definição: a ferramenta **não escolhe**, ela reporta para você decidir.

Tolerância padrão de **0,01** (um centavo), configurável. Todos os valores usam
`Decimal` — nunca `float`, porque dinheiro não aceita artefato de ponto flutuante.

## Saída real

```
conciliados: 2 | divergentes: 1 | sem pedido correspondente: 1 | sem identificador: 1 |
pedidos não pagos: 2 | diferença total: R$ -65.44

  [valor_divergente] PAGAMENTO RECEBIDO -> Consultoria (#P1003) | diferença R$ -65.44 |
  confirme se houve desconto, frete ou taxa; ajuste o pedido ou devolva a diferença
  [valor_nao_identificado] PAGAMENTO RECEBIDO | Pix sem CPF/CNPJ e sem valor único:
  confirme com o cliente antes de dar baixa
```

Cada divergência diz **o que fazer**, não só que algo deu errado.

## Limitações

- **Não lê o extrato do banco.** Você exporta o CSV e passa para cá. Integração com
  OpenPix, BSPay, Mercado Pago etc. fica por sua conta — este é o núcleo de
  conciliação, não o gateway.
- **Não emite Pix** e não substitui contabilidade.
- **Não é aconselhamento fiscal.**
- A detecção de cabeçalho cobre os nomes de coluna mais comuns de extrato brasileiro.
  Se o seu banco usar um nome muito específico, force com `--colunas data=1,valor=3`.

## Testes

```bash
python3 -m unittest discover -s tests -v
```

38 testes cobrem normalização de documento e chave, todas as regras de casamento,
fronteira de tolerância, leitura de CSV nos dois formatos, e um cenário de fim de dia
completo (3 fecham, 1 divergente, 1 sem pagamento, 1 Pix solto).

## Licença

MIT. Ver [LICENSE](LICENSE).
