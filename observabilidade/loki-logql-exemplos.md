# LogQL de correlação: do `X-Request-Id` da interface ao `trace_id`

Este arquivo é o texto-fonte do que a **aba Correlação** desta demonstração faz.

Ela não fala com um Loki real: ela reproduz, com dados de

`data/sinteticos.json`, o mesmo gesto que se faz num Grafana — clicar num

identificador de pedido e chegar nos logs e no trace daquele pedido.

Formato de log publicado pelas aplicações (uma linha JSON por evento, o que o

Collector recebe por OTLP e envia ao Loki — ver

`otel-collector-config.yaml`, processor `transform/pedido`):

```json
{
  "timestamp": "2026-01-24T09:12:41-03:00",
  "level": "INFO",
  "service_name": "api-busca",
  "message": "busca atendida: 1 documento de demonstração retornado (REQ-DEMO-6m5mdkwz-001)",
  "trace_id": "4b3ebdfd1ca1c7b4aefdcbacefa37a7d",
  "span_id": "aefdcbacefa37a7d",
  "request_id": "REQ-DEMO-6m5mdkwz-001",
  "empresa_id": "EMP-DEMO-0001"
}

```

Todos os valores acima são sintéticos e vêm do gerador (`tools/gen_data.py`,

seed `20260124`). `EMP-DEMO-0001` e `REQ-DEMO-6m5mdkwz-001` são a empresa e o

pedido do documento `DOC-FISCAL-SINT-000001` da aba Busca fiscal.

## O que é rótulo (indexado) e o que é campo (não indexado)

Decisão de custo, não de estilo: rótulo demais no Loki multiplica séries e

encarece ingestão. Indexamos apenas o que uma investigação realmente usa como

ponto de entrada.

| Chave | Onde | Por quê |
| --- | --- | --- |
| `namespace` | label de stream | isolamento por ambiente (aqui, `automacao-demo`) |
| `service_name` | label de stream | API, worker, validador, conector |
| `level` | label de stream | separar erro de ruído sem tocar no corpo do log |
| `trace_id` | label de stream | é a chave de correlação com Tempo |
| `request_id` | field (estrutura JSON) | cardinalidade alta demais para label: um valor por pedido |
| `empresa_id` | field | filtro opcional; quando virar label, medir antes o custo |

Para consultar `request_id` sem indexá-lo como label, usa-se `| json` e um

filtro de linha — Loki a partir da versão 2.9 também aceita *field filter*

(`| request_id="..."`) quando o campo está no esquema do tenant.

## 1. Sair do `X-Request-Id` que a interface mostrou e chegar nos logs

Foi isto que o clique no botão da aba Busca fez (`#/correlacao/<trace_id>`):

```logql
{namespace="automacao-demo", service_name="api-busca"}
  | json
  | request_id = "REQ-DEMO-6m5mdkwz-001"

```

Mesma coisa pelo `trace_id`, que é o que a coluna do painel traz:

```logql
{namespace="automacao-demo"} | trace_id = "4b3ebdfd1ca1c7b4aefdcbacefa37a7d"

```

E o caminho inverso — do trace para o pedido, quando alguém chega por Tempo:

```logql
{namespace="automacao-demo"}
  | json
  | line_format "{{.request_id}} {{.level}} {{.message}}"
  | trace_id = "4b3ebdfd1ca1c7b4aefdcbacefa37a7d"

```

## 2. Do alerta para o log do motivo da falha

O alerta `TaxaErroWorkerQueimaModerada` (`prometheus-rules.yml`) leva aqui:

```logql
sum by (service_name) (
  count_over_time(
    {namespace="automacao-demo", level="ERROR"}
    | json
    | __error__="" [1h]
  )
)

```

Com o motivo agrupado, que é o que a fila de falhas da interface mostra:

```logql
topk(
  5,
  sum by (motivo) (
    count_over_time(
      {namespace="automacao-demo", service_name="worker-extracao", level="ERROR"}
      | json
      | pattern `ERR-SINT-<motivo>:<msg>` [24h]
    )
  )
)

```

## 3. Latência da extração vista no log, não só no histograma

```logql
avg_over_time(
  {namespace="automacao-demo", service_name="worker-extracao"}
  | json
  | unwrap duracao_ms [10m]
)

```

```logql
quantile_over_time(0.95,
  {namespace="automacao-demo", service_name="worker-extracao"}
  | json
  | unwrap duracao_ms [1h]
)

```

Se o p95 do log divergir do `demo:extracao_p95_segundos_curto`, o problema está

na instrumentação (um dos dois não está medindo a mesma etapa), não no serviço.

## 4. Busca sem resultado: o pedido que a interface respondeu e o usuário não viu nada

```logql
sum(
  count_over_time(
    {namespace="automacao-demo", service_name="api-busca", level="WARN"}
    |~ "sem resultado" [1h]
  )
)

```

É o SLI `buscas_sem_resultado_pct` da aba Cobertura & SLI. Alto e a busca virou

um lugar onde o usuário não confia: problema de produto, não de disponibilidade.

## 5. Ligação automática do Grafana (trace_id vira link para o Tempo)

No Loki datasource do Grafana, a configuração `derivedFields` cria o link. É

isto que faz o painel `observabilidade/dashboards/grafana-demo.json` mostrar

"Open in Tempo" ao lado de cada linha:

```json
{
  "derivedFields": [
    {
      "name": "traceID",
      "matcherJson": "trace_id",
      "url": "${__value.raw}",
      "datasourceUid": "DS_TEMPO"
    }
  ]
}

```

Regras do desenho que aparecem aqui:

1. `request_id` e `trace_id` andam juntos desde a borda: o `X-Request-Id` é

   gerado no Ingress/proxy, entra no trace OTel e é copiado para o log. Se um
   dos dois faltar, a correlação morre no meio.

2. Erro sem `trace_id` é bug de instrumentação. A checagem disso está no

   `tools/validate.py` (amostra os traces de `data/sinteticos.json` e exige
   `trace_id` e `request_id` em cada span de log).

3. Retenção de log e retenção de trace precisam bater. Se o Loki retém 14 dias e

   o Tempo 7, a pergunta "o que aconteceu naquele pedido da semana passada?" não
   tem resposta — é o `INC-DEMO-08` da demonstração.

## 6. Como o alerta de queima se relaciona com estas consultas

| Alerta | Consulta que responde a primeira pergunta |
| --- | --- |
| `CoberturaFiscalQueimaAcelerada` | `sum(count_over_time({namespace="automacao-demo", service_name="worker-publicacao", level="ERROR"}[30m]))` |
| `TaxaErroWorkerQueimaAcelerada` | bloco 2, por `service_name` |
| `DisponibilidadeBuscaAbaixoDoObjetivo` | `sum by (status_code) (count_over_time({namespace="automacao-demo", service_name="api-busca"} \| json \| status_code=~"5.." [15m]))` |
| `MetricaDeSLIAusente` | qualquer consulta deste arquivo que retorne vazio: série sumida, não serviço parado |
| `PipelineParadoComFilaCrescendo` | bloco 2 filtrando `service_name="agendador-varredura"` |

Nada neste arquivo foi executado: a página é estática. As consultas existem para

que você possa ler, copiar e colar num Grafana de verdade e conferir a forma.
