# Política de segurança

Este repositório publica uma **demonstração estática** de uma aplicação de

automação de gestão fiscal: HTML, CSS e JavaScript próprios, um JSON gerado por

script e a documentação do desenho (manifests Kubernetes, regras de alerta,

configuração de Collector, ADRs). Nada aqui é um serviço em produção.

## O que este site executa (e o que não executa)

Executa, no navegador:

* renderizar `data/sinteticos.json` (ou o fallback `data/dados.js` quando aberto

  por `file://`);

* filtros, ordenação, painel de detalhe e gráficos SVG desenhados em `app.js`;
* rotas por hash (`#/busca`, `#/sli`, …) e cópia de identificadores para a

  área de transferência.

Não executa, em hipótese alguma:

* chamada de rede para fora do próprio site — não há API, telemetria, analytics,

  CDN, fonte externa, `npm` nem build;

* autenticação, sessão ou cookie: a demo não tem login;
* acesso a banco, fila ou repositório de arquivos. Os manifests de `k8s/` e os

  arquivos de `observabilidade/` **documentam** um desenho; eles não são
  aplicados por nada aqui.

## Superfície real de risco

| Item | Risco possível | Mitigação já no repositório |
| --- | --- | --- |
| Vazamento de informação confidencial | nome de cliente, domínio, IP, documento fiscal, credencial | `tools/leak_check.py` roda em todo push e barra formato de CNPJ/CPF com dígito verificador, e-mail fora de `example.com`/`example.org`, IP fora de localhost/faixa de exemplo, token com formato conhecido, chave privada e credencial com valor não-placeholder |
| Saída de dados do navegador | requisição a host externo | nenhum recurso remoto é referenciado; `tools/validate.py` confere `href`/`src` internos e o `leak_check.py` confere a allow list de hosts |
| Links de terceiros | conteúdo publicado mudar | `rel="noopener noreferrer"` em todo link externo |
| Publicação em GitHub Pages | artifact maior que o esperado, segredo no pipeline | job de publicação sem segredo, sem `npm`, sem build; permissões mínimas (`contents: read`, `pages: write`, `id-token: write`); nenhum arquivo `CNAME` |
| Manifests copiados para um cluster real | Secret com valor real commitado, Service exposto, container privilegiado | `k8s/README.md` com o que não fazer; `secret-template.yaml` só com `<CHANGE_ME>`; `securityContext` com `runAsNonRoot`, `readOnlyRootFilesystem`, `drop: ["ALL"]`; `NetworkPolicy` default-deny |

## Como relatar um problema

1. Prefira **Report a vulnerability** na aba Security deste repositório no

   GitHub (`github.com/jraalves/automacao`). Isso abre uma *security advisory*
   privada: ninguém além de mim e de você vê o conteúdo.

2. Não abra issue pública descrevendo vulnerabilidade, e não cole credencial,

   token, log interno ou dado de terceiro em issue, PR ou discussão.

3. Não há endereço de e-mail nem chave PGP publicados: este projeto é pessoal e

   estático, e o canal privado do GitHub resolve sem criar um alvo de coleta de
   e-mail.

## O que esperar

* É um projeto pessoal de portfólio, mantido por uma pessoa, sem SLA. Resposta

  em dias úteis, sem prazo prometido.

* Se você encontrar um dado real (seu ou de terceiros) publicado aqui, diga no

  mesmo canal: a correção é imediata (remover, regenerar os dados sintéticos,
  reforçar o verificador) e o histórico do projeto passa a registrar o caso.

* Itens fora de escopo: abuso de um serviço que este repositório descreve mas não

  executa, problemas do seu cluster ao aplicar os manifests de exemplo, e
  vulnerabilidades de dependências que este projeto não tem — não há
  `package.json` nem `requirements.txt`: só a biblioteca padrão do Python e
  JavaScript do navegador.

## Endurecimento usado no desenho documentado

Os arquivos de `k8s/` e `docs/decisoes/` registram as práticas que o desenho

adota — leia como revisão de arquitetura, não como garantia do site estático:

* borda com Ingress + oauth2-proxy, Service nunca publicado

  (`docs/decisoes/0001-borda-com-ingress-e-oauth2-proxy.md`);

* Secret montado `readOnly`, credencial fora do Git, rotação por mecanismo

  externo (`k8s/secret-template.yaml`);

* `NetworkPolicy` default-deny com liberações nominais (`k8s/networkpolicy.yaml`);
* contêiner sem root, filesystem somente leitura, capabilities descartadas,

  `seccompProfile: RuntimeDefault` (todos os Deployments);

* cota e limite por namespace, inclusive `pod-security.kubernetes.io/enforce:

  restricted` (`k8s/namespace.yaml`);

* telemetria com `trace_id` e `request_id` correlacionados, para que investigação

  não dependa de adivinhação (`observabilidade/loki-logql-exemplos.md`).
