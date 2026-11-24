# Automação de gestão fiscal — demonstração

Interface, pipeline, observabilidade e arquitetura de um caso real de automação

fiscal, **recontados com dados sintéticos**. Site publicado:

<https://jraalves.github.io/automacao/> · código:

<https://github.com/jraalves/automacao>

> **Demonstração, não produto.** Nada aqui roda um backend. A página é estática,

> consome um JSON gerado por script e não faz nenhuma chamada de rede.

> **Estado do deploy:** este repositório é a fonte; o site é servido pelo GitHub Pages a partir
> da branch `main` na raiz. Se a URL abaixo responder 404, o Pages ainda está desligado neste
> repositório — ligar é Settings → Pages → *Deploy from a branch* → `main` / `(root)` → Save
> (ou rodar o workflow de `ci/github-actions-deploy.yml`).

## O endereço: por que `jraalves.github.io/automacao` e não `automacao.github.io`

1. No GitHub Pages, a forma raiz do domínio pertence à conta que tem aquele nome

   de usuário: `automacao.github.io` só existiria se existisse a conta `automacao`,
   e o endereço de um projeto é sempre `usuário.github.io/nome-do-repositorio`.

2. Esse nome de usuário já está ocupado por outra conta — então tentar apontar para

   `automacao.github.io` significaria depender de um endereço que eu não controlo,
   não posso reservar e não posso garantir que continue existindo.

3. O caminho versionado dentro do próprio usuário é a forma controlável e permanente:

   vive no meu repositório, muda comigo quando o repositório muda, é publicado pelo
   mesmo pipeline deste site e não depende de domínio comprado nem de DNS de terceiro.

## O que é esta demonstração

O case original: uma aplicação de **busca, recuperação e gestão de documentos

fiscais** (NF-e, NFC-e, CT-e, MDF-e, NFS-e, CC-e) armazenados num repositório de

arquivos corporativo. O pipeline descobre o arquivo, ingere, extrai campos do

leiaute fiscal (com OCR quando o PDF não tem camada de texto), valida contra

esquema e regras, publica no destino com nome padronizado e deixa o que precisa de

revisão numa fila de conferência humana.

Este repositório publica o que pode ser publicado:

* **a interface** — seis vistas em HTML, CSS e JavaScript puro, sem `npm`, sem

  build e sem dependência externa (`index.html`, `app.js`, `styles.css`);

* **os dados de demonstração** — empresas, documentos, traces, logs, métricas,

  workflows e incidentes gerados por `tools/gen_data.py` com seed fixa;

* **o desenho de produção** — manifests Kubernetes (`k8s/`), regras de SLI e

  alertas, roteamento de Alertmanager, configuração do Collector OTel, consultas
  LogQL e um painel Grafana versionado (`observabilidade/`);

* **as decisões** — três ADRs com contexto, custo aceito e como medir

  (`docs/decisoes/`).

O que **não** é publicado: o código do cliente, nomes de clientes e ambientes,

domínios internos, credenciais, caminhos reais de arquivo e qualquer chave de

acesso de documento fiscal. Motivo e alternativa em

[`docs/decisoes/0003-dados-sinteticos-em-vez-do-codigo-original.md`](docs/decisoes/0003-dados-sinteticos-em-vez-do-codigo-original.md).

## Como rodar local

Pré-requisito: um navegador. Para as ferramentas, Python 3.10+ com a biblioteca

padrão (nada para instalar).

```bash
# 1) abrir direto do disco (funciona: o app cai no fallback data/dados.js)
xdg-open index.html          # ou: open index.html

# 2) servir por HTTP (aí o fetch de data/sinteticos.json é usado)
python3 -m http.server 8080  # depois abra http://localhost:8080/

```

Sem servidor, o `fetch` do JSON é bloqueado pela política de origem do

`file://`; nesse caso `app.js` usa `window.DEMO_DATA` definido em

`data/dados.js` — o mesmo corpo JSON, byte a byte. A faixa no rodapé da página

informa de onde os dados vieram.

Validação (o que o CI roda):

```bash
python3 tools/gen_data.py --check     # os dados versionados batem com o gerador?
python3 tools/validate.py             # JSON, YAML, links internos, vistas, painel
python3 tools/leak_check.py           # varredura anti-vazamento (termos, CPF/CNPJ, IPs, URLs)
node --check app.js                   # sintaxe do JavaScript (se node existir)
node tools/teste_render.js            # as seis vistas montam sem exceção (DOM falso)

```

## As seis vistas da interface

