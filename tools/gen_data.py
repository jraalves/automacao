#!/usr/bin/env python3
"""Gerador determinístico dos dados sintéticos da demonstração.

Produz, a partir de uma seed fixa (padrão 20260124) e só com a biblioteca padrão:

    data/sinteticos.json  -> consumido pelo site via fetch()
    data/dados.js         -> mesmo conteúdo em window.DEMO_DATA (fallback file://)

Nenhum dado aqui representa cliente, empresa real ou documento real. Todos os
identificadores usam prefixos DEMO / SINT / FICTICIA e nenhum número passa no
dígito verificador de CNPJ ou de CPF. Antes de gravar, o gerador passa o texto
resultante pela varredura de tools/leak_check.py e se recusa a escrever saída
que case com as regras anti-vazamento (veja `--list-blocked` naquele script).

Uso:
    python3 tools/gen_data.py                  # gera em data/
    python3 tools/gen_data.py --seed 20260124 --out data
    python3 tools/gen_data.py --check          # regenera e compara byte a byte
    python3 tools/gen_data.py --stdout         # imprime o JSON, não escreve

Determinismo: a saída é byte idêntica em qualquer máquina e em qualquer data,
porque o único "agora" usado é a constante BASE abaixo. O CI roda --check.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import leak_check  # noqa: E402  (reaproveita as regras anti-vazamento)

SEED_PADRAO = 20260124
FUSO = timezone(timedelta(hours=-3))
BASE = datetime(2026, 1, 24, 12, 0, 0, tzinfo=FUSO)   # "agora" da demonstração
VERSAO_DADOS = "1.0.0"
QTD_DOCUMENTOS = 40
QTD_PONTOS_SERIE = 26

ARQ_JSON = "sinteticos.json"
ARQ_JS = "dados.js"

# ---------------------------------------------------------------------------
# Tabelas base (todas fictícias)
# ---------------------------------------------------------------------------
EMPRESAS = [
    ("EMP-DEMO-0001", "Aurora Exemplo Ltda (fictícia)", "SP", "Lucro Presumido"),
    ("EMP-DEMO-0002", "Boreal Demonstrações SA (fictícia)", "SP", "Lucro Real"),
    ("EMP-DEMO-0003", "Cisne Serviços Sintéticos Ltda", "MG", "Simples Nacional"),
    ("EMP-DEMO-0004", "Diamante Logística Fictícia SA", "RJ", "Lucro Presumido"),
    ("EMP-DEMO-0005", "Estuário Comércio Ilustrativo Ltda", "RS", "Lucro Real"),
    ("EMP-DEMO-0006", "Farol Demonstrações Sintéticas ME", "PR", "Simples Nacional"),
    ("EMP-DEMO-0007", "Gaivota Participações Fictícias SA", "SC", "Lucro Presumido"),
    ("EMP-DEMO-0008", "Horizonte Exemplos Tributários Ltda", "BA", "Lucro Presumido"),
]

TIPOS = [
    ("NFe", "NF-e", "Nota fiscal eletrônica de mercadoria (modelo 55)"),
    ("NFCe", "NFC-e", "Nota fiscal de consumo eletrônica (modelo 65)"),
    ("CTe", "CT-e", "Conhecimento de transporte eletrônico (modelo 57)"),
    ("MDFe", "MDF-e", "Manifesto eletrônico de documentos fiscais (modelo 58)"),
    ("NFSe", "NFS-e", "Nota fiscal de serviço eletrônica (exemplo genérico)"),
    ("CCe", "CC-e", "Carta de correção eletrônica (documento de ajuste)"),
]

STATUS = [
    ("publicado", "Publicado no repositório de destino", "ok"),
    ("conferencia", "Aguardando conferência humana", "alerta"),
    ("processando", "Em extração", "info"),
    ("fila", "Na fila de execução", "neutro"),
    ("falha", "Falha de extração ou validação", "erro"),
]

MOTIVOS_FALHA = {
    "schema": "XML fora do padrão: namespace do layout 4.00 ausente no envelope sintético",
    "ocr": "PDF sem camada de texto: chave de acesso ilegível depois do OCR de demonstração",
    "timeout": "Tempo limite de 30 s ao ler o arquivo no repositório de origem (exemplo)",
    "regra": "Regra fiscal rejeitou o documento: data de emissão posterior à entrada no repositório",
    "duplicidade": "Chave de acesso duplicada entre EMP-DEMO-0002 e EMP-DEMO-0005 (exemplo)",
    "conflito_etag": "Repositório de destino recusou a gravação: ETag divergente no exemplo",
}

# classe de erro -> etapa do pipeline onde a falha acontece (índice do span)
FALHA_PARA_ETAPA = {
    "timeout": "ingestao",
    "ocr": "extracao",
    "schema": "validacao_schema",
    "regra": "validacao_schema",
    "duplicidade": "publicacao",
    "conflito_etag": "publicacao",
}

# etapa nomeada do documento -> índice do span correspondente no trace sintético
ETAPA_PARA_SPAN = {
    "descoberta": 2,
    "ingestao": 2,
    "extracao": 3,
    "validacao_schema": 4,
    "publicacao": 5,
    "conferencia_humana": 5,
}

EXTENSOES = [("xml", "application/xml"), ("pdf", "application/pdf"), ("json", "application/json")]

ETAPAS_DOCUMENTO = [
    ("descoberta", "varredura agendada do repositório de arquivos corporativo (M365 Drive-like, genérico)", "agendador-varredura"),
    ("ingestao", "arquivo baixado para staging e checksum calculado", "api-ingestao"),
    ("extracao", "parser do layout fiscal extraiu os campos de demonstração", "worker-extracao"),
    ("validacao_schema", "documento validado contra o esquema e as regras fiscais", "validador-schema"),
    ("publicacao", "gravado no repositório de destino com nome padronizado", "worker-publicacao"),
    ("conferencia", "revisão humana registrada pelo perfil de conferência da demo", "conferente-demo"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def iso(instante: datetime) -> str:
    return instante.isoformat(timespec="seconds")


def moeda(valor: float) -> str:
    """Formata em padrão brasileiro sem depender de locale externo."""
    total_centavos = round(abs(valor) * 100)
    inteiro, fracao = divmod(total_centavos, 100)
    texto = f"{inteiro:,}".replace(",", ".")
    sinal = "-" if valor < 0 else ""
    return f"{sinal}R$ {texto},{fracao:02d}"


def sem_ambiguidade(rng: random.Random, alfabeto: str, comprimento: int,
                    max_digitos_seguidos: int = 7) -> str:
    """String aleatória sem sequências longas de dígitos (não parece documento real)."""
    while True:
        saida = "".join(rng.choice(alfabeto) for _ in range(comprimento))
        if not re.search(rf"\d{{{max_digitos_seguidos + 1},}}", saida):
            return saida


def token_letras(rng: random.Random, comprimento: int = 8) -> str:
    alfabeto = "bcdfghjkmnprstvwz0123456789"
    while True:
        saida = "".join(rng.choice(alfabeto) for _ in range(comprimento))
        if not re.search(r"\d{4,}", saida):
            return saida


def flutuante(rng: random.Random, minimo: float, maximo: float, casas: int = 2) -> float:
    return round(rng.uniform(minimo, maximo), casas)


# ---------------------------------------------------------------------------
# Blocos
# ---------------------------------------------------------------------------
def gerar_aviso() -> dict:
    return {
        "rotulo": "Demonstração — dados sintéticos, nenhum dado de cliente",
        "titulo": "Demonstração com dados sintéticos",
        "texto": (
            "Esta página mostra a interface, o fluxo, a observabilidade e a arquitetura de uma "
            "aplicação de automação de gestão fiscal. Nada aqui vem de um cliente, de um "
            "empregador ou de um ambiente real: empresas, documentos, números, traces, métricas "
            "e incidentes são produzidos por script a partir de uma seed fixa."
        ),
        "o_que_e_valido": [
            "o desenho da solução: camadas, contratos de API, etapas do pipeline, SLIs e alertas",
            "os manifests Kubernetes e as regras de Prometheus que documentam esse desenho",
            "a interação da interface: filtros, painel de detalhe, correlação, reprocessamento",
        ],
        "o_que_e_ficticio": [
            "todos os números, datas, identificadores, nomes de empresa e caminhos de arquivo",
            "a impressão de que houve processamento real por trás desta página",
            "qualquer telemetria: não há coleta de dados nesta demonstração",
        ],
        "seed": SEED_PADRAO,
        "gerador": "tools/gen_data.py",
        "arquivo_aviso": "data/AVISO-SINTETICO.md",
    }


def gerar_identidade() -> dict:
    return {
        "titulo": "Automação de gestão fiscal — demonstração",
        "subtitulo": "Busca, recuperação e gestão de documentos fiscais em repositório de arquivos corporativo",
        "versao_dados": VERSAO_DADOS,
        "idioma": "pt-BR",
        "instante_base": iso(BASE),
        "fuso": "UTC-3 (hora fixa usada pelo gerador)",
        "seed": SEED_PADRAO,
        "gerado_por": "tools/gen_data.py",
        "como_reproduzir": "python3 tools/gen_data.py --seed 20260124 --out data",
        "rotulo_fixo": "DEMONSTRAÇÃO",
        "autor": {
            "nome": "João Renato Alves",
            "papel": "SRE / Observabilidade",
            "github": "https://github.com/jraalves",
            "linkedin": "https://www.linkedin.com/in/jraalves",
            "portfolio": "https://jraalves.github.io/portfolio",
        },
        "licenca": "MIT",
    }


def gerar_autenticacao() -> dict:
    return {
        "demo_sem_autenticacao": True,
        "aviso_demo": (
            "A demonstração roda sem autenticação: não há tela de login, não há sessão e não há "
            "chamada de rede além do carregamento dos próprios arquivos do site."
        ),
        "producao_documentada": (
            "No desenho de produção o acesso passa por oauth2-proxy à frente do Service, com o "
            "Ingress expondo apenas o proxy; o backend só é alcançado depois do cookie de sessão "
            "emitido pelo proxy. Isso está documentado em k8s/ e em docs/decisoes/, mas não é "
            "executado aqui."
        ),
        "referencias": [
            "k8s/ingress.yaml",
            "k8s/oauth2-proxy.yaml",
            "docs/decisoes/0001-borda-com-ingress-e-oauth2-proxy.md",
        ],
    }


def gerar_empresas() -> list[dict]:
    rng = random.Random(SEED_PADRAO + 1)
    saida = []
    for indice, (codigo, nome, uf, regime) in enumerate(EMPRESAS):
        saida.append({
            "id": codigo,
            "nome": nome,
            "uf": uf,
            "regime": regime,
            "cnpj": "não usado na demo — nenhuma sequência com formato de CNPJ",
            "volume_mensal_ilustrativo": 600 + indice * 240 + rng.randint(0, 300),
            "responsavel_demo": f"perfil-conferencia-{indice + 1:02d}",
            "pasta_raiz": f"/Demonstracao-Fiscal/{codigo}",
            "ativo": indice != 5,
        })
    return saida


def gerar_tipos() -> list[dict]:
    return [{"codigo": c, "rotulo": r, "descricao": d} for c, r, d in TIPOS]


def gerar_status() -> list[dict]:
    return [{"codigo": c, "rotulo": r, "tom": t} for c, r, t in STATUS]


def gerar_repositorio() -> dict:
    return {
        "nome": "repositório de arquivos corporativo (M365 Drive-like, genérico)",
        "descricao": (
            "O caso original lia e escrevia em um repositório de arquivos corporativo. Nesta "
            "demonstração o conector é descrito como uma API genérica de arquivos (listar, "
            "baixar, subir, consultar ETag). Nenhum produto, domínio ou tenant é citado."
        ),
        "caminho_base": "/Demonstracao-Fiscal",
        "operacoes": ["listar", "baixar", "subir", "mover", "consultar ETag"],
        "autenticacao_producao": "OAuth2 com cliente próprio da plataforma (não executado aqui)",
        "autenticacao_demo": "nenhuma: a demonstração roda sem login e sem rede",
        "limite_lote_ilustrativo": "1.000 arquivos por varredura agendada",
    }


def gerar_schema_extracao() -> list[dict]:
    return [
        {"campo": "chave_de_acesso", "origem": "XML / leiaute", "obrigatorio": True, "validacao": "44 posições (mascaradas na demo)"},
        {"campo": "numero_documento", "origem": "XML / leiaute", "obrigatorio": True, "validacao": " entero > 0"},
        {"campo": "data_emissao", "origem": "XML / leiaute", "obrigatorio": True, "validacao": "ISO-8601, <= data de entrada"},
        {"campo": "empresa_emitente", "origem": "XML / leiaute", "obrigatorio": True, "validacao": "existe no cadastro demo"},
        {"campo": "valor_total", "origem": "XML / OCR", "obrigatorio": True, "validacao": "> 0, duas casas decimais"},
        {"campo": "base_tributavel", "origem": "XML / OCR", "obrigatorio": False, "validacao": "<= valor_total"},
        {"campo": "uf_destino", "origem": "XML / OCR", "obrigatorio": True, "validacao": "duas letras, lista de UFs"},
        {"campo": "modelo_documento", "origem": "XML / leiaute", "obrigatorio": True, "validacao": "55, 65, 57, 58 ou padrão de serviço"},
        {"campo": "status_referenciado", "origem": "regra de negócio", "obrigatorio": False, "validacao": "documento relacionado existente"},
        {"campo": "caminho_publicado", "origem": "pipeline", "obrigatorio": True, "validacao": "convenção /ano/mes/empresa/tipo-numero"},
    ]


def gerar_documentos() -> tuple[list[dict], list[dict]]:
    rng = random.Random(SEED_PADRAO + 2)
    distribuicao = (["publicado"] * 20 + ["conferencia"] * 8 + ["processando"] * 3
                    + ["fila"] * 3 + ["falha"] * 6)
    rng.shuffle(distribuicao)
    rotulos_por_status = {c: r for c, r, _ in STATUS}

    documentos: list[dict] = []
    for i in range(QTD_DOCUMENTOS):
        emp = EMPRESAS[rng.randrange(len(EMPRESAS))]
        tipo = TIPOS[rng.randrange(len(TIPOS))]
        status = distribuicao[i]
        emissao = BASE - timedelta(days=rng.randint(4, 160), hours=rng.randint(0, 20),
                                   minutes=rng.randint(0, 59))
        entrada = emissao + timedelta(days=rng.randint(0, 3), hours=rng.randint(1, 30))
        numero_demo = f"NF-DEMO-{rng.choice(['97', '98', '99'])}-{rng.randint(1, 999999):06d}"
        extensao, mime = EXTENSOES[0] if tipo[0] in ("NFe", "CTe", "MDFe", "NFSe") else \
            (EXTENSOES[1] if rng.random() < 0.7 else EXTENSOES[2])
        valor = flutuante(rng, 180.0, 48000.0)
        latencia = (flutuante(rng, 1.1, 6.4) if status in ("publicado", "conferencia")
                    else flutuante(rng, 6.5, 22.0) if status == "processando"
                    else flutuante(rng, 8.0, 45.0) if status == "falha" else 0.0)
        motivo = None
        if status == "falha":
            classe = list(MOTIVOS_FALHA)[i % len(MOTIVOS_FALHA)]
            motivo = {"classe": classe, "descricao": MOTIVOS_FALHA[classe],
                      "codigo_erro": f"ERR-SINT-{rng.randint(100, 599)}",
                      "etapa": FALHA_PARA_ETAPA[classe]}

        pasta = f"{emissao.strftime('%Y')}/{emissao.strftime('%m')}/{emp[0]}"
        caminho_origem = f"/Demonstracao-Fiscal/{pasta}/original-{numero_demo}.{extensao}"
        caminho_destino = f"/Demonstracao-Fiscal/{pasta}/publicado-{numero_demo}.{extensao}"

        trilha = montar_trilha(status, motivo, entrada, rng)
        trace_id = sem_ambiguidade(rng, "0123456789abcdef", 32)
        span_raiz = sem_ambiguidade(rng, "0123456789abcdef", 16)
        request_id = f"REQ-DEMO-{token_letras(rng)}-{i + 1:03d}"

        documentos.append({
            "id": f"DOC-FISCAL-SINT-{i + 1:06d}",
            "empresa_id": emp[0],
            "empresa_nome": emp[1],
            "uf": emp[2],
            "tipo": tipo[0],
            "tipo_rotulo": tipo[1],
            "numero_documento_demo": numero_demo,
            "serie": rng.randint(1, 3),
            "subserie": rng.randint(0, 9),
            "chave_acesso": "ocultada nesta demonstração",
            "extensao": extensao,
            "mime": mime,
            "tamanho_bytes": rng.randint(6, 900) * 512,
            "checksum_sha256": sem_ambiguidade(rng, "0123456789abcdef", 16),
            "data_emissao": iso(emissao),
            "data_referencia": emissao.strftime("%Y-%m"),
            "data_entrada_repositorio": iso(entrada),
            "status": status,
            "status_rotulo": rotulos_por_status[status],
            "tentativas": 1 if status in ("publicado", "conferencia", "fila") else rng.randint(2, 4),
            "latencia_extracao_seg": latencia,
            "motivo_falha": motivo,
            "caminho_origem": caminho_origem,
            "caminho_destino": caminho_destino if status == "publicado" else None,
            "valor_documento": moeda(valor),
            "base_tributavel": moeda(valor * 0.86),
            "icms": moeda(valor * 0.12),
            "rotulos": ["sintetico", tipo[0].lower(), "demo"],
            "trace_id": trace_id,
            "span_id": span_raiz,
            "request_id": request_id,
            "trilha_auditoria": trilha,
        })

    rng2 = random.Random(SEED_PADRAO + 7)
    tracos = [gerar_traco(doc, rng2) for doc in documentos]
    return documentos, tracos


def montar_trilha(status: str, motivo: dict | None, entrada: datetime,
                  rng: random.Random) -> list[dict]:
    """Trilha de auditoria coerente com o status: cada status para em uma etapa."""
    corte = {"fila": 1, "processando": 3, "conferencia": 5, "publicado": 6}.get(status, 6)
    if status == "falha" and motivo:
        indice_etapa = next((idx for idx, (nome, _, _) in enumerate(ETAPAS_DOCUMENTO)
                             if nome == motivo["etapa"]), 2)
        corte = indice_etapa + 1
    trilha = []
    for posicao in range(corte):
        evento, detalhe, ator = ETAPAS_DOCUMENTO[posicao]
        instante = entrada + timedelta(minutes=7 * posicao + rng.randint(0, 11))
        eh_falha = status == "falha" and posicao == corte - 1
        trilha.append({
            "sequencia": posicao + 1,
            "instante": iso(instante),
            "evento": evento,
            "ator": ator,
            "resultado": "falha" if eh_falha else "ok",
            "detalhe": motivo["descricao"] if eh_falha and motivo else detalhe,
        })
    return trilha


def gerar_traco(doc: dict, rng: random.Random) -> dict:
    """Trace sintético (OTel) com spans e logs correlatos ao X-Request-Id da UI."""
    etapas = [
        ("http GET /api/demo/documentos/{id}", "api-busca", "entrada", 0.04),
        ("find documentos (coleção demo)", "mongodb", "banco", 0.02),
        ("drive.download (repositório de origem)", "conector-arquivos", "saída", 0.12),
        ("extrator.parse xml|ocr", "worker-extracao", "processamento", 0.44),
        ("validador.schema leiaute 4.00", "validador-schema", "processamento", 0.17),
        ("drive.upload (repositório de destino)", "worker-publicacao", "saída", 0.21),
    ]
    total_ms = max(120.0, doc["latencia_extracao_seg"] * 1000.0)
    falha_em = None
    if doc["motivo_falha"]:
        falha_em = ETAPA_PARA_SPAN.get(doc["motivo_falha"]["etapa"], 3)

    spans, deslocamento = [], 0
    for posicao, (nome, servico, tipo, fracao) in enumerate(etapas):
        if falha_em is not None and posicao > falha_em:
            continue
        duracao = int(total_ms * fracao)
        status_span = "erro" if falha_em is not None and posicao == falha_em else "ok"
        spans.append({
            "nome": nome,
            "servico": servico,
            "tipo": tipo,
            "span_id": sem_ambiguidade(rng, "0123456789abcdef", 16),
            "inicio_ms": deslocamento,
            "duracao_ms": duracao,
            "profundidade": 0 if posicao == 0 else (1 if posicao < 3 else 2),
            "status": status_span,
        })
        deslocamento += max(1, duracao // 2)

    base_ts = datetime.fromisoformat(doc["data_entrada_repositorio"])
    frases = [
        ("INFO", "api-busca", f"busca atendida: 1 documento de demonstração retornado ({doc['request_id']})"),
        ("DEBUG", "conector-arquivos", f"listagem paginada: {rng.randint(1, 42)} itens na pasta {doc['empresa_id']}"),
        ("INFO", "worker-extracao", f"extração executada em {doc['latencia_extracao_seg']:.2f} s "
                                    f"({doc['tipo_rotulo']} {doc['numero_documento_demo']})"),
    ]
    if doc["motivo_falha"]:
        frases.append(("ERROR", "worker-extracao",
                       f"{doc['motivo_falha']['codigo_erro']}: {doc['motivo_falha']['descricao']}"))
    elif doc["status"] == "publicado":
        frases.append(("INFO", "worker-publicacao",
                       f"publicado em {doc['caminho_destino']} com ETag de exemplo"))
    elif doc["status"] == "conferencia":
        frases.append(("INFO", "validador-schema", "aguardando conferência humana na fila da demo"))
    else:
        frases.append(("WARN", "validador-schema", "documento na fila: próxima etapa ainda não executada"))

    logs = []
    for posicao, (nivel, servico, mensagem) in enumerate(frases):
        logs.append({
            "instante": iso(base_ts + timedelta(seconds=3 * posicao + rng.randint(0, 7))),
            "nivel": nivel,
            "servico": servico,
            "mensagem": mensagem,
            "trace_id": doc["trace_id"],
            "span_id": spans[min(posicao, len(spans) - 1)]["span_id"],
            "request_id": doc["request_id"],
        })

    return {
        "request_id": doc["request_id"],
        "trace_id": doc["trace_id"],
        "span_raiz": doc["span_id"],
        "documento_id": doc["id"],
        "empresa_id": doc["empresa_id"],
        "servico_origem": "api-busca",
        "amostragem": "traço retido pela política de amostragem da demonstração (100%)",
        "duracao_total_ms": sum(s["duracao_ms"] for s in spans),
        "spans": spans,
        "logs": logs,
    }


def gerar_pipeline(documentos: list[dict]) -> dict:
    rng = random.Random(SEED_PADRAO + 3)
    definicoes = [
        ("descoberta", "Descoberta", "Varredura agendada do repositório de arquivos corporativo", "agendador-varredura", 1284, 0.82, 2.4),
        ("ingestao", "Ingestão", "Baixa do arquivo, checksum e cópia para staging", "api-ingestao", 1271, 1.34, 4.1),
        ("extracao", "Extração", "Parser do layout fiscal e OCR quando o arquivo é PDF", "worker-extracao", 1246, 3.91, 12.6),
        ("validacao_schema", "Validação de schema", "Conformidade ao leiaute + regras fiscais", "validador-schema", 1229, 0.47, 1.8),
        ("publicacao", "Publicação no repositório de destino", "Gravação com nome padronizado e ETag", "worker-publicacao", 1188, 1.62, 5.3),
        ("conferencia_humana", "Conferência humana", "Fila de revisão por perfil de conferência", "interface-web", 412, 96.0, 1740.0),
    ]
    etapas = []
    for codigo, nome, descricao, servico, entradas, mediana, p95 in definicoes:
        falhas = rng.randint(3, 17) if codigo != "conferencia_humana" else rng.randint(0, 3)
        em_andamento = rng.randint(8, 63)
        etapas.append({
            "codigo": codigo,
            "nome": nome,
            "descricao": descricao,
            "servico": servico,
            "entradas_24h": entradas,
            "concluidas_24h": entradas - falhas - em_andamento,
            "em_andamento": em_andamento,
            "falhas_24h": falhas,
            "mediana_seg": mediana,
            "p95_seg": p95,
            "fila_espera": rng.randint(0, 240),
            "reprocessavel": codigo in ("extracao", "validacao_schema", "publicacao"),
        })

    falhas_doc = [d for d in documentos if d["status"] == "falha"]
    fila = []
    for doc in falhas_doc:
        classe = doc["motivo_falha"]["classe"]
        fila.append({
            "documento_id": doc["id"],
            "empresa_id": doc["empresa_id"],
            "tipo_rotulo": doc["tipo_rotulo"],
            "numero_documento_demo": doc["numero_documento_demo"],
            "motivo_classe": classe,
            "descricao": doc["motivo_falha"]["descricao"],
            "etapa": doc["motivo_falha"]["etapa"],
            "tentativas": doc["tentativas"],
            "ultima_tentativa": iso(BASE - timedelta(minutes=rng.randint(4, 320))),
            "proxima_acao": rng.choice(["reprocessar", "reprocessar com OCR",
                                        "reprocessar após correção de leiaute",
                                        "enviar para análise humana"]),
            "reprocessavel": classe != "duplicidade",
        })
    return {
        "janela": "últimas 24 horas da janela de demonstração",
        "etapas": etapas,
        "fila_falhas": fila,
        "politica_retry": "3 tentativas com backoff exponencial (1 s, 4 s, 16 s) e beco sem saída de exemplo",
        "metrica_fila": "pipeline_etapa_documentos{etapa=...,resultado=...}",
    }


def gerar_workflows() -> list[dict]:
    rng = random.Random(SEED_PADRAO + 4)
    modelos = [
        ("Coleta mensal NF-e", "0 3 5 * *", "concluido"),
        ("Reprocessamento de PDFs sem texto", "15 2 * * 1-5", "falha_parcial"),
        ("Conferência de CT-e de transporte", "*/30 * * * *", "executando"),
        ("Carga retroativa do exercício anterior", "0 1 1 2 *", "na_fila"),
        ("Varredura da pasta de demonstração", "*/5 * * * *", "executando"),
        ("Auditoria de duplicidade de chaves", "0 4 * * 0", "bloqueado"),
        ("Validação de leiaute 4.00", "30 3 * * *", "executando"),
        ("Relatório de cobertura por empresa demo", "0 6 * * 1", "na_fila"),
    ]
    saida = []
    for i, (nome, cron, estado) in enumerate(modelos):
        emp = EMPRESAS[i % len(EMPRESAS)]
        previstos = rng.randint(120, 1800)
        progresso = {"concluido": 100.0, "bloqueado": round(rng.uniform(4.0, 22.0), 1),
                     "na_fila": 0.0}.get(estado)
        if progresso is None:
            progresso = round(rng.uniform(31.0, 92.0), 1)
        concluidos = int(previstos * progresso / 100)
        inicio = BASE - timedelta(hours=rng.randint(1, 30), minutes=rng.randint(0, 59))
        saida.append({
            "id": f"WF-DEMO-{2026}-{i + 1:02d}",
            "nome": nome,
            "empresa_id": emp[0],
            "empresa_nome": emp[1],
            "cron": cron,
            "estado": estado,
            "progresso_pct": progresso,
            "documentos_previstos": previstos,
            "documentos_concluidos": concluidos,
            "inicio": iso(inicio),
            "proxima_execucao": iso(BASE + timedelta(hours=rng.randint(1, 48))),
            "fila_de_mensagens": rng.randint(0, 320),
            "responsavel": f"perfil-conferencia-{(i % 8) + 1:02d}",
            "gatilho": "agendamento" if "*/5" not in cron else "varredura contínua",
        })
    return saida


def gerar_serie() -> tuple[list[dict], list[dict], dict, list[dict]]:
    """Série horária (26 pontos), burn rate, orçamento de erro e cobertura por empresa.

    A janela inclui um pico deliberado (3 horas) que corresponde ao incidente
    INC-DEMO-02, para que o gráfico de burn rate tenha história: uma janela de
    queima acelerada, um alerta de página e a volta ao ritmo normal.
    """
    rng = random.Random(SEED_PADRAO + 5)
    pico = 12                      # índice do ponto mais crítico da série
    janela_pico = (pico, pico + 1, pico + 2)
    limite_erro = 0.5              # SLO de taxa de erro do worker: <= 0,5%
    limite_acima_p95 = 5.0         # fração aceita acima de 12 s (consistente com p95)
    limite_disponibilidade = 0.5   # fração aceita de respostas 5xx
    serie = []
    for hora in range(QTD_PONTOS_SERIE):
        instante = BASE - timedelta(hours=(QTD_PONTOS_SERIE - 1) - hora)
        esperados = rng.randint(300, 520)
        faltantes = rng.choices([0, 1, 2, 3, 5, 8], weights=[45, 20, 13, 10, 7, 5])[0]
        falhas = rng.choices([0, 1, 2, 3, 5], weights=[40, 30, 15, 10, 5])[0]
        concluidas = esperados - faltantes + rng.randint(0, 24)
        p50 = flutuante(rng, 2.4, 4.2)
        p95 = flutuante(rng, 5.8, 11.2)
        acima_p95 = rng.choices([2, 4, 7, 12], weights=[45, 25, 20, 10])[0]
        buscas = rng.randint(420, 1900)
        respostas_5xx = rng.choices([0, 1, 2, 4], weights=[55, 20, 15, 10])[0]
        if hora == janela_pico[0]:
            faltantes += 26
            falhas += 44
            concluidas = max(1, concluidas - 62)
            p50 += 3.4
            p95 = 21.8
            acima_p95 = 196
            respostas_5xx = 27
        elif hora == janela_pico[1]:
            faltantes += 9
            falhas += 15
            concluidas = max(1, concluidas - 21)
            p50 += 1.6
            p95 = 14.6
            acima_p95 = 74
            respostas_5xx = 11
        elif hora == janela_pico[2]:
            faltantes += 3
            falhas += 6
            acima_p95 = 26
            p95 += 1.9
        validados = esperados - faltantes
        serie.append({
            "instante": iso(instante),
            "hora_rotulo": instante.strftime("%d/%m %Hh"),
            "documentos_esperados": esperados,
            "documentos_validados": validados,
            "cobertura_pct": round(validados / esperados * 100, 2),
            "extracoes_concluidas": max(1, concluidas),
            "extracoes_falhas": falhas,
            "taxa_erro_pct": round(falhas / max(1, falhas + concluidas) * 100, 3),
            "extracoes_acima_de_12s": acima_p95,
            "pct_acima_de_12s": round(acima_p95 / max(1, concluidas) * 100, 3),
            "latencia_p50_seg": p50,
            "latencia_p95_seg": round(p95, 2),
            "buscas": buscas,
            "requisicoes_5xx": respostas_5xx,
            "disponibilidade_pct": round((buscas - respostas_5xx) / buscas * 100, 3),
            "buscas_sem_resultado_pct": flutuante(rng, 1.2, 9.8, 1),
        })

    def media_janela(indice: int, largura: int) -> float:
        inicio = max(0, indice - largura + 1)
        janela = serie[inicio:indice + 1]
        return sum(p["taxa_erro_pct"] for p in janela) / len(janela)

    burn = []
    for indice, ponto in enumerate(serie):
        queima_1h = round(ponto["taxa_erro_pct"] / limite_erro, 3)
        burn.append({
            "instante": ponto["instante"],
            "hora_rotulo": ponto["hora_rotulo"],
            "queima_1h": queima_1h,
            "queima_6h": round(media_janela(indice, 6) / limite_erro, 3),
            "queima_24h": round(media_janela(indice, 24) / limite_erro, 3),
            "alerta_de_pagina": queima_1h >= 14.4,
            "incidente_id": "INC-DEMO-02" if indice == pico else None,
        })

    total_concluidas = sum(p["extracoes_concluidas"] for p in serie)
    total_falhas = sum(p["extracoes_falhas"] for p in serie)
    total_esperados = sum(p["documentos_esperados"] for p in serie)
    total_validados = sum(p["documentos_validados"] for p in serie)
    total_buscas = sum(p["buscas"] for p in serie)
    total_5xx = sum(p["requisicoes_5xx"] for p in serie)
    total_acima_p95 = sum(p["extracoes_acima_de_12s"] for p in serie)
    taxa_erro_26h = round(total_falhas / max(1, total_falhas + total_concluidas) * 100, 3)
    cobertura_26h = round(total_validados / max(1, total_esperados) * 100, 2)
    erro_disp_26h = round(total_5xx / max(1, total_buscas) * 100, 3)
    acima_p95_26h = round(total_acima_p95 / max(1, total_concluidas) * 100, 3)

    dias_decorridos, dias_janela = 24, 30
    fracao_mes = dias_decorridos / dias_janela
    limiar_saudavel = round(fracao_mes * 100 * 0.85, 1)

    # Valores mensais (mês de referência) são agregados independentes da série de
    # 26 horas: a série é uma janela curta e inclui o incidente INC-DEMO-02.
    rng_mes = random.Random(SEED_PADRAO + 9)
    mensal = {
        "taxa_erro_worker": {"objetivo_pct": limite_erro,
                             "observado_pct": round(rng_mes.uniform(0.34, 0.46), 3)},
        "cobertura_fiscal": {"objetivo_pct": 99.0,
                             "observado_pct": round(rng_mes.uniform(99.25, 99.45), 2)},
        "disponibilidade_busca": {"objetivo_pct": 99.5,
                                  "observado_pct": round(rng_mes.uniform(99.55, 99.72), 3)},
        "latencia_extracao": {"objetivo_pct": limite_acima_p95,
                              "acima_do_objetivo_pct": round(rng_mes.uniform(3.4, 4.4), 2),
                              "p95_mes_seg": round(rng_mes.uniform(10.2, 11.6), 2)},
    }

    queimas_mes = {
        "taxa_erro_worker": mensal["taxa_erro_worker"]["observado_pct"] / limite_erro,
        "cobertura_fiscal": (100 - mensal["cobertura_fiscal"]["observado_pct"]) / 1.0,
        "disponibilidade_busca": (100 - mensal["disponibilidade_busca"]["observado_pct"])
        / limite_disponibilidade,
        "latencia_extracao": mensal["latencia_extracao"]["acima_do_objetivo_pct"] / limite_acima_p95,
    }

    def situacao(queima: float) -> dict:
        """Converte queima média em consumo do orçamento no mês."""
        consumido = round(queima * fracao_mes * 100, 1)
        return {
            "queima_media": round(queima, 3),
            "consumido_pct": consumido,
            "restante_pct": round(max(0.0, 100 - consumido), 1),
            "ritmo": ("saud\u00e1vel" if consumido <= limiar_saudavel
                      else "acelerado" if consumido <= 100 else "or\u00e7amento esgotado"),
        }

    error_budget = {
        "sli_referencia": "taxa_erro_worker",
        "janela": {
            "rotulo": "m\u00eas de refer\u00eancia 2026-01",
            "dias_decorridos": dias_decorridos,
            "dias_totais": dias_janela,
            "limiar_saudavel_pct": limiar_saudavel,
        },
        "formula": (
            "queima = taxa de erro observada / (100% - objetivo do SLI); "
            "consumo do or\u00e7amento = queima m\u00e9dia x (dias decorridos / dias da janela); "
            "as janelas de 1 h, 6 h e 24 h do gr\u00e1fico s\u00e3o m\u00e9dias m\u00f3veis simples "
            "sobre a s\u00e9rie hor\u00e1ria de 26 pontos"
        ),
        "instante_do_pico": serie[pico]["instante"],
        "incidente_na_janela": "INC-DEMO-02",
        "principal": {
            **situacao(queimas_mes["taxa_erro_worker"]),
            "sli_id": "taxa_erro_worker",
            "objetivo_pct": limite_erro,
            "observado_mes_pct": mensal["taxa_erro_worker"]["observado_pct"],
            "observado_janela_26h_pct": taxa_erro_26h,
            "queima_janela_26h": round(taxa_erro_26h / limite_erro, 3),
            "arquivo_regras": "observabilidade/prometheus-rules.yml",
        },
        "janela_26h": {
            "taxa_erro_pct": taxa_erro_26h,
            "cobertura_pct": cobertura_26h,
            "erro_disponibilidade_pct": erro_disp_26h,
            "acima_de_12s_pct": acima_p95_26h,
            "nota": (
                "a janela curta de 26 horas cont\u00e9m o pico do incidente INC-DEMO-02; "
                "por isso a queima da janela \u00e9 maior que a m\u00e9dia do m\u00eas"
            ),
        },
        "limiares": {
            "paginar": {"janela": "1 h", "queima_minima": 14.4,
                        "motivo": "queima r\u00e1pida com pouco or\u00e7amento restante"},
            "ticket_urgente": {"janela": "6 h", "queima_minima": 6.0,
                               "motivo": "consumo 6x acima do ritmo do or\u00e7amento"},
            "revisao_semanal": {"janela": "24 h", "queima_minima": 1.0,
                                "motivo": "no ritmo exato do or\u00e7amento"},
        },
        "por_sli": [
            {"sli_id": "taxa_erro_worker", "tipo": "max",
             "objetivo_pct": limite_erro,
             "observado_mes_pct": mensal["taxa_erro_worker"]["observado_pct"],
             "observado_janela_26h_pct": taxa_erro_26h,
             **situacao(queimas_mes["taxa_erro_worker"])},
            {"sli_id": "cobertura_fiscal", "tipo": "min",
             "objetivo_pct": 99.0,
             "observado_mes_pct": mensal["cobertura_fiscal"]["observado_pct"],
             "observado_janela_26h_pct": cobertura_26h,
             **situacao(queimas_mes["cobertura_fiscal"])},
            {"sli_id": "disponibilidade_busca", "tipo": "min",
             "objetivo_pct": 99.5,
             "observado_mes_pct": mensal["disponibilidade_busca"]["observado_pct"],
             "observado_janela_26h_pct": round(100 - erro_disp_26h, 3),
             **situacao(queimas_mes["disponibilidade_busca"])},
            {"sli_id": "latencia_extracao", "tipo": "max",
             "objetivo_p95_seg": 12.0, "objetivo_acima_pct": limite_acima_p95,
             "p95_mes_seg": mensal["latencia_extracao"]["p95_mes_seg"],
             "acima_do_objetivo_mes_pct": mensal["latencia_extracao"]["acima_do_objetivo_pct"],
             "acima_do_objetivo_janela_pct": acima_p95_26h,
             **situacao(queimas_mes["latencia_extracao"])},
        ],
    }

    cobertura_por_empresa = []
    for indice, (codigo, nome, uf, _) in enumerate(EMPRESAS):
        esperados = rng.randint(900, 2400)
        cobertura_alvo = [99.4, 99.1, 98.2, 99.6, 97.8, 99.0, 99.3, 98.7][indice]
        validados = int(round(esperados * cobertura_alvo / 100))
        cobertura_por_empresa.append({
            "empresa_id": codigo,
            "empresa_nome": nome,
            "uf": uf,
            "mes_referencia": "2026-01",
            "documentos_esperados": esperados,
            "documentos_publicados": validados,
            "pendentes": esperados - validados,
            "cobertura_pct": cobertura_alvo,
            "atingiu_objetivo": cobertura_alvo >= 99.0,
        })

    return serie, burn, error_budget, cobertura_por_empresa


def gerar_slis() -> list[dict]:
    return [
        {
            "id": "cobertura_fiscal",
            "nome": "Cobertura fiscal",
            "objetivo": "99,0% dos documentos obrigatórios publicados até D+5",
            "unidade": "%",
            "direcao": "min",
            "limite": 99.0,
            "janela": "30 dias",
            "medida": "documentos publicados / documentos esperados, por empresa demo e mês de referência",
            "consulta_prometheus": "sum(rate(documentos_publicados_total[30d])) / sum(rate(documentos_esperados_total[30d]))",
            "arquivo_regras": "observabilidade/prometheus-rules.yml",
        },
        {
            "id": "latencia_extracao",
            "nome": "Latência de extração (p95)",
            "objetivo": "p95 da etapa de extração <= 12 s",
            "unidade": "s",
            "direcao": "max",
            "limite": 12.0,
            "janela": "30 dias",
            "medida": "histograma de duração da etapa de extração (service_name=worker-extracao)",
            "consulta_prometheus": "histogram_quantile(0.95, sum by (le) (rate(extracao_duracao_seconds_bucket[30d])))",
            "arquivo_regras": "observabilidade/prometheus-rules.yml",
        },
        {
            "id": "taxa_erro_worker",
            "nome": "Taxa de erro do worker",
            "objetivo": "<= 0,5% das extrações com falha definitiva",
            "unidade": "%",
            "direcao": "max",
            "limite": 0.5,
            "janela": "30 dias",
            "medida": "extrações com resultado=falha_definitiva / total de extrações",
            "consulta_prometheus": "sum(rate(extracoes_total{resultado=\"falha_definitiva\"}[30d])) / sum(rate(extracoes_total[30d]))",
            "arquivo_regras": "observabilidade/prometheus-rules.yml",
        },
        {
            "id": "disponibilidade_busca",
            "nome": "Disponibilidade da API de busca",
            "objetivo": "99,5% das requisições respondidas sem 5xx",
            "unidade": "%",
            "direcao": "min",
            "limite": 99.5,
            "janela": "30 dias",
            "medida": "requisições 2xx/3xx/4xx / total de requisições medida no Ingress",
            "consulta_prometheus": "sum(rate(http_server_duration_milliseconds_count{http_response_status_code!~\"5..\"}[30d])) / sum(rate(http_server_duration_milliseconds_count[30d]))",
            "arquivo_regras": "observabilidade/prometheus-rules.yml",
        },
    ]


def gerar_incidentes(instante_pico: datetime) -> list[dict]:
    """8 incidentes sintéticos; o S1 está ancorado no pico da série horária."""
    rng = random.Random(SEED_PADRAO + 6)
    modelos = [
        ("Imagem base nova elevou o cold start do worker de extração", "S2", "latencia_extracao",
         "Registry sem cache de camadas no nó", "Reversão da imagem e fixação do digest", 3),
        ("Fila acumulou após queda do serviço de arquivos do exemplo", "S1", "cobertura_fiscal",
         "Tempo limite do conector sem reconexão", "Backoff com ponto de retomada por pasta", None),
        ("Regra de validação rejeitou leiaute 4.00 recém-publicado", "S2", "taxa_erro_worker",
         "Esquema novo sem entrada no mapa de campos", "Dicionário de campos atualizado e teste de contrato", 15),
        ("Consulta sem índice gerou varredura completa na coleção da demo", "S3", "disponibilidade_busca",
         "Filtro novo sem índice composto", "Índice composto e limite de página por padrão", 5),
        ("Worker em loop de reinício por secret montado ausente", "S2", "taxa_erro_worker",
         "Rollout antes da criação do secret", "Job de preparação e verificação no pipeline", 8),
        ("Conflito de chave duplicada na publicação por corrida de dois workers", "S3", "cobertura_fiscal",
         "Lock de arquivo não aplicado", "Lock distribuído via ETag no repositório de destino", 14),
        ("Alerta de cobertura ficou silencioso por rótulo renomeado", "S3", "cobertura_fiscal",
         "Regra sem teste de métrica presente", "Teste de regra no CI e sintaxe validada antes do deploy", 19),
        ("Retenção curta do Loki impediu correlação de trace antigo", "S3", "disponibilidade_busca",
         "Política de retenção divergente por tenant", "Padronização de retenção e consulta de arquivo", 22),
    ]
    incidentes = []
    for i, (titulo, sev, sli, causa, desfecho, dia) in enumerate(modelos):
        if dia is None:
            # incidente dentro da janela da série: bate com o pico de queima
            inicio = instante_pico
            mttd, mttr = 7, 148
        else:
            inicio = BASE - timedelta(days=dia, hours=rng.randint(0, 9))
            mttd = rng.randint(3, 42)
            mttr = rng.randint(18, 210)
        incidentes.append({
            "id": f"INC-DEMO-{i + 1:02d}",
            "titulo": titulo,
            "severidade": sev,
            "sli_id": sli,
            "inicio": iso(inicio),
            "deteccao": iso(inicio + timedelta(minutes=mttd)),
            "resolucao": iso(inicio + timedelta(minutes=mttd + mttr)),
            "mttd_min": mttd,
            "mttr_min": mttr,
            "causa_sintetica": causa,
            "desfecho": desfecho,
            "detectado_por": rng.choice(["alerta de burn rate", "painel de SLI",
                                         "conferente da demo", "chamado interno de exemplo"]),
            "slo_furado": sev == "S1" or mttr > 120,
            "dentro_da_janela_da_serie": dia is None,
            "acoes": [
                "post-mortem sem culpa registrado na fila de demonstração",
                f"PR de exemplo {f'INC-DEMO-{i + 1:02d}'}-fix",
                "regra de alerta ajustada para o burn rate do SLI afetado",
            ],
        })
    return incidentes


def gerar_indicadores(serie: list[dict]) -> dict:
    ultimo = serie[-1]
    janela_24 = serie[-24:]
    concluidas = sum(p["extracoes_concluidas"] for p in janela_24)
    falhas = sum(p["extracoes_falhas"] for p in janela_24)
    sucesso = round(concluidas / (concluidas + falhas) * 100, 2)
    pico_p95 = max(p["latencia_p95_seg"] for p in janela_24)
    media_24 = sum(p["extracoes_concluidas"] for p in janela_24) / len(janela_24)
    return {
        "documentos_indexados": {
            "valor": 186432, "unidade": "docs", "rotulo": "Documentos indexados",
            "delta_pct": 1.8, "delta_sentido": "subiu",
            "detalhe": "a tabela mostra 40 documentos de exemplo; o restante é agregado ilustrativo",
            "fonte": "coleção de documentos da demonstração",
            "tom": "info",
        },
        "extracoes_ultima_hora": {
            "valor": ultimo["extracoes_concluidas"], "unidade": "extrações",
            "rotulo": "Extrações na última hora",
            "delta_pct": round((ultimo["extracoes_concluidas"] - media_24) / media_24 * 100, 1),
            "delta_sentido": "subiu" if ultimo["extracoes_concluidas"] >= media_24 else "desceu",
            "detalhe": f"último ponto de uma série de {QTD_PONTOS_SERIE} horas ({ultimo['hora_rotulo']})",
            "fonte": "extracoes_total{resultado=\"concluida\"}",
            "tom": "ok",
        },
        "taxa_sucesso": {
            "valor": sucesso, "unidade": "%", "rotulo": "Taxa de sucesso (24 h)",
            "delta_pct": -0.4, "delta_sentido": "desceu",
            "detalhe": f"{concluidas} concluídas contra {falhas} falhas na janela",
            "fonte": "pipeline de extração",
            "tom": "ok" if sucesso >= 99.0 else "alerta",
        },
        "latencia_p95": {
            "valor": ultimo["latencia_p95_seg"], "unidade": "s", "rotulo": "Latência p95 (extração)",
            "delta_pct": round((ultimo["latencia_p95_seg"] - pico_p95) / pico_p95 * 100, 1),
            "delta_sentido": "desceu" if ultimo["latencia_p95_seg"] <= pico_p95 else "subiu",
            "detalhe": f"pico da janela de 24 h: {pico_p95:.2f} s",
            "fonte": "extracao_duracao_seconds_bucket",
            "tom": "ok" if ultimo["latencia_p95_seg"] <= 12 else "alerta",
        },
    }


def gerar_escala() -> dict:
    return {
        "rotulo": "ordem de grandeza ilustrativa",
        "aviso": (
            "Estes valores dão proporção ao desenho e servem para discutir arquitetura. "
            "Não são medições de um ambiente real e não devem ser usados para dimensionamento."
        ),
        "itens": [
            {"nome": "documentos fiscais por mês", "faixa": "1,2 mi a 1,8 mi", "nota": "variação sazonal de ±25%"},
            {"nome": "empresas no escopo", "faixa": "8 a 24", "nota": "a demo mostra as 8 EMP-DEMO-0001 a 0008"},
            {"nome": "requisições de busca", "faixa": "30 a 80 rps", "nota": "média em horário comercial"},
            {"nome": "pessoas usando por dia", "faixa": "25 a 60", "nota": "conferência, auditoria e consulta"},
            {"nome": "réplicas da API", "faixa": "4 a 12", "nota": "HPA documentada em k8s/hpa.yaml"},
            {"nome": "workers de extração", "faixa": "3 a 24", "nota": "escala por profundidade de fila"},
            {"nome": "volume no banco", "faixa": "dezenas de GB", "nota": "documentos + auditoria + cadastros"},
            {"nome": "retenção de logs", "faixa": "14 a 30 dias", "nota": "Loki com retenção por tenant"},
        ],
    }


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------
def montar(seed: int) -> dict:
    global SEED_PADRAO
    SEED_PADRAO = seed
    serie, burn, error_budget, cobertura = gerar_serie()
    documentos, tracos = gerar_documentos()
    dados = {
        "aviso": gerar_aviso(),
        "identidade": gerar_identidade(),
        "autenticacao": gerar_autenticacao(),
        "empresas": gerar_empresas(),
        "tipos_documento": gerar_tipos(),
        "status_extracao": gerar_status(),
        "repositorio": gerar_repositorio(),
        "schema_extracao": gerar_schema_extracao(),
        "indicadores": gerar_indicadores(serie),
        "documentos": documentos,
        "tracos": tracos,
        "pipeline": gerar_pipeline(documentos),
        "workflows": gerar_workflows(),
        "serie_oraria": serie,
        "burn_rate": burn,
        "error_budget": error_budget,
        "cobertura_por_empresa": cobertura,
        "slis": gerar_slis(),
        "incidentes": gerar_incidentes(datetime.fromisoformat(
            error_budget["instante_do_pico"])),
        "escala_ilustrativa": gerar_escala(),
    }
    return dados


# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------
def serializar(dados: dict) -> str:
    return json.dumps(dados, ensure_ascii=False, indent=2) + "\n"


CABECALHO_JS = (
    "// Arquivo GERADO por tools/gen_data.py — NÃO EDITE À MÃO.\n"
    "// Fallback para uso via file:// (o fetch de JSON é bloqueado pela política de origem).\n"
    "// Mesmo conteúdo, byte a byte no corpo JSON, de data/sinteticos.json.\n"
)


def conteudo_js(texto_json: str) -> str:
    return CABECALHO_JS + "window.DEMO_DATA = " + texto_json.rstrip("\n") + ";\n"


def validar_saida(dados: dict) -> None:
    """Checagens estruturais + varredura anti-vazamento do conteúdo gerado."""
    erros: list[str] = []
    contagens = {
        "documentos": (len(dados["documentos"]), QTD_DOCUMENTOS),
        "empresas": (len(dados["empresas"]), 8),
        "tipos_documento": (len(dados["tipos_documento"]), 6),
        "status": (len(dados["status_extracao"]), 5),
        "serie_oraria": (len(dados["serie_oraria"]), QTD_PONTOS_SERIE),
        "burn_rate": (len(dados["burn_rate"]), QTD_PONTOS_SERIE),
        "incidentes": (len(dados["incidentes"]), 8),
        "etapas_pipeline": (len(dados["pipeline"]["etapas"]), 6),
        "tracos": (len(dados["tracos"]), QTD_DOCUMENTOS),
        "workflows": (len(dados["workflows"]), 8),
    }
    for nome, (obtido, esperado) in contagens.items():
        if obtido != esperado:
            erros.append(f"{nome}: {obtido} gerados, esperado {esperado}")

    ids = [d["id"] for d in dados["documentos"]]
    if len(set(ids)) != len(ids):
        erros.append("id de documento duplicado")
    reqs = [d["request_id"] for d in dados["documentos"]]
    if len(set(reqs)) != len(reqs):
        erros.append("request_id duplicado")
    traces = [d["trace_id"] for d in dados["documentos"]]
    if len(set(traces)) != len(traces):
        erros.append("trace_id duplicado")

    for doc in dados["documentos"]:
        if not doc["id"].startswith("DOC-FISCAL-SINT-"):
            erros.append(f"id fora do padrão sintético: {doc['id']}")
        if not doc["numero_documento_demo"].startswith("NF-DEMO-"):
            erros.append(f"numero de documento fora do padrão: {doc['numero_documento_demo']}")
        if not doc["empresa_id"].startswith("EMP-DEMO-"):
            erros.append(f"empresa fora do padrão: {doc['empresa_id']}")
        if not doc["trilha_auditoria"]:
            erros.append(f"documento {doc['id']} sem trilha de auditoria")

    for etapa in dados["pipeline"]["etapas"]:
        if etapa["concluidas_24h"] < 0:
            erros.append(f"etapa {etapa['codigo']} com contagem negativa")

    if [f["documento_id"] for f in dados["pipeline"]["fila_falhas"]] != \
       [d["id"] for d in dados["documentos"] if d["status"] == "falha"]:
        erros.append("fila de falhas não bate com os documentos em falha")

    texto = serializar(dados)
    problemas = leak_check.validar_texto_gerado(texto.splitlines(), "saida gerada")
    if problemas:
        erros.append("o conteúdo gerado casou com a varredura anti-vazamento:")
        erros.extend(problemas[:25])

    if erros:
        print("FALHOU a validação do gerador:", file=sys.stderr)
        for erro in erros:
            print(f"  - {erro}", file=sys.stderr)
        raise SystemExit(1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gera os dados sintéticos da demonstração")
    parser.add_argument("--seed", type=int, default=SEED_PADRAO,
                        help=f"seed do gerador (padrão {SEED_PADRAO})")
    parser.add_argument("--out", default=str(RAIZ / "data"),
                        help="diretório de saída (padrão: data/) da raiz do repositório")
    parser.add_argument("--check", action="store_true",
                        help="regenera em memória e compara byte a byte com o que está em disco")
    parser.add_argument("--stdout", action="store_true",
                        help="imprime o JSON na saída padrão e não escreve arquivo")
    args = parser.parse_args(argv)

    dados = montar(args.seed)
    validar_saida(dados)
    texto = serializar(dados)

    if args.stdout:
        sys.stdout.write(texto)
        return 0

    saida = Path(args.out)
    if args.check:
        problemas = []
        for nome, esperado in ((ARQ_JSON, texto), (ARQ_JS, conteudo_js(texto))):
            caminho = saida / nome
            if not caminho.is_file():
                problemas.append(f"{nome}: ausente em disco")
                continue
            atual = caminho.read_bytes()
            if atual != esperado.encode("utf-8"):
                problemas.append(f"{nome}: {len(atual)} bytes em disco contra "
                                 f"{len(esperado.encode('utf-8'))} bytes gerados agora")
        if problemas:
            print("FALHOU --check: a saída não é reproduzível byte a byte", file=sys.stderr)
            for problema in problemas:
                print(f"  - {problema}", file=sys.stderr)
            print("Regenere com: python3 tools/gen_data.py --seed "
                  f"{args.seed} --out {args.out}", file=sys.stderr)
            return 1
        print(f"ok: saída idêntica byte a byte para a seed {args.seed} "
              f"({len(texto.encode('utf-8'))} bytes de JSON em 2 arquivos)")
        return 0

    saida.mkdir(parents=True, exist_ok=True)
    caminho_json = saida / ARQ_JSON
    caminho_js = saida / ARQ_JS
    caminho_json.write_text(texto, encoding="utf-8")
    caminho_js.write_text(conteudo_js(texto), encoding="utf-8")
    print(f"gerado: {caminho_json} ({caminho_json.stat().st_size} bytes)")
    print(f"gerado: {caminho_js} ({caminho_js.stat().st_size} bytes)")

    resumo = {
        "seed": args.seed,
        "empresas": len(dados["empresas"]),
        "tipos_documento": len(dados["tipos_documento"]),
        "documentos": len(dados["documentos"]),
        "tracos": len(dados["tracos"]),
        "pontos_serie": len(dados["serie_oraria"]),
        "incidentes": len(dados["incidentes"]),
        "etapas_pipeline": len(dados["pipeline"]["etapas"]),
        "falhas_na_fila": len(dados["pipeline"]["fila_falhas"]),
        "workflows": len(dados["workflows"]),
    }
    print("resumo: " + " ".join(f"{k}={v}" for k, v in resumo.items()))
    print("validação: contagens, prefixos sintéticos e varredura anti-vazamento passaram")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
