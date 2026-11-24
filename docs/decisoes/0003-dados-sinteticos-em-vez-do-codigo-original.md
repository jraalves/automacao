# ADR 0003 — Publicar dados sintéticos em vez do código original

**Status:** aceito (é a regra desta demonstração inteira)

**Data de referência:** 2026-01-24

**Escopo:** `tools/gen_data.py`, `data/sinteticos.json`, `data/dados.js`,

`data/AVISO-SINTETICO.md`, `tools/leak_check.py`

## Contexto

O código desta aplicação foi escrito sob contrato. Ele não pode ser publicado:

contém nomes de clientes, domínios corporativos, caminhos reais de arquivos,

credenciais de serviço, consultas que revelam esquema de dados fiscais de

terceiros e — o mais sensível — chaves de acesso de documentos fiscais reais,

que são identificadores de terceiros, não números meus para divulgar.

Ao mesmo tempo, um portfólio em que a frase é "fiz uma automação fiscal grande,

confie em mim" não vale nada. A alternativa a publicar o código tem que provar

competência de verdade, e competência aqui está em três lugares: como o sistema

foi desenhado, como ele falha, e como isso foi medido.

## Decisão

1. **Publicar o desenho e a interface, com dados 100% sintéticos.** As empresas,

   documentos, traces, logs, métricas, incidentes e workflows desta página são
   produzidos por `tools/gen_data.py` a partir de uma seed fixa (`20260124`).

2. **Reproducibilidade byte a byte.** Mesma seed, mesma saída, em qualquer

   máquina e em qualquer data: o gerador usa um instante fixo (`BASE`), não o
   relógio. O CI roda `python3 tools/gen_data.py --check` e compara com o que
   está versionado.

3. **Prefixos que não fingem ser reais.** `EMP-DEMO-*`, `DOC-FISCAL-SINT-*`,

   `NF-DEMO-*`, `REQ-DEMO-*`, `INC-DEMO-*`, hosts `*.example.com`, namespace
   `automacao-demo`, imagens com sufixo `-SINT`. Chave de acesso de documento
   fiscal **não existe** aqui: o campo aparece como "ocultada nesta demonstração".

4. **Varredura automática anti-vazamento no CI.** `tools/leak_check.py` barra:

   nomes de clientes/empregadores/produtos, números com formato e dígito
   verificador de CNPJ/CPF (e sequências ambíguas de 11/14 dígitos), e-mail fora
   de `example.com`/`example.org`, IP fora de localhost/faixa de exemplo, token
   com formato conhecido, chave privada, credencial com valor não-placeholder,
   URL fora da allow list e nome de arquivo que jamais deveria entrar.

5. **Rotular o que é desenho e o que é execução.** A página diz, em três lugares

   (faixa fixa, aba Sobre, `data/AVISO-SINTETICO.md`), que nada é executado pelo
   site: não há API, banco, fila, Prometheus ou repositório de arquivos por trás
   do `index.html`.

6. **Números de escala são ordem de grandeza, não medição.** A tabela

   "escala — ordem de grandeza ilustrativa" existe para discutir arquitetura. Ela
   não dimensiona nada e diz isso no próprio texto.

## Alternativas rejeitadas

- **Publicar o código com busca-e-substituição.** Rejeitada logo. Redação de

  contrato não autoriza, e anonimato por substituição textual é frágil: fica o
  formato dos identificadores, o padrão de nomenclatura de pastas, o esquema de
  banco, os comentários de ticket interno. Um único campo esquecido vira vazamento
  de dado de terceiro.

- **Publicar só um artigo/diagrama.** Honesto, e é o que quase todo portfólio faz.

  Não prova a parte que me interessa provar: instrumentação funcionando, correlação
  de request/trace, burn rate com limiar, manifests que aplicam o desenho.

- **Montar um ambiente real de demonstração (com login, banco e filas).** Prazo e

  custo desproporcionais para um case de portfólio, e criaria uma superfície
  exposta que eu teria que proteger para sempre. A página é estática de propósito.

## Consequências

**Custo aceito.** Nada aqui prova performance em ambiente real, e a página assume

isso. O leitor que quiser números de produção não vai encontrá-los — e é melhor

assumir isso na primeira tela do que deixar a impressão de que houve execução.

Também tenho trabalho extra: manter gerador, verificador e comparação de byte a

byte no CI.

**Ganhos.** Posso abrir a interface, os manifests, as regras de alerta, os ADRs e

a forma de medir sem violar nada; o case fica verificável por quem lê (rodar o

gerador e o validador leva segundos); e a política de privacidade deixa de ser

declaração para ser checagem automática.

**O que fica mais difícil.** Resistir à tentação de "embelezar" com um número real

ou um nome real. Toda métrica nova nesta demo precisa nascer no gerador, com a

fórmula registrada — se eu não consigo gerar, eu não publico.

## Como medir

| Checagem | Comando | Resultado esperado |
| --- | --- | --- |
| Reprodutibilidade | `python3 tools/gen_data.py --check` | byte a byte idêntico |
| Paridade JSON × fallback JS | `python3 tools/validate.py` | `sinteticos.json` == corpo de `dados.js` |
| Anti-vazamento | `python3 tools/leak_check.py` | zero achados, código de saída 0 |
| Estrutura do desenho | `python3 tools/validate.py` | zero erros em YAML, JSON, links e vistas |

## Referências cruzadas

`data/AVISO-SINTETICO.md` · `tools/gen_data.py` · `tools/leak_check.py` ·

`tools/validate.py` · `.github/workflows/deploy.yml` ·

aba "Sobre o case" da interface (`index.html`)
