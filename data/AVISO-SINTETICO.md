# Aviso: todos os dados deste diretório são sintéticos

Os dois arquivos aqui publicados — `sinteticos.json` e `dados.js` — **não**

contêm dado de cliente, de empregador, de empresa real, de ambiente real ou de

pessoa real. Foram gerados por script, a partir de uma seed fixa, para que esta

demonstração mostre interface, fluxo, observabilidade e arquitetura sem publicar

código confidencial. A decisão está registrada em

`docs/decisoes/0003-dados-sinteticos-em-vez-do-codigo-original.md`.

## Os dois arquivos e por que existem dois

| Arquivo | Consumido por | Conteúdo |
| --- | --- | --- |
| `sinteticos.json` | `app.js` via `fetch()` quando o site roda por HTTP | o corpo JSON puro |
| `dados.js` | navegador quando `index.html` é aberto por `file://` | `window.DEMO_DATA = <mesmo JSON>` |

O `fetch` de um JSON é bloqueado pela política de origem quando a página é

aberta direto do disco; por isso os dois arquivos existem e o corpo JSON é

**idêntico byte a byte** entre eles. O `tools/validate.py` compara os dois a

cada execução e o CI também.

## Como reproduzir

```bash
# regenera os dois arquivos (escrita em data/)
python3 tools/gen_data.py

# compara com o que está versionado, sem escrever nada (modo do CI)
python3 tools/gen_data.py --check

# imprime o JSON no terminal para inspecionar
python3 tools/gen_data.py --stdout | head -40

# outra seed (mostra que a saída muda: não use para commitar)
python3 tools/gen_data.py --seed 20269999 --out /tmp

```

Requisito: Python 3.10+ com a biblioteca padrão. Nenhuma dependência externa,

nenhuma chamada de rede, nenhuma variável de ambiente.

## Por que a saída é sempre a mesma

Determinismo não é sorte, é restrição. Quatro regras no `tools/gen_data.py`:

1. **Seed fixa: `20260124`.** Cada bloco usa sua própria instância

   (`random.Random(SEED + n)`), então mudar um bloco não reembaralha os outros.

2. **Relógio congelado.** A constante `BASE` é

   `2026-01-24T12:00:00-03:00` e é o único "agora" usado. Nenhuma data sai do
   `datetime.now()`, então a saída não muda amanhã nem em outro fuso.

3. **Serialização canônica.** `json.dumps(..., ensure_ascii=False, indent=2)` e

   uma quebra de linha final: chave alguma depende de ordem de `dict` aleatória.

4. **Números com fórmula registrada.** Taxa de erro, queima de orçamento,

   cobertura, p50/p95 e MTTR saem de contas explícitas sobre a série horária de
   26 pontos — a fórmula aparece no próprio JSON
   (`error_budget.formula`) e no comentário do painel Grafana.

Ordem dos blocos gerados (`montar()`): `aviso`, `identidade`, `autenticacao`,

`empresas` (8), `tipos_documento` (6), `status_extracao` (5), `repositorio`,

`schema_extracao`, `indicadores`, `documentos` (40), `tracos` (40), `pipeline`

(6 etapas), `workflows` (8), `serie_oraria` (26), `burn_rate` (26),

`error_budget`, `cobertura_por_empresa` (8), `slis` (4), `incidentes` (8),

`escala_ilustrativa`.

## O que é sintético aqui

* **Identificadores** com prefixo que não finge ser real: `EMP-DEMO-0001` a

  `EMP-DEMO-0008`, `DOC-FISCAL-SINT-000001`, `NF-DEMO-*`, `WF-DEMO-2026-*`,
  `REQ-DEMO-*`, `INC-DEMO-01` a `INC-DEMO-08`, `ERR-SINT-*`.

* **Nomes de empresa** rotulados como fictícios

  ("Aurora Exemplo Ltda (fictícia)", "Cisne Serviços Sintéticos Ltda").

* **Chave de acesso de documento fiscal:** não existe no arquivo. O campo

  `chave_acesso` traz a string `"ocultada nesta demonstração"`, porque chave de
  acesso real é identificador de terceiro.

* **CNPJ e CPF:** não existem. O campo `cnpj` de empresa carrega a frase

  explicativa, e o verificador barra qualquer sequência de 11 ou 14 dígitos — as
  strings aleatórias geradas são construídas sem sequências longas de dígitos por
  causa disso.

* **Caminhos de arquivo** sob `/Demonstracao-Fiscal/` em

  `repositório de arquivos corporativo (M365 Drive-like, genérico)`: descrevem o
  padrão de organização, não um tenant, domínio ou usuário.

* **Datas** todas ancoradas em `BASE`, com janelas de dias/horas da seed.
* **Métricas, traces, logs e incidentes**: existem para demonstrar a forma da

  correlação (`trace_id` ↔ `request_id`) e o cálculo de queima de orçamento. Não
  são medição de ambiente nenhum.

## O que é desenho documentado (e por isso vale ler)

* A estrutura das etapas: descoberta → ingestão → extração → validação de schema

  → publicação → conferência humana, com a trilha de auditoria de cada documento
  parando na etapa em que o status manda.

* Os nomes de métrica (`documentos_publicados_total`,

  `extracoes_total{resultado=...}`, `extracao_duracao_seconds_bucket`,
  `http_server_duration_milliseconds_count`, `pipeline_etapa_documentos`) que
  aparecem em `observabilidade/prometheus-rules.yml` e no painel
  `observabilidade/dashboards/grafana-demo.json`.

* Os limiares de queima (14,4x em 1 h, 6x em 6 h, 1x em 24 h) usados iguais no

  JSON, nas regras e em `observabilidade/alertmanager-routing.yml`.

Nada disso é executado pelo site: não há Prometheus, Loki, Tempo, banco ou

repositório atrás desta página.

## Se você for reusar isto

* Rodar `python3 tools/gen_data.py` com a seed padrão deve dar saída **idêntica**

  ao que está versionado. Se divergir, algo mudou no gerador: rode
  `--check` e compare.

* Ao trocar nomes, valores ou adicionar campos, rode `python3 tools/leak_check.py`

  e `python3 tools/validate.py` antes de commitar. O gerador já passa o texto
  gerado pelo verificador anti-vazamento antes de escrever em disco, e se recusa
  a gravar saída que case com alguma regra.

* A licença do código é MIT (`LICENSE`). Os dados deste diretório são fictícios:

  não representam nenhuma empresa, órgão, documento ou pessoa.