| # | Vista | O que você faz nela | O que ela prova |
| --- | --- | --- | --- |
| 01 | **Visão geral** | ler quatro indicadores, a série horária e a fila de workflows | o estado consolidado do pipeline, com o incidente dentro da janela |
| 02 | **Busca fiscal** | filtrar por empresa, tipo, status, mês e texto; abrir o painel de detalhe de um documento; clicar num `X-Request-Id` | a interação de consulta e a correlação request → trace → logs |
| 03 | **Extração & workflow** | ver as seis etapas com throughput e latência, a fila de falhas com motivo e o botão **reprocessar** | o desenho do trabalho assíncrono (e que reprocessar não regrava publicado) |
| 04 | **Cobertura & SLI** | comparar cobertura por empresa, ver gráfico de queima (1 h, 6 h, 24 h), orçamento de erro e incidentes | SLI/SLO com fórmula explícita, não número solto |
| 05 | **Arquitetura** | percorrer o diagrama (borda, API, workers, dados, telemetria) e abrir cada arquivo do desenho | como o sistema foi desenhado e onde cada peça está versionada |
| 06 | **Sobre o case** | ler o que é sintético, o que é desenho, a pilha, a escala ilustrativa e a política de privacidade | honestidade sobre o que a demonstração mostra |

Há uma sétima vista, `#/correlacao`, alcançável só por clique num identificador:

ela mostra o trace e os logs daquele pedido, como se faz num Grafana.

## Mapa de arquivos

```
.
├── index.html                  página única: seis vistas, acessível, sem script externo
├── app.js                      renderização, filtros, gráficos SVG, roteamento por hash
├── styles.css                  identidade visual própria (sem fonte externa, sem CDN)
├── 404.html                    página de erro com a mesma identidade e links úteis
├── robots.txt                  indexação permitida
├── README.md · SECURITY.md · LICENSE · .gitignore
├── data/
│   ├── sinteticos.json         dados da demo (consumidos via fetch)
│   ├── dados.js                mesmo JSON em window.DEMO_DATA (fallback file://)
│   └── AVISO-SINTETICO.md      o que é sintético, a seed e como reproduzir
├── tools/
│   ├── gen_data.py             gerador determinístico (--check, --stdout, --seed, --out)
│   ├── validate.py             validador estrutural do repositório (stdlib)
│   ├── leak_check.py           varredura anti-vazamento (blocklist, formatos, hosts)
│   └── teste_render.js         teste de fumaça das vistas sem navegador
├── k8s/
│   ├── README.md               ordem de aplicação, variáveis e o que não fazer
│   ├── namespace.yaml          namespace + ResourceQuota + LimitRange
│   ├── deployment-api.yaml     API FastAPI (sondas, securityContext, ConfigMap, SA)
│   ├── deployment-worker.yaml  workers de extração/publicação
│   ├── service-api.yaml        Services ClusterIP (nada publicado)
│   ├── ingress.yaml            borda TLS e roteamento por path
│   ├── oauth2-proxy.yaml       autenticação na borda
│   ├── networkpolicy.yaml      default-deny + liberações nominais
│   ├── secret-template.yaml    modelo de credencial (só placeholder)
│   ├── hpa.yaml                autoscala: CPU para a API, fila para o worker
│   ├── pdb.yaml                orçamento de interrupção diferente por papel
│   └── postgres-or-mongo-statefulset.yaml  camada de dados e a troca de engine
├── observabilidade/
│   ├── prometheus-rules.yml    SLIs, gravações, burn rate e alertas (28 regras)
│   ├── alertmanager-routing.yml roteamento, inibição e silêncio por severidade
│   ├── otel-collector-config.yaml Collector: OTLP na entrada, três backends na saída
│   ├── loki-logql-exemplos.md  do X-Request-Id da interface ao trace_id
│   └── dashboards/grafana-demo.json painel importável (14 painéis)
├── docs/decisoes/
│   ├── index.html              índice publicado dos ADRs
│   ├── 0001-borda-com-ingress-e-oauth2-proxy.md
│   ├── 0002-workers-desacoplados-da-api.md
│   └── 0003-dados-sinteticos-em-vez-do-codigo-original.md
├── img/
│   ├── favicon.svg             ícone da página
│   └── demo-card.svg           imagem de compartilhamento (1200×630)
└── ci/github-actions-deploy.yml  qualidade + publicação em GitHub Pages

```

## O que é demonstração e o que é desenho de arquitetura

