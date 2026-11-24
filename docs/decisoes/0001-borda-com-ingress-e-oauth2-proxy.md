# ADR 0001 — Borda com Ingress + oauth2-proxy em vez de expor o Service

**Status:** aceito (desenho documentado nesta demonstração)

**Data de referência:** 2026-01-24 (instante fixo dos dados sintéticos)

**Escopo:** `k8s/ingress.yaml`, `k8s/oauth2-proxy.yaml`, `k8s/service-api.yaml`,

`k8s/networkpolicy.yaml`

## Contexto

A aplicação atende três públicos com direitos diferentes: quem consulta documento

(conferência, auditoria, área fiscal), quem reprocessa (time de operação) e quem

administra cadastros. O caso original já tinha um provedor de identidade

corporativo com grupos, e a regra de acesso vivia **dentro** do código de negócio:

cada endpoint comparava grupo e papel, e cada novo endpoint repetia a comparação.

O desenho precisava responder a quatro coisas de uma vez:

1. como terminar TLS sem colocar certificado dentro da aplicação;
2. como autenticar sem transformar a API em um servidor de sessão;
3. como manter a API não publicável quando o cluster tem um Ingress;
4. como rate limit e cabeçalhos de segurança serem aplicados num lugar só.

## Decisão

1. **Um Ingress é a única entrada pública.** TLS, rate limit defensivo por rota e

   cabeçalhos de segurança (`X-Content-Type-Options`, `Referrer-Policy`,
   `X-Frame-Options`) vivem nas anotações do Ingress (`k8s/ingress.yaml`).

2. **O Ingress aponta para o oauth2-proxy, não para a API.** Toda path passa pelo

   proxy, inclusive `/oauth2` (callback). A API só é alcançada depois que o proxy
   valida o cookie de sessão e injeta os cabeçalhos de identidade
   (`X-Auth-Subject`, `X-Auth-Groups`).

3. **O Service da API continua `ClusterIP`.** Não existe `type: LoadBalancer` e não

   existe NodePort na aplicação. Se o Ingress cair, nada vira público.

4. **A allow list de tráfego é escrita** (`k8s/networkpolicy.yaml`,

   `negar-por-padrao` + liberações nominais). O proxy é o único pod que fala com a
   API; a API e o worker falam com a camada de dados e com o conector.

5. **Autorização fina continua na aplicação** — o proxy responde "quem é", a

   aplicação responde "pode". A diferença fica explícita no código de negócio: o
   grupo vira papel uma única vez, no middleware que lê os cabeçalhos assinados
   pela borda.

## Alternativas rejeitadas

- **`LoadBalancer` direto na API com autenticação no código.** Mais simples de

  subir, mas expõe a superfície inteira antes de qualquer checagem, espalha
  sessão pelo código e não resolve TLS nem rate limit. Rejeitada.

- **Sidecar de autenticação por pod.** Funciona, e é a resposta certa quando se

  precisa de mTLS por carga. Para uma aplicação web com um único ponto de entrada
  é operação desnecessária: dois caminhos de configuração para a mesma regra.

- **JWT validado na aplicação sem proxy.** Elimina a camada, mas joga para o time

  de aplicação refresh de token, revogação, logout e a parte chata de OIDC. O
  proxy resolve isso com configuração.

- **ApiGateway de nuvem específica.** Fora do escopo desta demonstração por

  desenho: nenhuma decisão aqui depende de fornecedor.

## Consequências

**Custo aceito.** Mais um componente para operar, mais um log para olhar, e um

fluxo de logout que o time precisa entender (sessão no proxy, estado no

navegador). Se o proxy cair, ninguém acessa a aplicação — que é o comportamento

correto, mas exige alerta próprio (o `DisponibilidadeBuscaAbaixoDoObjetivo` em

`observabilidade/prometheus-rules.yml` separa 5xx da aplicação de 5xx da borda).

**Ganhos.** Um lugar para mexer em TLS, cabeçalhos e rate limit; a allow list de

tráfego vira revisão de código; e a aplicação perde a responsabilidade de sessão,

o que reduz o surface de bug de autenticação.

**O que fica mais difícil.** Teste de ponta a ponta precisa de identidade falsa;

para a demonstração, a página é estática e roda **sem autenticação nenhuma** — o

desenho acima é documentado, não executado por este site.

## Como medir

| Pergunta | Métrica / checagem | Onde |
| --- | --- | --- |
| A borda está disponível? | `demo:disponibilidade_busca_ratio` >= 0.995 | `observabilidade/prometheus-rules.yml` |
| Alguém expôs a API sem proxy? | ausência de Service com `type` != `ClusterIP` em `k8s/` | `tools/validate.py` + revisão |
| Tráfego indevido chegou à API? | `liberar-proxy-para-api` como única liberação externa à borda | `k8s/networkpolicy.yaml` |
| O proxy está autenticando? | razão entre requests com `X-Auth-Subject` e requests totais | log de acesso da borda |

## Referências cruzadas

`k8s/ingress.yaml` · `k8s/oauth2-proxy.yaml` · `k8s/networkpolicy.yaml` ·

`k8s/README.md` (item "O que NÃO fazer" 2) ·

`docs/decisoes/0002-workers-desacoplados-da-api.md` ·

aba Arquitetura da interface (`index.html`)
