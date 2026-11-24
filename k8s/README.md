# k8s/ — manifests do desenho de produção (demonstração)

Estes arquivos **documentam** como a aplicação de automação fiscal seria rodada

num cluster Kubernetes. Nada aqui é executado por este site: a página publicada

é estática e consome apenas `data/sinteticos.json`.

Todos os nomes são fictícios: namespace `automacao-demo`, host

`automacao-demo.example.com`, registry `registry.example.com` e imagens com

sufixo `-SINT`. Se aparecer um nome de ambiente real em algum desses campos, o

`tools/leak_check.py` do CI quebra o pipeline.

## Ordem de aplicação

```bash
kubectl apply -f k8s/namespace.yaml                     # 1. namespace, cota, limites
kubectl apply -f k8s/secret-template.yaml               # 2. SÓ COMO MODELO: veja abaixo
kubectl apply -f k8s/configmap-otel.yaml                # 3. configuração do Collector
kubectl apply -f k8s/deployment-api.yaml                # 4. API + ServiceAccount + ConfigMap
kubectl apply -f k8s/deployment-worker.yaml             # 5. workers de extração/publicação
kubectl apply -f k8s/service-api.yaml                   # 6. Services ClusterIP (nada exposto)
kubectl apply -f k8s/postgres-or-mongo-statefulset.yaml # 7. camada de dados (desenho alternativo)
kubectl apply -f k8s/oauth2-proxy.yaml                  # 8. proxy de autenticação na borda
kubectl apply -f k8s/networkpolicy.yaml                 # 9. default-deny + allow list de tráfego
kubectl apply -f k8s/pdb.yaml                           # 10. orçamento de interrupção
kubectl apply -f k8s/hpa.yaml                           # 11. autoscala
kubectl apply -f k8s/ingress.yaml                       # 12. borda TLS

```

Sobre o passo 9: o `negar-por-padrao` seleciona todos os pods do namespace. Se

você aplicar as NetworkPolicies antes de as cargas estarem rodando, nada quebra

(o default-deny só restringe o que ainda não foi liberado), mas é fácil perder

uma liberação no meio do barulho. A ordem que uso em cluster real é: Deployments

→ Services → NetworkPolicy → Ingress, conferindo `kubectl -n automacao-demo

logs` entre cada passo.

## Variáveis que você precisa trocar

| Onde | Campo | O que colocar |
| --- | --- | --- |
| `secret-template.yaml` | todo `stringData` | valores do SEU cluster de teste, **fora do Git** |
| todos os manifests | `namespace` | o namespace do seu ambiente |
| `deployment-*.yaml` | `image:` | imagem espelhada no registry interno |
| `ingress.yaml` | `host:` / `secretName:` | o domínio e o Secret TLS reais |
| `oauth2-proxy.yaml` | `oauth2.cfg` | emissor OIDC, clientes e grupos do seu IdP |
| `hpa.yaml` | `averageValue: "120"` | itens de fila por worker que o seu SLO tolera |

## O que NÃO fazer

1. **Não commitar Secret real.** O arquivo é um modelo: `kubectl create secret`

   ou um gerenciador (Sealed Secrets, External Secrets, Vault). O CI roda
   `tools/leak_check.py`, que barra chave privada, token com formato conhecido e
   valor de credencial que não seja placeholder — mas a barreira real é revisão.

2. **Não expor o Service da API.** `service-api.yaml` é `ClusterIP` e continua

   assim; quem publica rota é o Ingress, apontando para o `oauth2-proxy`
   (decisão em `../docs/decisoes/0001-borda-com-ingress-e-oauth2-proxy.md`).
   Trocar para `type: LoadBalancer` "só para testar" cria uma porta de entrada
   sem autenticação que a NetworkPolicy ainda permite, porque a liberação é feita
   por rótulo de pod, não por origem de rede.

3. **Não colar credencial em ConfigMap.** ConfigMap não é segredo: ele aparece

   em `kubectl get -o yaml` para qualquer um com leitura do namespace.

4. **Não remover o `readOnlyRootFilesystem: true`** para "resolver" um erro de

   escrita. Os caminhos temporários vão em `emptyDir` (é o que os manifests já
   fazem com `/tmp` e com o volume de execução do Postgres).

5. **Não esquecer o PDB ao escalar para 1 réplica.** Com `minAvailable: 2` e uma

   réplica só, o `drain` do nó fica travado para sempre. O HPA de API mantém
   `minReplicas: 3` exatamente para isso.

6. **Não aplicar a `configuration-snippet` do Ingress sem checar o controller.**

   Versões recentes do controller de Ingress exigem `allow-snippet-annotations`
   explícito, e com razão: anotação de snippet é execução de configuração. Se o
   seu cluster não aceita, mova os cabeçalhos de segurança para o middleware da
   aplicação ou para o proxy — não delete a proteção.

## Armadilhas que já me pegaram

- **Métrica errada no HPA do worker.** CPU de worker parado com fila crescente

  é 0. Escalar por profundidade de fila (`hpa.yaml`) é o que segura a cobertura.

- **Rótulo renomeado deixa alerta mudo.** Uma regra de SLI que aponta para um

  rótulo que deixou de existir fica verde para sempre. É a causa do
  `INC-DEMO-07` da demonstração, e o remédio é ter teste de regra no pipeline.

- **`secretKeyRef` criado depois do rollout.** O pod entra em loop de reinício.

  O job de preparação listado em `deployment-worker.yaml` existe para isso.

- **Amostragem de trace que corta o pedido da interface.** Se o Collector amostra

  antes de o `X-Request-Id` chegar ao backend, a aba de correlação não encontra
  nada. Veja `../observabilidade/loki-logql-exemplos.md`.