| Camada | Nesta página | No desenho documentado |
| --- | --- | --- |
| Interface | **executa no seu navegador**: filtros, detalhe, gráficos, correlação, reprocessar | React no caso original; aqui HTML/CSS/JS puro |
| Dados | **existe como arquivo**: `data/sinteticos.json` | Mongo no original; alternativa Postgres em `k8s/postgres-or-mongo-statefulset.yaml` |
| API | não existe | FastAPI com sondagens e contrato aberto (`k8s/deployment-api.yaml`) |
| Workers | não existem | extração, OCR e publicação em Deployment próprio, escalando por fila |
| Borda | não existe | Ingress + oauth2-proxy; o Service nunca é publicado |
| Telemetria | **existe como texto**: regras, painel, config do Collector | OTLP → Prometheus, Loki e Tempo; Alertmanager por severidade |
| Autenticação | não existe: a demo não tem login | sessão no proxy, allow list por grupo, cabeçalhos assinados pela borda |

Regra do projeto: se um número aparece, ele ou vem do gerador (com fórmula

registrada) ou está rotulado como ordem de grandeza ilustrativa. Nenhuma métrica

de produção é alegada aqui.

## Dados sintéticos: o aviso

Todos os dados são sintéticos e rotulados como tal — empresas, documentos,

números, datas, identificadores, traces, logs, métricas e incidentes. A seed é

fixa (`20260124`) e a saída é reprodutível byte a byte:

```bash
python3 tools/gen_data.py --check

```

Identificadores usam prefixos `EMP-DEMO`, `DOC-FISCAL-SINT`, `NF-DEMO`,

`REQ-DEMO`, `INC-DEMO` e `ERR-SINT`; hosts são `*.example.com`; namespace é

`automacao-demo`; imagens têm sufixo `-SINT`; chave de acesso de documento fiscal

não existe no arquivo. O detalhe está em

[`data/AVISO-SINTETICO.md`](data/AVISO-SINTETICO.md), e a checagem automática em

[`tools/leak_check.py`](tools/leak_check.py).

## Publicação

`ci/github-actions-deploy.yml` é a definição completa do deploy: dois jobs, nenhum segredo.
Para ativar, copie para `.github/workflows/deploy.yml` (a integração usada nesta publicação
não tinha permissão para escrever `.github/`, por isso o YAML está versionado aqui):

1. **qualidade** — `tools/gen_data.py --check`, `py_compile` nas ferramentas,

   `tools/validate.py`, `tools/leak_check.py` e o teste de renderização;

2. **páginas** — publica o diretório do repositório como artefato estático usando

   apenas `actions/upload-pages-artifact` e `actions/deploy-pages`. **Sem `npm`,
   sem build**: o que está no Git é o que vai para o ar.

Permissões mínimas (`contents: read` no workflow; `pages: write` e

`id-token: write` só no job de publicação), `concurrency` no grupo

`pages`, e **nenhum arquivo `CNAME`** — a publicação é no caminho versionado do

usuário, não num domínio próprio. Como a publicação é de arquivos crus, os links

para `.md`, `.yml` e `.json` abrem o texto-fonte no navegador; para ler os ADRs

formatados, use o repositório no GitHub ou o índice publicado em

[`docs/decisoes/`](docs/decisoes/).

## Links

* Código e issues: <https://github.com/jraalves> ·

  <https://github.com/jraalves/automacao>

* O case estendido (contexto, papel, resultado do projeto):

  <https://github.com/jraalves/fiscal-automation-case>

* Portfólio: <https://jraalves.github.io/portfolio>
* LinkedIn: <https://www.linkedin.com/in/jraalves>

Licença: MIT ([`LICENSE`](LICENSE)). Segurança: [`SECURITY.md`](SECURITY.md).

## English summary

A self-contained, static demonstration of a fiscal document automation product:

search and retrieval of tax documents kept in a corporate file repository, an

extraction pipeline (discovery → ingestion → extraction → schema validation →

publication → human review) and the SRE layer around it (SLIs, error budget,

burn-rate alerting, Kubernetes manifests, OpenTelemetry, GitOps). The client code

cannot be published, so this repository publishes the design plus **synthetic

data**: companies, documents, traces, logs, metrics, workflows and incidents are

generated by `tools/gen_data.py` from a fixed seed (20260124) and are reproducible

byte for byte. `tools/leak_check.py` blocks real-looking identifiers, credentials

and external hosts, and `tools/validate.py` checks the JSON, the YAML manifests,

every internal link, the six views and the published Grafana dashboard. Nothing

here is a production measurement, and the page says so. The site is served at

`jraalves.github.io/automacao` because a GitHub Pages root domain only exists for

a matching account name, that name belongs to someone else, and a versioned path

under my own user is the permanent, controllable option.
