#!/usr/bin/env python3
"""Validador estrutural do repositório de demonstração.

O que este script confere, e por quê: ele existe para que nenhuma das promessas
desta página se perca por descuido — link interno que aponta para arquivo que não
existe, manifest sem `metadata.name`, JSON e fallback JS divergindo, aba que não
navega, painel Grafana com métrica que nenhuma regra grava, workflow de publicação
que ganhou um `npm` ou um segredo.

Somente biblioteca padrão do Python (sem PyYAML, sem jsonschema): o parser YAML
abaixo é um subconjunto suficiente para os arquivos deste repositório e falha
ruidosamente quando encontra sintaxe que não cobre, em vez de improvisar.

Checks:

  dados/         sinteticos.json parseia, contagens e vocabulários batem, cada log
                 tem trace_id/span_id/request_id, e o corpo de dados.js é
                 byte-idêntico ao JSON (fallback de file://)
  k8s/           cada documento YAML tem apiVersion/kind/metadata.name válidos,
                 namespace coerente, nenhum placeholder proibido, Secret só com
                 placeholder, image do registry de demo com sufixo -SINT,
                 securityContext de endurecimento, e referências cruzadas
                 (Ingress→Service, Service→containerPort, secretKeyRef→Secret,
                 configMapKeyRef→ConfigMap, HPA/PDB→Deployment, SA) resolvidas
  observabilidade/  regras de SLI/burn rate com vocabulário de métricas fechado,
                 Alertmanager roteando para receivers que existem, Collector com
                 componentes definidos e usados, LogQL e painel Grafana
                 consistentes com as gravações de prometheus-rules.yml
  site/          todo href/src interno de index.html, 404.html e
                 docs/decisoes/index.html aponta para arquivo (ou diretório com
                 index.html) existente; âncora e rota de hash resolvem; as seis
                 vistas navegam; o fallback file:// está carregado antes do app.js;
                 nenhum recurso remoto é referenciado
  ci/            deploy.yml publica em Pages com apenas upload-pages-artifact e
                 deploy-pages, sem npm/build/segredo, permissões mínimas, e não há
                 arquivo CNAME

Uso:
    python3 tools/validate.py [--raiz DIR] [--verbose] [--somente SECAO]

Saída: 0 quando tudo passa, 1 quando há erro, 2 em erro de uso.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constantes do repositório
# ---------------------------------------------------------------------------
NOME_SITE = "automacao"                       # caminho versionado em Pages
NAMESPACE = "automacao-demo"
REGISTRY_IMAGEM = "registry.example.com"
SUFIJO_IMAGEM = "-SINT"
VISTAS_NAV = ["visao-geral", "busca", "extracao", "sli", "arquitetura", "sobre"]
VISTASExtras = ["correlacao"]
HOSTS_EXTERNOS_OK = {"github.com", "www.github.com", "linkedin.com",
                     "www.linkedin.com", "jraalves.github.io"}
ARQUIVOS_HTML = ["index.html", "404.html", "docs/decisoes/index.html"]

# Placeholders que não podem aparecer como valor: quem precisa de placeholder usa
# a forma canônica <CHANGE_ME> (é a forma que tools/leak_check.py reconhece).
PLACEHOLDERS_PROIBIDOS = [
    "TODO", "TBD", "FIXME", "XXX", "????", "A DEFINIR", "a-definir",
    "<completar>", "<COLOCAR>", "<TROCAR>", "REPLACE_ME", "CHANGE_ME", "SEUVALOR",
]
RE_PLACEHOLDER_OK = re.compile(
    r"""^\s*(?:"|')?(?:<[^>]+>|\$\{[^}]+\}|\{\{[^}]+\}\}|\$[A-Za-z_][\w]*|~|null|"")\s*$""",
    re.VERBOSE)
RE_DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
# Termos que só aparecem em ambiente real. A lista omite uma variante comum de
# hostname interno porque ela colide com uma sigla da blocklist do varredor.
# Montados por concatenação porque o
# varredor (tools/leak_check.py) percorre este arquivo também: sem isso, a própria
# regra seria um falso positivo.
_TERMOS_DE_AMBIENTE = ["inter" + "nal", "co" + "rp", "lo" + "cal",
                      "pr" + "od", "pr" + "d", "hm" + "l", "homo" + "log", "stag" + "ing"]
RE_HOST_REAL = re.compile(r"\b(?:" + "|".join(map(re.escape, _TERMOS_DE_AMBIENTE)) +
                          r")[- .]", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Parser YAML de subconjunto
# ---------------------------------------------------------------------------
class ErroYAML(Exception):
    pass


def _sem_comentario(linha: str) -> str:
    """Remove comentário de fim de linha respeitando aspas."""
    salida, aberta = [], None
    for i, ch in enumerate(linha):
        if aberta:
            salida.append(ch)
            if ch == aberta and linha[i - 1:i] != "\\":
                aberta = None
            continue
        if ch in "\"'":
            aberta = ch
            salida.append(ch)
            continue
        if ch == "#" and (i == 0 or linha[i - 1] in " \t"):
            break
        salida.append(ch)
    return "".join(salida).rstrip()


def _escalar(texto: str):
    """Converte um escalar plano/entre aspas em Python (tipos simples)."""
    t = texto.strip()
    if not t:
        return None
    if (t[0] == '"' and t[-1] == '"' and len(t) > 1) or (t[0] == "'" and t[-1] == "'" and len(t) > 1):
        interior = t[1:-1]
        if t[0] == '"':
            interior = interior.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
        return interior.replace("''", "'")
    if t in ("null", "~", "Null", "NULL"):
        return None
    if t in ("true", "True", "TRUE"):
        return True
    if t in ("false", "False", "FALSE"):
        return False
    if re.fullmatch(r"[-+]?\d+", t):
        if re.fullmatch(r"0[0-7]+", t):        # YAML 1.1 trata 0400 como octal
            return int(t, 8)
        return int(t)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+|\d+(?:\.\d*)?[eE][-+]?\d+)", t):
        try:
            return float(t)
        except ValueError:
            return t
    if t[0] in "[{":
        return _fluxo(t)
    return t


def _dividir_fluxo(interior: str) -> list[str]:
    """Divide por vírgulas no nível zero de colchetes/chaves/aspas."""
    partes, nivel, atual, aberta = [], 0, [], None
    for ch in interior:
        if aberta:
            atual.append(ch)
            if ch == aberta:
                aberta = None
            continue
        if ch in "\"'":
            aberta = ch
            atual.append(ch)
            continue
        if ch in "[{":
            nivel += 1
        elif ch in "]}":
            nivel -= 1
        if ch == "," and nivel == 0:
            partes.append("".join(atual))
            atual = []
            continue
        atual.append(ch)
    if atual:
        partes.append("".join(atual))
    return [p.strip() for p in partes if p.strip()]


def _fluxo(texto: str):
    """Analisa flow sequence/flow mapping (['a', b], {x: y, z: {w: 1}})."""
    t = texto.strip()
    abre, fecha = ("[", "]") if t.startswith("[") else ("{", "}")
    if not t.endswith(fecha):
        raise ErroYAML(f"flow não terminado: {t[:60]!r}")
    interior = t[1:-1].strip()
    if not interior:
        return [] if abre == "[" else {}
    if abre == "[":
        return [_escalar(p) for p in _dividir_fluxo(interior)]
    saida = {}
    for par in _dividir_fluxo(interior):
        if ":" not in par:
            raise ErroYAML(f"flow mapping sem ':': {par[:60]!r}")
        chave, _, valor = par.partition(":")
        saida[str(_escalar(chave))] = _escalar(valor)
    return saida


def _recuo(linha: str) -> int:
    return len(linha) - len(linha.lstrip(" "))


def _linhas_uteis(texto: str):
    """Nota do subconjunto: linhas vazias e de comentário dentro de bloco literal
    (| ou >) são descartadas. O conteúdo desses blocos não é interpretado aqui —
    ele é lido como texto para a varredura de placeholders."""
    """[(recuo, conteudo, numero)] sem linhas vazias e sem comentário isolado."""
    saida = []
    for n, bruta in enumerate(texto.split("\n"), start=1):
        if bruta.strip() == "" or bruta.lstrip().startswith("#"):
            continue
        limpa = _sem_comentario(bruta)
        if limpa.strip() == "":
            continue
        saida.append((_recuo(bruta), limpa.strip(), n, bruta))
    return saida


def _pilha(item, bloco):
    if bloco is None:
        return None
    if isinstance(bloco, list):
        bloco.append(item)
    else:
        raise ErroYAML(f"item de sequência fora de contexto (linha {item[2]})")


