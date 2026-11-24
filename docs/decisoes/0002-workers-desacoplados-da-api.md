# ADR 0002 — Workers desacoplados da API

**Status:** aceito (desenho documentado nesta demonstração)

**Data de referência:** 2026-01-24

**Escopo:** `k8s/deployment-api.yaml`, `k8s/deployment-worker.yaml`, `k8s/hpa.yaml`,

`k8s/pdb.yaml`

## Contexto

A aplicação faz duas coisas com naturezas opostas:

* **busca interativa** — uma consulta com filtro de empresa, mês e status, que o

  usuário espera responder em menos de um segundo, com paginação, e cujo pior
  resultado é um p95 alto;

* **extração em lote** — baixar arquivo do repositório de origem, parsear leiaute

  fiscal, rodar OCR quando o PDF não tem camada de texto, validar esquema, gravar
  no destino. Cada documento pode custar segundos de CPU e uma chamada de rede
  longa, e o pior resultado é um documento não publicado no prazo.

No começo as duas viviam no mesmo processo. Foi a decisão errada, e o motivo é

apresentável: durante uma carga retroativa do exercício anterior, a busca de

conferência subiu o p95 para dezenas de segundos, e um restart de processo derrubava

busca e extração juntas. O efeito colateral mais caro, porém, foi operacional:

**a métrica de escala era uma só**, então autoescalar por CPU significava escalar a

busca junto com o lote, pagando réplicas de API que ninguém estava usando.

## Decisão

1. **Deployments separados, imagens separadas, direitos separados.** A API

   (`automacao-api`) fala com a camada de dados e com o repositório de destino
   apenas em operações curtas; o worker (`automacao-worker`) é o único processo que
   executa extração, OCR e publicação.

2. **Comunicação por fila de trabalho, não por chamada direta.** A API registra a

   intenção de processamento (documento, motivo, tentativas) e o worker reclama o
   item. Nada na API espera o fim de uma extração.

3. **Escala por métricas diferentes.** API: CPU/memória (o custo acompanha o

   número de buscas). Worker: profundidade de fila
   (`demo:fila_documentos_por_worker` em `observabilidade/prometheus-rules.yml`,
   consumida pelo HPA em `k8s/hpa.yaml`). Rejeito escalar worker por CPU: fila
   parada com CPU ociosa é exatamente o cenário que gera incidente de cobertura.

4. **Orçamento de interrupção diferente.** API com `minAvailable: 2` (não aceito

   buraco na busca); worker com `maxUnavailable: 1` e
   `terminationGracePeriodSeconds` alto, porque esvaziamento precisa terminar a
   extração em andamento (`k8s/pdb.yaml`).

5. **Reprocessamento é operação do usuário, não retry automático infinito.**

   A interface expõe "reprocessar" com o motivo da falha; a política de retry
   automática é curta (3 tentativas, backoff exponencial) e depois o item vira fila
   de análise humana. Retry sem fim contra um leiaute quebrado só produz custo.

## Alternativas rejeitadas

- **Mesmo deployment, processos separados por flag.** Menos manifests, mas o

  rollout continua sendo um só: uma imagem ruim derruba os dois papéis de novo.

- **Função como serviço para extração.** Atrai pelo custo proporcional, mas OCR e

  parse de leiaute têm perfil de memória e tempo que estouram limite de execução, e
  o acoplamento com o repositório de arquivos precisa de credencial montada — algo
  que prefiro num pod com `readOnlyRootFilesystem`.

- **Fila com consumer único e lock pessimista.** Elimina corrida de publicação, e

  foi o que o caso original tentou antes do `INC-DEMO-06` da demonstração: um lock
  por documento no repositório de destino (via ETag) resolve a corrida com custo
  muito menor.

## Consequências

**Custo aceito.** Eventual consistência visível: um documento publicado aparece na

busca com atraso, e o status "processando" existe na interface para dizer isso sem

mentira. Também passei a manter dois caminhos de rollout, dois PDBs e duas políticas

de autoscala, e a precisar de uma métrica de fila confiável (se ela sumir, o HPA

congela — por isso o alerta `MetricaDeSLIAusente`).

**Ganhos.** Um worker lento não degrada busca; o lote pode escalar a 24 réplicas

sem tocar na API; a falha de extração fica isolada no componente certo; o custo de

OCR aparece onde ele é gerado.

**O que fica mais difícil.** Debugar fluxo assíncrono exige correlação: o

`X-Request-Id` da interface precisa sobreviver ao trace e aos logs do worker, que é

o que `observabilidade/loki-logql-exemplos.md` documenta.

## Como medir

| Pergunta | Métrica | Onde |
| --- | --- | --- |
| A busca ficou lenta por causa do lote? | `demo:extracao_p95_segundos` vs `demo:disponibilidade_busca_ratio` no mesmo instante | painel Grafana |
| O worker está atrasando a publicação? | `demo:pipeline_fila_documentos` crescente | `observabilidade/prometheus-rules.yml` |
| O desacoplamento está segurando? | tempo entre `data_entrada_repositorio` e publicação na trilha de auditoria | `data/sinteticos.json` |
| Retry virou loop? | contagem de tentativas por documento na fila de falhas | aba Extração & workflow |

## Referências cruzadas

`k8s/deployment-worker.yaml` · `k8s/hpa.yaml` · `k8s/pdb.yaml` ·

`docs/decisoes/0001-borda-com-ingress-e-oauth2-proxy.md` ·

`observabilidade/loki-logql-exemplos.md`