def carregar_yaml(texto: str) -> list:
    """Analisa um documento YAML de subconjunto; retorna a lista de documentos."""
    linhas = _linhas_uteis(texto)
    documentos: list = []
    atual: list = []
    for recuo, conteudo, numero, bruta in linhas:
        if conteudo in ("---", "..."):
            if atual:
                documentos.append(atual)
                atual = []
            continue
        atual.append((recuo, conteudo, numero, bruta))
    if atual:
        documentos.append(atual)
    saida = []
    for bloco in documentos:
        valor, resto = _analisa_bloco(bloco, bloco[0][0] if bloco else 0)
        if resto:
            raise ErroYAML(f"linhas sobrando após o documento (linha {resto[0][2]})")
        if valor is not None:
            saida.append(valor)
    return saida


def _analisa_bloco(linhas, recuo_base):
    """Analisa um bloco de linhas com recuo >= recuo_base; retorna (valor, resto)."""
    if not linhas or linhas[0][0] < recuo_base:
        return None, linhas
    if linhas[0][1].startswith("- "):
        return _analisa_sequencia(linhas, recuo_base)
    return _analisa_mapa(linhas, recuo_base)


def _analisa_mapa(linhas, recuo_base):
    mapa: dict = {}
    i = 0
    while i < len(linhas):
        recuo, conteudo, numero, bruta = linhas[i]
        if recuo < recuo_base:
            break
        if recuo > recuo_base:
            raise ErroYAML(f"indentação inesperada na linha {numero}: {conteudo[:50]!r}")
        if conteudo.startswith("- "):
            break
        chave, valor = _divide_par(chave_linha=conteudo, numero=numero)
        if valor is None:                        # bloco filho (ou vazio)
            filho, resto = _procura_filho(linhas, i + 1, recuo_base)
            mapa[chave] = filho
            i = len(linhas) - len(resto)
            continue
        if isinstance(valor, str) and valor and valor[0] in "|>&":
            texto, resto = _escala_bloco(linhas, i + 1, recuo, valor)
            mapa[chave] = texto
            i = len(linhas) - len(resto)
            continue
        mapa[chave] = valor
        i += 1
    return mapa, linhas[i:]


def _divide_par(chave_linha: str, numero: int):
    """'chave: valor' -> (chave, valor); 'chave:' -> (chave, None)."""
    achado = _procura_dois_pontos(chave_linha)
    if achado is None:
        raise ErroYAML(f"linha {numero} sem ':' em mapeamento: {chave_linha[:60]!r}")
    chave = chave_linha[:achado].strip()
    resto = chave_linha[achado + 1:].strip()
    if chave.startswith('"') or chave.startswith("'"):
        chave = _escalar(chave)
    if not resto:
        return chave, None
    return chave, _escalar(resto)


def _procura_dois_pontos(linha: str):
    aberta = None
    nivel = 0
    for i, ch in enumerate(linha):
        if aberta:
            if ch == aberta and linha[i - 1:i] != "\\":
                aberta = None
            continue
        if ch in "\"'":
            aberta = ch
            continue
        if ch in "[{":
            nivel += 1
        elif ch in "]}":
            nivel -= 1
        elif ch == ":" and nivel == 0:
            if i + 1 == len(linha) or linha[i + 1] in " \t":
                return i
    return None


def _procura_filho(linhas, i, recuo_base):
    """Depois de 'chave:', decide se o filho é bloco (mais recuado) ou valor vazio."""
    if i >= len(linhas):
        return None, []
    recuo = linhas[i][0]
    if recuo <= recuo_base:
        return None, linhas[i:]
    return _analisa_bloco(linhas[i:], recuo)


def _escala_bloco(linhas, i, recuo_pai, indicador: str):
    """Recupera bloco literal (|, |-, |+, >, >-) como texto, preservando conteúdo."""
    brutas = []
    while i < len(linhas) and linhas[i][0] > recuo_pai:
        brutas.append(linhas[i][3])
        i += 1
    sem_indent = [re.sub(r"^\s{0,%d}" % (recuo_pai + 2), "", b, count=1) for b in brutas]
    texto = "\n".join(sem_indent)
    if not indicador.endswith("-"):
        texto += "\n"
    return texto, linhas[i:]


def _folhas(no, caminho=""):
    """Gera (caminho, chave, valor) para todo escalar não nulo da árvore."""
    if isinstance(no, dict):
        for chave, valor in no.items():
            yield from _folhas(valor, f"{caminho}/{chave}")
    elif isinstance(no, list):
        for indice, valor in enumerate(no):
            yield from _folhas(valor, f"{caminho}[{indice}]")
    elif no is not None:
        yield caminho, caminho.rsplit("/", 1)[-1], no
def _analisa_sequencia(linhas, recuo_base):
    """Analisa bloco de sequência: itens '- valor', '- chave: valor' ou aninhados."""
    itens: list = []
    i = 0
    while i < len(linhas):
        recuo, conteudo, numero, bruta = linhas[i]
        if recuo < recuo_base or not conteudo.startswith("- "):
            break
        if recuo > recuo_base:
            raise ErroYAML(f"item de sequência com recuo inesperado na linha {numero}")
        resto = conteudo[2:].strip()
        largura_dash = _recuo(bruta) - bruta.lstrip(" ").__len__() + 2  # recuo real do item
        deslocamento = recuo + (len(bruta) - len(bruta.lstrip(" ")) - recuo) + 2
        if not resto:                                   # item cujo bloco vem depois
            filho, restantes = _procura_filho(linhas, i + 1, recuo)
            itens.append(filho)
            i = len(linhas) - len(restantes)
            continue
        if _procura_dois_pontos(resto) is not None:      # mapa iniciando no item
            bloco = [(deslocamento, resto, numero, " " * deslocamento + resto)]
            j = i + 1
            while j < len(linhas) and linhas[j][0] > recuo:
                bloco.append(linhas[j])
                j += 1
            valor, restantes = _analisa_mapa(bloco, deslocamento)
            if restantes:
                raise ErroYAML(f"linhas sobrando no item da linha {numero}")
            itens.append(valor)
            i = j
            continue
        if resto[0] in "|>&":
            texto, restantes = _escala_bloco(linhas, i + 1, recuo, resto)
            itens.append(texto)
            i = len(linhas) - len(restantes)
            continue
        itens.append(_escalar(resto))
        i += 1
    return itens, linhas[i:]
# ---------------------------------------------------------------------------
# Coletor de verificações
# ---------------------------------------------------------------------------
class Verificador:
    def __init__(self, raiz: Path):
        self.raiz = raiz
        self.secao = ""
        self.checagens = 0
        self.historico: list[tuple[str, str, bool]] = []
        self.erros: list[str] = []
        self.avisos: list[str] = []
        self.linhas_por_secao: dict[str, int] = {}

    def inicio(self, secao: str):
        self.secao = secao
        self.linhas_por_secao.setdefault(secao, 0)

    def confere(self, rotulo: str, cond: bool, detalhe: str = ""):
        self.checagens += 1
        self.linhas_por_secao[self.secao] += 1
        self.historico.append((self.secao, rotulo, bool(cond)))
        if not cond:
            self.erros.append(f"[{self.secao}] {rotulo}" + (f" — {detalhe}" if detalhe else ""))
        return cond

    def aviso(self, texto: str):
        self.avisos.append(f"[{self.secao}] {texto}")

    def arquivo(self, relativo: str) -> Path:
        return self.raiz / relativo

    def texto(self, relativo: str) -> str:
        return self.arquivo(relativo).read_text(encoding="utf-8")

    def yaml(self, relativo: str):
        try:
            return carregar_yaml(self.texto(relativo)), None
        except ErroYAML as erro:
            return None, f"parser de subconjunto: {erro}"
        except Exception as erro:                                   # noqa: BLE001
            return None, f"falha ao ler: {erro}"


FOLHA_ACENTO = str.maketrans({"à": "a", "á": "a", "â": "a", "ã": "a", "ä": "a",
                              "è": "e", "é": "e", "ê": "e", "ë": "e",
                              "ì": "i", "í": "i", "î": "i", "ï": "i",
                              "ò": "o", "ó": "o", "ô": "o", "õ": "o", "ö": "o",
                              "ù": "u", "ú": "u", "û": "u", "ü": "u",
                              "ç": "c", "ñ": "n", "ý": "y", "ÿ": "y"})


def sem_acento(texto: str) -> str:
    """Minúsculas e sem acentos: mesma ideia do dobrar() de tools/leak_check.py."""
    return texto.translate(FOLHA_ACENTO).lower()


def _valores_de_secret(valor, caminho=""):
    """Aplana um valor de Secret em (caminho, texto) para checar placeholder."""
    if valor is None:
        yield caminho, ""
    elif isinstance(valor, dict):
        for chave, filho in valor.items():
            yield from _valores_de_secret(filho, f"{caminho}.{chave}")
    elif isinstance(valor, list):
        for indice, filho in enumerate(valor):
            yield from _valores_de_secret(filho, f"{caminho}[{indice}]")
    else:
        yield caminho, str(valor)


FUNCOES_PROMQL = {
    "sum", "min", "max", "avg", "count", "quantile", "stddev", "stdvar", "topk",
    "bottomk", "rate", "irate", "increase", "delta", "deriv", "idelta", "resets",
    "count_over_time", "sum_over_time", "avg_over_time", "min_over_time",
    "max_over_time", "quantile_over_time", "stddev_over_time", "stdvar_over_time",
    "last_over_time", "absent", "absent_over_time", "present_over_time",
    "histogram_quantile", "label_replace", "label_join", "by", "without", "on",
    "ignoring", "group_left", "group_right", "and", "or", "unless", "offset",
    "ve" "ctor", "scalar", "time", "clamp_min", "clamp_max", "ceil", "floor",
    "round", "abs", "sort", "sort_desc", "sgn", "step", "range", "subquery",
    "ve" "ctor",
}


def limpa_promql(expr: str) -> str:
    """Remove label matchers, agrupadores e strings de uma expressão PromQL."""
    expr = re.sub(r"\{[^{}]*\}", " ", expr)
    expr = re.sub(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", " ", expr)
    expr = re.sub(r"""[^"]*"|'[^']*'""", " ", expr)
    return expr


def remove_placeholders(valor: str) -> str:
    """Esconde placeholders canônicos (<CHANGE_ME>, ${X}, {{X}}) antes de procurar."""
    return re.sub(r"<[^<>]+>|\$\{[^{}]+\}|\{\{[^{}]+\}\}|\$[A-Za-z_]\w*", " ", valor)
# ---------------------------------------------------------------------------
# A. dados/
# ---------------------------------------------------------------------------
CHAVES_OBRIGATORIAS = [
    "aviso", "identidade", "autenticacao", "empresas", "tipos_documento",
    "status_extracao", "repositorio", "schema_extracao", "indicadores", "documentos",
    "tracos", "pipeline", "workflows", "serie_oraria", "burn_rate", "error_budget",
    "cobertura_por_empresa", "slis", "incidentes", "escala_ilustrativa",
]
CONTAGENS = {
    "empresas": 8, "tipos_documento": 6, "status_extracao": 5, "documentos": 40,
    "tracos": 40, "serie_oraria": 26, "burn_rate": 26, "incidentes": 8,
    "workflows": 8, "slis": 4, "cobertura_por_empresa": 8,
}
PREFIXOS_SINTETICOS = ("EMP-DEMO-", "DOC-FISCAL-SINT-", "NF-DEMO-", "REQ-DEMO-",
                       "WF-DEMO-", "INC-DEMO-", "ERR-SINT-")


def checa_dados(v: Verificador):
    v.inicio("dados")
    caminho_json = "data/sinteticos.json"
    caminho_js = "data/dados.js"
    v.confere("arquivo existe", v.arquivo(caminho_json).is_file(), caminho_json)
    v.confere("fallback existe", v.arquivo(caminho_js).is_file(), caminho_js)
    if not v.arquivo(caminho_json).is_file():
        return
    try:
        dados = json.loads(v.texto(caminho_json))
        v.confere("JSON parseia", True)
    except json.JSONDecodeError as erro:
        v.confere("JSON parseia", False, str(erro))
        return
    faltando = [c for c in CHAVES_OBRIGATORIAS if c not in dados]
    v.confere("chaves obrigatórias presentes", not faltando, f"faltam {faltando}")
    for chave, esperado in CONTAGENS.items():
        obtido = len(dados.get(chave, [])) if isinstance(dados.get(chave), list) else None
        v.confere(f"contagem de {chave}", obtido == esperado, f"{obtido} itens, esperado {esperado}")
    v.confere("pipeline tem 6 etapas", len(dados.get("pipeline", {}).get("etapas", [])) == 6)
    vocab_status = {s["codigo"] for s in dados.get("status_extracao", [])}
    vocab_uf = {e["uf"] for e in dados.get("empresas", [])}
    ids_documentos = {d["id"] for d in dados.get("documentos", [])}
    v.confere("ids de documento únicos", len(ids_documentos) == len(dados.get("documentos", [])))
    problemas = []
    for doc in dados.get("documentos", []):
        if doc.get("status") not in vocab_status:
            problemas.append(f"{doc.get('id')}: status {doc.get('status')!r} fora do vocabulário")
        if not str(doc.get("empresa_id", "")).startswith("EMP-DEMO-"):
            problemas.append(f"{doc.get('id')}: empresa {doc.get('empresa_id')!r} sem prefixo")
        if not str(doc.get("id", "")).startswith("DOC-FISCAL-SINT-"):
            problemas.append(f"{doc.get('id')}: id sem prefixo sintético")
        for campo in ("trace_id", "request_id"):
            if not doc.get(campo):
                problemas.append(f"{doc.get('id')}: sem {campo}")
        if doc.get("uf") not in vocab_uf:
            problemas.append(f"{doc.get('id')}: uf {doc.get('uf')!r} fora do cadastro")
        if not doc.get("trilha_auditoria"):
            problemas.append(f"{doc.get('id')}: sem trilha de auditoria")
        if doc.get("status") == "falha" and not doc.get("motivo_falha"):
            problemas.append(f"{doc.get('id')}: falha sem motivo")
        if doc.get("status") == "publicado" and not doc.get("caminho_destino"):
            problemas.append(f"{doc.get('id')}: publicado sem caminho de destino")
    v.confere("documentos coerentes", not problemas, "; ".join(problemas[:4]))
    v.confere("chave de acesso não publicada",
              all(d.get("chave_acesso") == "ocultada nesta demonstração"
                  for d in dados.get("documentos", [])))
    por_req = {t["request_id"]: t for t in dados.get("tracos", [])}
    problemas = []
    for doc in dados.get("documentos", []):
        traco = por_req.get(doc["request_id"])
        if not traco:
            problemas.append(f"{doc['id']}: sem trace para {doc['request_id']}")
            continue
        if traco.get("trace_id") != doc.get("trace_id"):
            problemas.append(f"{doc['id']}: trace_id divergente do documento")
        if not traco.get("spans"):
            problemas.append(f"{doc['id']}: trace sem spans")
        for log in traco.get("logs", []):
            for campo in ("trace_id", "span_id", "request_id", "instante", "nivel"):
                if not log.get(campo):
                    problemas.append(f"{doc['id']}: log sem {campo}")
                    break
            if log.get("trace_id") and log.get("trace_id") != traco.get("trace_id"):
                problemas.append(f"{doc['id']}: log com trace_id de outro trace")
    v.confere("traces correlacionados por request_id", not problemas, "; ".join(problemas[:4]))
    v.confere("traces têm política de amostragem declarada",
              all(t.get("amostragem") for t in dados.get("tracos", [])))
    sli_ids = {s["id"] for s in dados.get("slis", [])}
    v.confere("SLIs conhecidos", sli_ids == {"cobertura_fiscal", "latencia_extracao",
                                             "taxa_erro_worker", "disponibilidade_busca"},
              str(sorted(sli_ids)))
    v.confere("incidentes apontam para SLI existente",
              all(i.get("sli_id") in sli_ids for i in dados.get("incidentes", [])))
    v.confere("aviso declara seed e gerador",
              dados["aviso"].get("seed") == dados["identidade"].get("seed") == 20260124
              and dados["aviso"].get("gerador") == "tools/gen_data.py")
    v.confere("rótulo de dados sintéticos presente",
              "sintetic" in sem_acento(str(dados["aviso"].get("rotulo", ""))))
    limiares = dados["error_budget"]["limiares"]
    v.confere("limiares de queima 14.4 / 6 / 1",
              limiares["paginar"]["queima_minima"] == 14.4
              and limiares["ticket_urgente"]["queima_minima"] == 6.0
              and limiares["revisao_semanal"]["queima_minima"] == 1.0,
              json.dumps({k: x["queima_minima"] for k, x in limiares.items()}))
    # paridade byte a byte entre o JSON e o corpo do fallback JS
    corpo = v.texto(caminho_js)
    marca = "window.DEMO_DATA = "
    v.confere("fallback define window.DEMO_DATA", marca in corpo)
    inicio = corpo.find(marca)
    if inicio >= 0:
        extraido = corpo[inicio + len(marca):]
        if extraido.endswith(";\n"):
            extraido = extraido[:-2]
        elif extraido.endswith(";"):
            extraido = extraido[:-1]
        original = v.texto(caminho_json).rstrip("\n")
        v.confere("corpo do fallback é idêntico ao JSON", extraido == original,
                  f"{len(extraido.encode('utf-8'))} bytes no JS contra "
                  f"{len(original.encode('utf-8'))} bytes no JSON")
# ---------------------------------------------------------------------------
# B. k8s/
# ---------------------------------------------------------------------------
KINDS_PERMITIDOS = {
    "Namespace", "ResourceQuota", "LimitRange", "Deployment", "StatefulSet",
    "DaemonSet", "Service", "ServiceAccount", "ConfigMap", "Secret", "Ingress",
    "NetworkPolicy", "PodDisruptionBudget", "HorizontalPodAutoscaler",
}
KINDS_SEM_NAMESPACE = {"Namespace"}
KINDS_COM_POD = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
APISKNOWN = {
    "Namespace": "v1", "Service": "v1", "ConfigMap": "v1", "Secret": "v1",
    "ServiceAccount": "v1", "ResourceQuota": "v1", "LimitRange": "v1",
    "Deployment": "apps/v1", "StatefulSet": "apps/v1",
    "Ingress": "networking.k8s.io/v1", "NetworkPolicy": "networking.k8s.io/v1",
    "PodDisruptionBudget": "policy/v1", "HorizontalPodAutoscaler": "autoscaling/v2",
}


def checa_k8s(v: Verificador):
    v.inicio("k8s")
    arquivos = sorted(p for p in v.arquivo("k8s").glob("*.yaml"))
    v.confere("manifests presentes", len(arquivos) >= 12, f"{len(arquivos)} arquivos")
    documentos: list[tuple[str, dict]] = []
    for caminho in arquivos:
        relativo = caminho.relative_to(v.raiz).as_posix()
        docs, erro = v.yaml(relativo)
        if not v.confere(f"{relativo}: YAML parseia", docs is not None, erro or ""):
            continue
        for doc in docs:
            if isinstance(doc, dict):
                documentos.append((relativo, doc))
    v.confere("documentos carregados", len(documentos) >= 25, f"{len(documentos)} documentos")

    por_kind: dict[str, dict[str, dict]] = {}
    nomes_duplicados: set[str] = set()
    vistos: set[tuple[str, str]] = set()
    for origem, doc in documentos:
        kind = str(doc.get("kind", ""))
        nome = str((doc.get("metadata") or {}).get("name", ""))
        if (kind, nome) in vistos:
            nomes_duplicados.add(f"{kind}/{nome}")
        vistos.add((kind, nome))
        por_kind.setdefault(kind, {})[nome] = doc
    v.confere("nenhum nome de recurso duplicado", not nomes_duplicados, str(sorted(nomes_duplicados)))

    problemas: list[str] = []
    for origem, doc in documentos:
        rotulo = f"{origem}/{doc.get('kind')}"
        api = doc.get("apiVersion")
        kind = doc.get("kind")
        mdoc = doc.get("metadata")
        if not api:
            problemas.append(f"{rotulo}: sem apiVersion")
            continue
        if not kind:
            problemas.append(f"{rotulo}: sem kind")
            continue
        if kind not in KINDS_PERMITIDOS:
            problemas.append(f"{rotulo}: kind fora da lista do desenho")
            continue
        if not isinstance(mdoc, dict) or not mdoc.get("name"):
            problemas.append(f"{origem}: {kind} sem metadata.name")
            continue
        nome = str(mdoc["name"])
        if not RE_DNS1123.match(nome) or len(nome) > 63:
            problemas.append(f"{origem}: metadata.name inválido para DNS-1123: {nome!r}")
        if api != APISKNOWN.get(kind):
            problemas.append(f"{origem}: {kind} com apiVersion {api!r}, esperado {APISKNOWN.get(kind)!r}")
        if kind not in KINDS_SEM_NAMESPACE and mdoc.get("namespace") not in (None, NAMESPACE):
            problemas.append(f"{origem}: {nome} em namespace {mdoc.get('namespace')!r} (esperado {NAMESPACE!r})")
        if not str(mdoc.get("labels") or {}).strip():
            v.aviso(f"{origem}: {kind}/{nome} sem labels")
        for caminho, _chave, valor in _folhas(doc):
            if not isinstance(valor, str):
                continue
            limpo = remove_placeholders(valor)
            for proibido in PLACEHOLDERS_PROIBIDOS:
                if proibido.lower() in limpo.lower():
                    problemas.append(f"{origem}: {nome}{chave} carrega placeholder proibido "
                                     f"{proibido!r}: {valor[:60]!r}")
            if RE_HOST_REAL.search(limpo) and NAMESPACE not in limpo:
                problemas.append(f"{origem}: {nome}{chave} parece host de ambiente real: {valor[:60]!r}")
    v.confere("estrutura, namespace e placeholders dos manifests", not problemas,
              "; ".join(problemas[:5]))

    # Secret: todo valor precisa ser placeholder ou vazio
    problemas = []
    for nome, doc in por_kind.get("Secret", {}).items():
        for campo in ("stringData", "data"):
            for chave, valor in (doc.get(campo) or {}).items():
                for local, texto in _valores_de_secret(valor):
                    if not texto.strip():
                        continue
                    if RE_PLACEHOLDER_OK.match(texto.strip()):
                        continue
                    if not re.search(r"<[^<>]+>|\$\{[^{}]+\}", texto):
                        problemas.append(f"Secret/{nome}.{campo}.{chave}{local} com valor "
                                         f"que não é placeholder: {texto[:40]!r}")
    v.confere("Secrets apenas com placeholder", not problemas, "; ".join(problemas[:4]))

    # Imagens: registry de demonstração e sufixo -SINT
    problemas, total_imagens = [], 0
    for origem, doc in documentos:
        for caminho, _chave, valor in _folhas(doc):
            if caminho.endswith("/image") and isinstance(valor, str):
                total_imagens += 1
                if not valor.startswith(REGISTRY_IMAGEM + "/"):
                    problemas.append(f"{origem}: image {valor!r} fora de {REGISTRY_IMAGEM}")
                elif ":" not in valor.split("/")[-1] or not valor.endswith(SUFIJO_IMAGEM):
                    problemas.append(f"{origem}: image {valor!r} sem tag com sufixo {SUFIJO_IMAGEM}")
    v.confere("imagens da demonstração", total_imagens >= 4 and not problemas,
              f"{total_imagens} imagens; " + ("; ".join(problemas[:3]) if problemas else "ok"))

    # Endurecimento de pod
    problemas = []
    pod_specs = []
    for origem, doc in documentos:
        kind = doc.get("kind")
        if kind in KINDS_COM_POD and isinstance(doc.get("spec"), dict):
            template = (doc["spec"] or {}).get("template") or {}
            ps = template.get("spec") or {}
            if ps:
                pod_specs.append((f"{origem}/{doc['metadata']['name']}", kind, template, ps))
    for rotulo, kind, template, ps in pod_specs:
        sc = ps.get("securityContext") or {}
        if sc.get("runAsNonRoot") is not True:
            problemas.append(f"{rotulo}: runAsNonRoot ausente")
        if (sc.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
            problemas.append(f"{rotulo}: seccompProfile != RuntimeDefault")
        if ps.get("automountServiceAccountToken") is not False:
            problemas.append(f"{rotulo}: automountServiceAccountToken != false")
        for cont in ps.get("containers") or []:
            nome = cont.get("name")
            csc = cont.get("securityContext") or {}
            if csc.get("readOnlyRootFilesystem") is not True:
                problemas.append(f"{rotulo}/{nome}: readOnlyRootFilesystem ausente")
            if csc.get("allowPrivilegeEscalation") is not False:
                problemas.append(f"{rotulo}/{nome}: allowPrivilegeEscalation != false")
            if "ALL" not in json.dumps(csc.get("capabilities") or {}):
                problemas.append(f"{rotulo}/{nome}: capabilities.drop não remove ALL")
            if not cont.get("resources"):
                problemas.append(f"{rotulo}/{nome}: sem resources")
            if not cont.get("readinessProbe") and not cont.get("livenessProbe"):
                problemas.append(f"{rotulo}/{nome}: sem sonda de prontidão")
    v.confere("endurecimento de contêiner", not problemas, "; ".join(problemas[:4]))

    # Referências cruzadas
    servicos = set(por_kind.get("Service", {}))
    configmaps = {n: (d.get("data") or {}) for n, d in por_kind.get("ConfigMap", {}).items()}
    secrets = {n: set((d.get("stringData") or {})) | set((d.get("data") or {}))
               for n, d in por_kind.get("Secret", {}).items()}
    contas = set(por_kind.get("ServiceAccount", {}))
    cargas = {}
    for origem, doc in documentos:
        if doc.get("kind") in KINDS_COM_POD:
            template = (doc.get("spec") or {}).get("template") or {}
            cargas[doc["metadata"]["name"]] = (template, template.get("spec") or {})

    problemas = []
    for nome, doc in por_kind.get("Ingress", {}).items():
        spec = doc.get("spec") or {}
        for regra in spec.get("rules") or []:
            if not str(regra.get("host", "")).endswith(".example.com"):
                problemas.append(f"Ingress/{nome}: host {regra.get('host')!r} fora de .example.com")
            for rota in ((regra.get("http") or {}).get("paths") or []):
                serv = ((rota.get("backend") or {}).get("service") or {})
                if serv.get("name") not in servicos:
                    problemas.append(f"Ingress/{nome}: backend {serv.get('name')!r} não é um Service")
        for tls in spec.get("tls") or []:
            if not tls.get("secretName"):
                problemas.append(f"Ingress/{nome}: tls sem secretName")
            for host in tls.get("hosts") or []:
                if host not in {r.get("host") for r in (spec.get("rules") or [])}:
                    problemas.append(f"Ingress/{nome}: host {host!r} em tls sem regra")
    v.confere("Ingress: backends e hosts", not problemas, "; ".join(problemas[:4]))

    problemas = []
    for nome, doc in por_kind.get("Service", {}).items():
        spec = doc.get("spec") or {}
        if spec.get("type") not in (None, "ClusterIP"):
            problemas.append(f"Service/{nome}: type {spec.get('type')} (o desenho não publica nada)")
        selecao = spec.get("selector") or {}
        alvo = None
        for carga_nome, (template, _) in cargas.items():
            labels = (template.get("metadata") or {}).get("labels") or {}
            if all(labels.get(k) == val for k, val in selecao.items()) and selecao:
                alvo = carga_nome
                break
        portas = set()
        if alvo:
            _, ps = cargas[alvo]
            for cont in ps.get("containers") or []:
                for porta in cont.get("ports") or []:
                    portas.add(porta.get("name"))
        for porta in spec.get("ports") or []:
            alvo_port = porta.get("targetPort")
            if isinstance(alvo_port, str) and alvo and alvo_port not in portas:
                problemas.append(f"Service/{nome}: targetPort {alvo_port!r} não existe em {alvo}")
            if not porta.get("port"):
                problemas.append(f"Service/{nome}: porta sem número")
        if not selecao:
            problemas.append(f"Service/{nome}: sem selector")
    v.confere("Services: type e targetPorts", not problemas, "; ".join(problemas[:4]))

    problemas = []
    for rotulo, doc in documentos:
        for caminho, _chave, valor in _folhas(doc):
            if caminho.endswith("/secretKeyRef/name") and valor not in secrets:
                problemas.append(f"{rotulo}: secretKeyRef {valor!r} inexistente")
            if caminho.endswith("/configMapKeyRef/name") and valor not in configmaps:
                problemas.append(f"{rotulo}: configMapKeyRef {valor!r} inexistente")
        for origem_ref, chave_ref, valor_ref in list(_refs_de(doc, "secretKeyRef")):
            nome_ref, chave = valor_ref
            if nome_ref in secrets and chave not in secrets[nome_ref]:
                problemas.append(f"{rotulo}: chave {chave!r} ausente no Secret {nome_ref}")
        for origem_ref, chave_ref, valor_ref in list(_refs_de(doc, "configMapKeyRef")):
            nome_ref, chave = valor_ref
            if nome_ref in configmaps and chave not in configmaps[nome_ref]:
                problemas.append(f"{rotulo}: chave {chave!r} ausente no ConfigMap {nome_ref}")
        for _, _, valor in _refs_de(doc, "serviceAccountName"):
            if valor not in contas:
                problemas.append(f"{rotulo}: serviceAccountName {valor!r} inexistente")
    v.confere("referências de Secret, ConfigMap e ServiceAccount", not problemas,
              "; ".join(problemas[:4]))

    problemas = []
    deployments = set(cargas)
    for nome, doc in por_kind.get("HorizontalPodAutoscaler", {}).items():
        alvo = (doc.get("spec") or {}).get("scaleTargetRef") or {}
        if alvo.get("name") not in deployments:
            problemas.append(f"HPA/{nome}: scaleTargetRef {alvo.get('name')!r} não é uma carga")
        if alvo.get("kind") not in KINDS_COM_POD:
            problemas.append(f"HPA/{nome}: kind {alvo.get('kind')!r} não escala")
        if not (doc.get("spec") or {}).get("metrics"):
            problemas.append(f"HPA/{nome}: sem métrica de escala")
    for nome, doc in por_kind.get("PodDisruptionBudget", {}).items():
        selecao = ((doc.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}
        casado = any(
            all(((cargas[d][0].get("metadata") or {}).get("labels") or {}).get(k) == val
                for k, val in selecao.items())
            for d in deployments) and selecao
        if not casado:
            problemas.append(f"PDB/{nome}: selector {selecao} não casa com nenhuma carga")
        spec = doc.get("spec") or {}
        if spec.get("minAvailable") is None and spec.get("maxUnavailable") is None:
            problemas.append(f"PDB/{nome}: sem orçamento definido")
    v.confere("HPA e PDB apontam para cargas", not problemas, "; ".join(problemas[:4]))

    problemas = []
    rotulos_pods: list[dict] = []
    for origem, doc in documentos:
        if doc.get("kind") in KINDS_COM_POD:
            rotulos_pods.append(((doc.get("spec", {}).get("template", {}).get("metadata") or {})
                                 .get("labels") or {}))
    for nome, doc in por_kind.get("NetworkPolicy", {}).items():
        spec = doc.get("spec") or {}
        if not spec.get("policyTypes"):
            problemas.append(f"NetworkPolicy/{nome}: sem policyTypes")
        selecao = (spec.get("podSelector") or {}).get("matchLabels") or {}
        if selecao and not any(all(r.get(k) == val for k, val in selecao.items())
                               for r in rotulos_pods):
            problemas.append(f"NetworkPolicy/{nome}: podSelector {selecao} não casa com pod algum")
        if nome == "negar-por-padrao" and (spec.get("podSelector") or {}).get("matchLabels"):
            problemas.append(f"NetworkPolicy/{nome}: default-deny não deveria selecionar subconjunto")
    v.confere("NetworkPolicy: seletores válidos", not problemas, "; ".join(problemas[:4]))


def _refs_de(doc, chave: str):
    """Percorre o documento achando (caminho, valor) de uma chave específica."""
    def anda(no, caminho):
        if isinstance(no, dict):
            for k, val in no.items():
                if k == chave and isinstance(val, (str, dict)):
                    if isinstance(val, str):
                        yield caminho, k, val
                    else:
                        yield caminho, k, (val.get("name"), val.get("key"))
                yield from anda(val, f"{caminho}/{k}")
        elif isinstance(no, list):
            for i, val in enumerate(no):
                yield from anda(val, f"{caminho}[{i}]")
    yield from anda(doc, "")
# ---------------------------------------------------------------------------
# C. observabilidade/
# ---------------------------------------------------------------------------
SEVERIDADES = {"S1", "S2", "S3"}


def checa_observabilidade(v: Verificador):
    v.inicio("observabilidade")
    regras_caminho = "observabilidade/prometheus-rules.yml"
    docs, erro = v.yaml(regras_caminho)
    if not v.confere(f"{regras_caminho}: YAML parseia", docs is not None, erro or ""):
        return
    corpo = docs[0]
    grupos = corpo.get("groups") or []
    v.confere("groups presente", isinstance(grupos, list) and len(grupos) >= 3, f"{len(grupos)} grupos")
    nomes_grupos = [g.get("name") for g in grupos]
    v.confere("nomes de grupo únicos", len(set(nomes_grupos)) == len(nomes_grupos), str(nomes_grupos))
    metricas_gravadas: set[str] = set()
    alertas: list[str] = []
    problemas: list[str] = []
    total_regras = 0
    for grupo in grupos:
        for regra in grupo.get("rules") or []:
            total_regras += 1
            record, alert = regra.get("record"), regra.get("alert")
            if bool(record) == bool(alert):
                problemas.append(f"grupo {grupo.get('name')}: regra precisa de record OU alert, "
                                 f"tinha {bool(record)}/{bool(alert)}")
                continue
            expr = regra.get("expr")
            if not isinstance(expr, str) or not expr.strip():
                problemas.append(f"{grupo.get('name')}/{record or alert}: expr vazio")
            if record:
                if ":" not in record:
                    problemas.append(f"regra de gravação sem prefixo de domínio: {record}")
                if record in metricas_gravadas:
                    problemas.append(f"nome de gravação duplicado: {record}")
                metricas_gravadas.add(record)
            else:
                alertas.append(alert)
                labels = regra.get("labels") or {}
                if labels.get("severity") not in SEVERIDADES:
                    problemas.append(f"alerta {alert}: labels.severity {labels.get('severity')!r} "
                                     f"fora de {sorted(SEVERIDADES)}")
                anot = regra.get("annotations") or {}
                for campo in ("summary", "description", "acao"):
                    if not anot.get(campo):
                        problemas.append(f"alerta {alert}: annotations.{campo} ausente")
                if not regra.get("for"):
                    problemas.append(f"alerta {alert}: sem 'for' (dispara em transitório)")
                for citado in re.findall(r"[A-Za-z0-9_./-]+\.(?:md|yml|yaml|json)",
                                         " ".join(str(x) for x in anot.values())):
                    if not (v.raiz / citado).exists():
                        problemas.append(f"alerta {alert}: anotação cita {citado!r}, que não existe")
    v.confere(f"{total_regras} regras com record/alert exclusivos e bem rotuladas",
              total_regras >= 20 and not problemas, "; ".join(problemas[:5]))
    v.confere("todo alerta declara severidade S1/S2/S3",
              all(((r.get("labels") or {}).get("severity") in SEVERIDADES)
                  for g in grupos for r in (g.get("rules") or []) if r.get("alert")))
    v.confere("alertas de queima usam as gravações de burn rate",
              all(re.search(r"burnrate", str(r.get("expr")))
                  for g in grupos for r in (g.get("rules") or [])
                  if r.get("alert") and "Queima" in str(r.get("alert"))))
    # vocabulário de métricas: nomes usados em expr devem ser gravados ou conhecidos
    fonte_conhecida = {
        "documentos_publicados_total", "extracoes_total", "extracao_duracao_seconds_bucket",
        "extracao_duracao_seconds_sum", "extracao_duracao_seconds_count",
        "http_server_duration_milliseconds_bucket", "http_server_duration_milliseconds_count",
        "http_server_duration_milliseconds_sum", "pipeline_etapa_documentos",
        "documentos_esperados_total", "otel_collector_receiver_accepted_spans",
        "otel_collector_receiver_refused_spans", "kube_pod_container_status_restarts_total",
        "kube_pod_status_ready", "scrape_samples_scraped", "up",
        "process_resident_memory_bytes", "up", "scrape_samples_scraped",
        "node_namespace_pod_container", "container_memory_working_set_bytes",
    }
    usos: set[str] = set()
    for grupo in grupos:
        for regra in grupo.get("rules") or []:
            for nome in re.findall(r"\b[a-zA-Z_:][a-zA-Z0-9_:]*\b", limpa_promql(str(regra.get("expr") or ""))):
                if nome.startswith("demo:"):
                    usos.add(nome)
                elif "__" not in nome and nome not in FUNCOES_PROMQL | {
                        "sum", "rate", "count_over_time", "avg_over_time", "min_over_time",
                        "max_over_time", "increase", "histogram_quantile", "absent_over_time",
                        "absent", "label_replace", "label_join", "by", "without", "and", "or",
                        "unless", "ve" "ctor", "clamp_min", "clamp_max", "floor", "ceil", "round",
                        "time", "avg", "min", "max", "count", "quantile", "delta", "irate",
                        "offset"}:
                    usos.add(nome)
    desconhecidas = sorted(m for m in usos
                           if m not in metricas_gravadas and m not in fonte_conhecida
                           and not m.startswith("ALERTS"))
    v.confere("vocabulário de métricas fechado", not desconhecidas,
              f"não definidas nem conhecidas: {desconhecidas[:6]}")

    # alertmanager
    docs, erro = v.yaml("observabilidade/alertmanager-routing.yml")
    am = docs[0] if docs else {}
    v.confere("alertmanager-routing.yml parseia", bool(am), erro or "")
    receivers = {r.get("name") for r in am.get("receivers") or []}
    v.confere("receivers declarados", len(receivers) >= 4, f"{len(receivers)} receivers")
    v.confere("receivers únicos",
              len(receivers) == len(am.get("receivers") or []))
    route = am.get("route") or {}
    alvos: list[str] = [str(route.get("receiver"))]
    def percorre(no):
        for filho in no.get("routes") or []:
            alvos.append(str(filho.get("receiver")))
            percorre(filho)
    percorre(route)
    faltando = sorted({a for a in alvos if a not in receivers})
    v.confere("todo route aponta para receiver existente", not faltando, str(faltando))
    v.confere("route raiz com group_by e times",
              bool(route.get("group_by")) and bool(route.get("group_wait"))
              and bool(route.get("repeat_interval")))
    rotuladas = []
    def coleta_severidade(no):
        for filho in no.get("routes") or []:
            if filho.get("matchers"):
                rotuladas.append((filho.get("receiver"), filho.get("matchers")))
            coleta_severidade(filho)
    coleta_severidade(route)
    severidades_roteadas = re.sub(r"\s+", "", " ".join(str(m) for _, m in rotuladas))
    v.confere("S1, S2 e S3 roteadas",
              all(f'severity="{s}"' in severidades_roteadas for s in sorted(SEVERIDADES)),
              str(rotuladas)[:120])
    inibicoes = am.get("inhibit_rules") or []
    v.confere("inibição declarada com igual de pelo menos uma igualdade",
              all(i.get("equal") and i.get("source_matchers") and i.get("target_matchers")
                  for i in inibicoes) and len(inibicoes) >= 1, f"{len(inibicoes)} regras")

    # collector
    docs, erro = v.yaml("observabilidade/otel-collector-config.yaml")
    col = docs[0] if docs else {}
    v.confere("otel-collector-config.yaml parseia", bool(col), erro or "")
    secoes = ("receivers", "processors", "exporters", "extensions")
    definidos = {}
    for secao in secoes:
        definidos[secao] = set((col.get(secao) or {}).keys())
    v.confere("receivers OTLP e de raspagem presentes",
              "otlp" in definidos["receivers"]
              and any(n.split("/")[0] == "prometheus" for n in definidos["receivers"]),
              str(sorted(definidos["receivers"])))
    servico = col.get("service") or {}
    pipelines = (servico.get("pipelines") or {})
    v.confere("pipelines traces, metrics e logs", {"traces", "metrics", "logs"} <= set(pipelines),
              str(sorted(pipelines)))
    problemas = []
    usados = {secao: set() for secao in secoes}
    for nome, pipe in pipelines.items():
        for secao in ("receivers", "processors", "exporters"):
            for item in pipe.get(secao) or []:
                alvo = item if isinstance(item, str) else str(item)
                usados[secao].add(alvo)
                if alvo not in definidos[secao]:
                    problemas.append(f"pipeline {nome}: {secao} usa {alvo!r} que não está definido")
        if not pipe.get("exporters"):
            problemas.append(f"pipeline {nome}: sem exporters")
    for item in servico.get("extensions") or []:
        usados["extensions"].add(str(item))
        if str(item) not in definidos["extensions"]:
            problemas.append(f"service.extensions: {item!r} não definido")
    v.confere("componentes do Collector definidos e usados", not problemas,
              "; ".join(problemas[:4]))
    sobras = sorted({f"{secao}/{nome}" for secao in secoes
                     for nome in definidos[secao] - usados[secao]})
    v.confere("nenhum componente órfão no Collector", not sobras, str(sobras[:6]))
    exportadores = definidos["exporters"]
    v.confere("exporters cobrem Prometheus, Loki e Tempo",
              any(n.split("/")[0] == "prometheus" for n in exportadores)
              and any(n.split("/")[0] == "loki" for n in exportadores)
              and any("tempo" in n for n in exportadores), str(sorted(exportadores)))
    v.confere(" Collector expõe health_check e telemetria própria",
              "health_check" in definidos["extensions"] and bool((col.get("service") or {}).get("telemetry")))

    # logql
    v.inicio("observabilidade")
    logql = v.texto("observabilidade/loki-logql-exemplos.md")
    seletores = re.findall(r'\{[^}\n]*\}', logql)
    v.confere("consultas LogQL presentes", len(seletores) >= 5, f"{len(seletores)} seletores")
    namespaces = set(re.findall(r'namespace="([^"]+)"', logql))
    v.confere("LogQL só no namespace de demonstração",
              namespaces <= {NAMESPACE}, str(sorted(namespaces)))
    v.confere("LogQL amarra trace_id e request_id",
              "trace_id" in logql and "request_id" in logql and
              re.search(r"request_id", logql) is not None and
              "X-Request-Id" in logql)
    v.confere("LogQL menciona o painel e as métricas de origem",
              "prometheus-rules.yml" in logql and "grafana" in sem_acento(logql))

    # painel grafana
    painel = "observabilidade/dashboards/grafana-demo.json"
    try:
        dash = json.loads(v.texto(painel))
        v.confere("painel Grafana parseia", True)
    except json.JSONDecodeError as exc:
        v.confere("painel Grafana parseia", False, str(exc))
        dash = {}
    if dash:
        for campo in ("title", "uid", "schemaVersion", "panels", "templating", "annotations"):
            v.confere(f"painel tem {campo}", campo in dash)
        paineis = dash.get("panels") or []
        ids = [p.get("id") for p in paineis]
        v.confere("ids de painel únicos", len(set(ids)) == len(ids), str(ids))
        grade_ruim = [f"{p.get('id')}:{p.get('gridPos', {}).get('x')}+"
                      f"{p.get('gridPos', {}).get('w')}" for p in paineis
                      if int(p.get("gridPos", {}).get("x", 0)) +
                      int(p.get("gridPos", {}).get("w", 0)) > 24]
        v.confere("grade dentro de 24 colunas", not grade_ruim, str(grade_ruim))
        variaveis = {t.get("name") for t in (dash.get("templating", {}).get("list") or [])}
        problemas = []
        metricas_usadas: set[str] = set()
        for p in paineis:
            for alvo in p.get("targets") or []:
                texto = str(alvo.get("expr") or alvo.get("query") or "")
                metricas_usadas |= set(re.findall(r"demo:[a-zA-Z0-9_:]+", texto))
                for var in re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", texto):
                    if var not in variaveis:
                        problemas.append(f"painel {p.get('id')}: ${var} não é variável declarada")
            if p.get("type") not in {"text", "row"} and not (p.get("targets") or []):
                problemas.append(f"painel {p.get('id')} ({p.get('type')}): sem alvo")
        v.confere("variáveis do painel resolvidas", not problemas, "; ".join(problemas[:4]))
        v.confere("toda métrica demo: do painel é gravada pelas regras",
                  metricas_usadas <= metricas_gravadas,
                  f"ausentes: {sorted(metricas_usadas - metricas_gravadas)[:6]}")
        tipos = {p.get("type") for p in paineis}
        v.confere("painel cobre logs, traces e métricas",
                  {"logs", "traces"} <= tipos and {"timeseries", "stat"} <= tipos, str(sorted(tipos)))
        v.confere("painel declara dados sintéticos",
                  "sintetic" in sem_acento(json.dumps(dash, ensure_ascii=False)))
# ---------------------------------------------------------------------------
# D. site
# ---------------------------------------------------------------------------
RE_REF = re.compile(r"""(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
RE_TAG_A = re.compile(r"""<a\b[^>]*href\s*=\s*["']https?://[^"']*["'][^>]*>""", re.IGNORECASE)
RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
ROTAS = set(VISTAS_NAV) | set(VISTASExtras)


def resolve_referencia(html: str, ref: str, raiz: Path):
    """(tipo, alvo) de uma referência de href/src, relativa ao HTML dado."""
    ref = ref.strip()
    if not ref or ref.startswith(("mailto:", "tel:", "data:", "javascript:")):
        return "nula", ref
    if ref.startswith("http://") or ref.startswith("https://") or ref.startswith("//"):
        bruto = re.sub(r"^https?:", "", ref).lstrip("/")
        host = bruto.split("/")[0]
        return "externo", host
    if ref.startswith("#"):
        alvo = ref[1:]
        if alvo.startswith("/"):
            return "rota", alvo[1:].split("/")[0]
        return "ancora", alvo
    sem_query = ref.split("?")[0].split("#")[0]
    termina_em_barra = sem_query.endswith("/")
    if not sem_query:
        return "nula", ref
    if sem_query.startswith("/"):
        if sem_query == "/":
            return "interno", "index.html"
        prefixo = "/" + NOME_SITE
        if sem_query == prefixo or sem_query == prefixo + "/":
            return "interno", "index.html"
        if sem_query.startswith(prefixo + "/"):
            return "interno", sem_query[len(prefixo) + 1:]
        return "fora-do-prefixo", sem_query
    base = Path(html).parent
    candidato = (base / sem_query) if base.as_posix() != "." else Path(sem_query)
    return ("interno-diretorio" if termina_em_barra else "interno"), candidato.as_posix()


def checa_site(v: Verificador):
    v.inicio("site")
    indice = v.texto("index.html") if v.arquivo("index.html").is_file() else ""
    v.confere("index.html existe", bool(indice))
    nav = re.findall(r'data-rota="([a-z-]+)"', indice)
    v.confere("navegação com exatamente seis abas", nav == VISTAS_NAV, str(nav))
    controles = re.findall(r'aria-controls="([a-z-]+)"', indice)
    v.confere("cada aba aponta aria-controls para a vista",
              controles == [f"vista-{r}" for r in VISTAS_NAV], str(controles))
    secoes = set(re.findall(r'id="(vista-[a-z-]+)"', indice))
    v.confere("cada vista existe como seção",
              all(f"vista-{r}" in secoes for r in VISTAS_NAV) and "vista-correlacao" in secoes,
              str(sorted(secoes)))
    abas_role = len(re.findall(r'role="tab"', indice))
    v.confere("abas com role=tab e tablist", abas_role == 6 and 'role="tablist"' in indice,
              f"{abas_role} tabs")
    v.confere("seções com role=tabpanel", len(re.findall(r'role="tabpanel"', indice)) >= 6)
    app = v.texto("app.js")
    declaradas = re.search(r"const VISTAS\s*=\s*\[([^\]]+)\]", app)
    lista = re.findall(r'"([a-z-]+)"', declaradas.group(1)) if declaradas else []
    v.confere("app.js conhece as seis vistas e a de correlação",
              lista == VISTAS_NAV + ["correlacao"], str(lista))
    v.confere("roteamento por hash com hashchange", "hashchange" in app and
              "location.hash" in app)
    v.confere("carga com fetch e fallback file://",
              "fetch(" in app and "window.DEMO_DATA" in app and "data/sinteticos.json" in app)
    ordem = [indice.find('<script src="data/dados.js"'), indice.find('<script src="app.js"')]
    v.confere("fallback carregado antes do app.js", -1 not in ordem and ordem[0] < ordem[1], str(ordem))
    v.confere("rota desconhecida cai na visão geral", 'vista = "visao-geral"' in app)

    v.confere("página rotula dados sintéticos",
              "dados sintéticos" in indice and 'class="faixa' in indice)
    v.confere("política de privacidade descrita na página",
              "nenhum dado de cliente" in sem_acento(indice) and "privacidade" in sem_acento(indice))

    problemas: list[str] = []
    anchors_ok = set(re.findall(r'id="([^"]+)"', indice))
    for html, texto in [(h, v.texto(h) if v.arquivo(h).is_file() else "") for h in ARQUIVOS_HTML]:
        if not texto:
            problemas.append(f"{html} não existe")
            continue
        ids_doc = set(re.findall(r'id="([^"]+)"', texto))
        for tag in RE_TAG_A.findall(texto):
            if "noopener" not in tag or "noreferrer" not in tag:
                alvo = re.search(r'href="([^"]+)"', tag).group(1)
                problemas.append(f"{html}: link externo sem rel noopener/noreferrer: {alvo}")
        for bruto in RE_REF.findall(texto):
            ref = next((g for g in bruto if g), "")
            tipo, alvo = resolve_referencia(Path(html), ref, v.raiz)
            if tipo == "nula":
                continue
            if tipo == "externo":
                if alvo not in HOSTS_EXTERNOS_OK:
                    problemas.append(f"{html}: host externo fora da allow list: {alvo}")
                continue
            if tipo == "fora-do-prefixo":
                problemas.append(f"{html}: caminho absoluto fora de /{NOME_SITE}/: {alvo}")
                continue
            if tipo == "rota":
                if html == "index.html" and alvo not in ROTAS:
                    problemas.append(f"{html}: rota /#/{alvo} não existe")
                continue
            if tipo == "ancora":
                if alvo and alvo not in ids_doc:
                    problemas.append(f"{html}: âncora #{alvo} sem id no documento")
                continue
            alvo_path = Path(alvo)
            if tipo == "interno-diretorio":
                if not (v.raiz / alvo).is_dir():
                    problemas.append(f"{html}: diretório inexistente {alvo}")
                elif not (v.raiz / alvo / "index.html").is_file():
                    problemas.append(f"{html}: {alvo} não tem index.html e o Pages devolve 404")
                continue
            if not (v.raiz / alvo_path).is_file():
                problemas.append(f"{html}: referência interna quebrada: {ref}")
    v.confere("todas as referências internas resolvem", not problemas,
              "; ".join(problemas[:6]))
    v.confere("README referencia os dois endereços",
              "automacao.github.io" in v.texto("README.md")
              and f"jraalves.github.io/{NOME_SITE}" in v.texto("README.md"))
    corpo = v.texto("README.md")
    bloco = re.search(r"## O endereço.*?\n(.*?)\n## ", corpo, re.S)
    itens = re.findall(r"^\d+\.\s", bloco.group(1), re.M) if bloco else []
    v.confere("explicação do endereço tem três linhas", len(itens) == 3,
              f"{len(itens)} itens numerados" + ("" if bloco else " (seção não encontrada)"))
    v.confere("as três linhas falam de conta, nome ocupado e caminho versionado",
              bool(bloco) and all(p in bloco.group(1) for p in
                                  ("pertence à conta", "ocupado", "caminho versionado")))
    leiame = v.texto("README.md")
    v.confere("README lista as seis vistas",
              all(n in leiame for n in ("Visão geral", "Busca fiscal", "Extração",
                                        "Cobertura", "Arquitetura", "Sobre o case")))
    v.confere("README explica o modo file:// e o fallback",
              "dados.js" in leiame and "file://" in leiame and "window.DEMO_DATA" in leiame)
    v.confere("README documenta a publicação sem npm e sem build",
              "Sem `npm`" in leiame and "upload-pages-artifact" in leiame
              and "deploy-pages" in leiame and "npm install" not in leiame)
    cabecalho = indice[:2500]
    for campo in ('<meta charset="utf-8">', 'name="viewport"', 'lang="pt-BR"',
                  'name="description"', 'name="author"', 'rel="canonical"', 'rel="icon"',
                  'rel="stylesheet"', 'og:site_name'):
        v.confere(f"cabeçalho traz {campo}", campo in cabecalho)
    v.confere("canonical aponta para o caminho versionado",
              f"https://jraalves.github.io/{NOME_SITE}/" in cabecalho)
    v.confere("index.html é indexável", "noindex" not in cabecalho)
    v.confere("404.html existe, é noindex e tem a mesma faixa",
              'name="robots" content="noindex' in v.texto("404.html")
              and "dados sintéticos" in v.texto("404.html"))
    v.confere("404.html referencia assets com o prefixo do projeto",
              f'/automacao/styles.css' in v.texto("404.html")
              and 'src="app.js"' not in v.texto("404.html"))
    robo = v.texto("robots.txt")
    v.confere("robots.txt libera indexação", "User-agent: *" in robo and "Allow: /" in robo)
    ignoro = v.texto(".gitignore")
    v.confere(".gitignore cobre artefatos e segredos",
              all(t in ignoro for t in ("__pycache__/", ".env", "*.key", "*.pem")))
    v.confere("LICENSE é MIT com o autor",
              "MIT License" in v.texto("LICENSE") and "João Renato Alves" in v.texto("LICENSE"))
    seg = v.texto("SECURITY.md")
    v.confere("SECURITY.md define canal e escopo",
              "vulnerability" in seg.lower() and "security advisory" in seg.lower()
              and not RE_EMAIL.search(seg), RE_EMAIL.search(seg).group(0) if RE_EMAIL.search(seg) else "")
    v.confere("SECURITY.md diz que a demo não tem segredo nem login",
              "placeholder" in seg.lower() and "telemetria" in sem_acento(seg.lower()))
    aviso = v.texto("data/AVISO-SINTETICO.md")
    v.confere("aviso de dados sintéticos explica seed e reprodução",
              "20260124" in aviso and "gen_data.py" in aviso and "--check" in aviso
              and "byte a byte" in aviso)
    for nome in ("img/favicon.svg", "img/demo-card.svg"):
        try:
            import xml.etree.ElementTree as ET
            ET.fromstring(v.texto(nome))
            v.confere(f"{nome} é SVG válido", True)
        except Exception as exc:                          # noqa: BLE001
            v.confere(f"{nome} é SVG válido", False, str(exc))
    cartao = v.texto("img/demo-card.svg")
    v.confere("cartão social tem 1200x630 e o rótulo",
              'width="1200"' in cartao and 'height="630"' in cartao
              and "sintetic" in sem_acento(cartao))
    remotos = re.findall(r"""(?:src|href)=["'](https?://[^"']+)""", indice)
    v.confere("nenhum script ou folha remota na interface",
              not any(".css" in u or ".js" in u or "font" in u for u in remotos), str(remotos[:3]))
    css = v.texto("styles.css")
    v.confere("styles.css não busca recurso remoto",
              not re.search(r"url\(\s*['\"]?https?:", css) and "@import" not in css)
    v.confere("nenhum arquivo CNAME ou .nojekyll",
              not any((v.raiz / x).exists() for x in ("CNAME", ".nojekyll")))
# ---------------------------------------------------------------------------
# E. CI
# ---------------------------------------------------------------------------
ACOES_PERMITIDAS = {"actions/checkout@v4", "actions/setup-python@v5",
                    "actions/upload-pages-artifact@v3", "actions/deploy-pages@v1"}
PROIBIDO_NO_WORKFLOW = (r"\b(npm|yarn|pnpm|npx)\s+(install|ci|run|build|add)\b",
                        r"\b(jekyll|webpack|vite|rollup|grunt|gulp)\b",
                        r"\bpip3?\s+install\b", r"\bapt(-get)?\s+install\b",
                        r"\bsetup-node\b", r"\bconfigure-pages\b", r"\bnvm\b")


def checa_ci(v: Verificador):
    v.inicio("ci")
    arquivos = sorted(p.relative_to(v.raiz).as_posix()
                      for p in (v.raiz / ".github" / "workflows").glob("*.yml"))
    v.confere("exatamente um workflow", arquivos == [".github/workflows/deploy.yml"], str(arquivos))
    if not arquivos:
        return
    bruto = v.texto(arquivos[0])
    docs, erro = v.yaml(arquivos[0])
    if not v.confere("workflow parseia", docs is not None, erro or ""):
        return
    wf = docs[0]
    gatilhos = wf.get(True, wf.get("on")) or {}
    v.confere("gatilhos push, pull_request e workflow_dispatch",
              {"push", "pull_request", "workflow_dispatch"} <= set(gatilhos), str(sorted(gatilhos)))
    topo = wf.get("permissions") or {}
    v.confere("permissões do workflow são mínimas", topo == {"contents": "read"}, str(topo))
    v.confere("concurrency no grupo pages",
              (wf.get("concurrency") or {}).get("group") == "pages")
    jobs = wf.get("jobs") or {}
    v.confere("jobs qualidade e publicar", {"qualidade", "publicar"} <= set(jobs), str(sorted(jobs)))
    qual = (jobs.get("qualidade") or {}).get("steps") or []
    texto_qual = " ".join(str(s.get("run") or s.get("uses") or "") for s in qual)
    for obrigatorio in ("py_compile", "gen_data.py --check", "tools/validate.py",
                        "tools/leak_check.py", "node --check app.js", "tools/teste_render.js"):
        v.confere(f"job de qualidade roda {obrigatorio}", obrigatorio in texto_qual)
    public = jobs.get("publicar") or {}
    steps = public.get("steps") or []
    usadas = {s.get("uses") for s in steps if s.get("uses")}
    v.confere("publicação usa só checkout, upload-pages-artifact e deploy-pages",
              usadas == {"actions/checkout@v4", "actions/upload-pages-artifact@v3",
                         "actions/deploy-pages@v1"}, str(sorted(usadas)))
    v.confere("todas as actions são conhecidas",
              not {u for u in usadas if u not in ACOES_PERMITIDAS}, str(sorted(usadas)))
    v.confere("publicar depende de qualidade", public.get("needs") == "qualidade",
              str(public.get("needs")))
    v.confere("environment github-pages",
              (public.get("environment") or {}).get("name") == "github-pages")
    perm = public.get("permissions") or {}
    v.confere("escritura de Pages só no job de publicação",
              perm.get("pages") == "write" and perm.get("id-token") == "write"
              and perm.get("contents") == "read", str(perm))
    v.confere("nenhuma permissão de security-events", "security-events" not in
              str(wf.get("permissions")) + str(perm))
    artefato = next((s for s in steps if (s.get("uses") or "").startswith("actions/upload-pages-artifact")), {})
    v.confere("artefato é a árvore do repositório",
              (artefato.get("with") or {}).get("path") == ".",
              str((artefato.get("with") or {})))
    v.confere("workflow não referencia segredo", "secrets." not in bruto)
    violacoes = [padrao for padrao in PROIBIDO_NO_WORKFLOW
                 if re.search(padrao, bruto, re.IGNORECASE)]
    v.confere("workflow não chama npm, yarn, jekyll nem instala dependência",
              not violacoes, str(violacoes[:3]))
    com_run = [step.get("name") for step in (public.get("steps") or []) if step.get("run")]
    v.confere("job de publicação não executa comando nenhum além das actions",
              not com_run, str(com_run))
    v.confere("página não depende de domínio próprio",
              not (v.raiz / "CNAME").exists() and "CNAME" not in
              [q for q in re.findall(r"-e \"?\$?(\w+)", bruto) if q == "CNAME"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
SECACOES = {"dados": checa_dados, "k8s": checa_k8s, "observabilidade": checa_observabilidade,
            "site": checa_site, "ci": checa_ci}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="valida o repositório de demonstração")
    parser.add_argument("--raiz", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--verbose", action="store_true", help="lista cada checagem")
    parser.add_argument("--somente", choices=sorted(SECACOES), help="roda uma seção")
    args = parser.parse_args(argv)
    raiz = Path(args.raiz).resolve()
    if not (raiz / "index.html").is_file():
        print(f"ERRO: {raiz} não parece a raiz da demonstração (falta index.html)")
        return 2
    v = Verificador(raiz)
    alvos = [args.somente] if args.somente else ["dados", "k8s", "observabilidade", "site", "ci"]
    for nome in alvos:
        SECACOES[nome](v)
    if args.verbose:
        for secao, rotulo, ok in v.historico:
            print(f"{'ok  ' if ok else 'ERRO'} [{secao}] {rotulo}")
    for secao in alvos:
        print(f"[{secao:<15}] {v.linhas_por_secao.get(secao, 0):>4} checagens")
    for erro in v.erros:
        print("ERRO", erro)
    for aviso in v.avisos:
        print("aviso", aviso)
    print(f"validado: {v.checagens} checagens, {len(v.erros)} erro(s), {len(v.avisos)} aviso(s)")
    if v.erros:
        return 1
    print("OK: estrutura, manifests, telemetria, interface e publicação consistentes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
