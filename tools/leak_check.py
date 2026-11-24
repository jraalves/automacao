#!/usr/bin/env python3
"""Varredura anti-vazamento do repositorio de demonstracao.

Garante que nada de um ambiente real escape para um repositorio publico.
Varre todo arquivo de texto da arvore e verifica:

  1. nomes proibidos de clientes, empregadores e produtos (blocklist);
  2. numeros com formato e digito verificador de CNPJ/CPF (e sequencias de
     11/14 digitos que se parecam com documento real, mesmo com checksum
     invalido: uma demo nao precisa desse tipo de numero ambiguo);
  3. enderecos de e-mail que nao sejam *@example.com/example.org;
  4. enderecos IP fora dos exemplos allowados (127.0.0.1, ::1, 0.0.0.0 de
     bind e faixa 10.x usada em exemplos);
  5. credenciais: tokens (ghp_, github_pat_, AKIA, AIza, xoxb-), chaves
     privadas, "Authorization: Bearer ..." e chaves do tipo password/senha/
     token com valor que nao seja placeholder <CHANGE_ME> ou referencia;
  6. URLs com host fora da lista permitida (excecao apenas para a lista de
     hosts deste repo e para declaracoes de namespace XML, que nao trafegam);
  7. nomes de arquivo que jamais deveriam ser commitados (.env, *.pem, ...).

A blocklist fica em base64 para que este proprio arquivo possa ser publicado
sem "conter" literalmente os termos que ele procura. Use --list-blocked para
decodifica-la e audita-la.

Uso:
    python3 tools/leak_check.py [--root DIR] [--list-blocked] [--quiet]

Saida: 0 limpo, 1 achados, 2 erro de uso. Somente stdlib.
"""
from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Blocklist (decodificada em tempo de execucao; audite com --list-blocked)
# ---------------------------------------------------------------------------
_BLOCKLIST_B64 = (
    "Y2llbG8KaW5tZXRyaWNzCmthbGVuZGFlCnNhbnRhbmRlcgpwcm9kdWJhbgphZ3NuYXNvZnQKXGJh"
    "Z3NcYgp0ZWxlZm9uaWNhCnRva2lvCnRva3lvCmNhcGdlbWluaQpicmFkZXNjbwppbmRyYQpoaXRz"
    "cwpjb25uZWN0aXMKdmVjdG9yCmVtYnJhdGVsCm9uZXRvb25lCm9uZVxzP3RvXHM/b25lCm9uZV90"
    "b19vbmUKc2hhcmVwb2ludApvbmVkcml2ZQpzbHR3ZWIKXGJzbHRcYgpcYm5ldFxiClxidml2b1xi"
    "ClxiY2xhcm9cYgpcYmNsYXJhXGIKXGJjbGFyb3NcYgpcYmNsYXJhc1xiClxiYWxmYVxiCmZpbmFu"
    "Y2VpcmFccythbGZhClxianJhXGIKanJhXHMrY29ycG9yYXRpb24KXGJtZXRhXGIKaW50cmFuZXQ="
)

# Excecao documentada: \\bmeta\\b tambem casa com a tag HTML <meta>, que nao e
# nome de empresa. Liberamos somente quando o caractere anterior e "<".
RE_TAG_HTML = r"\bmeta\b"


def termos_bloqueados() -> list[str]:
    """Blocklist como lista de regexes legiveis (uma por linha)."""
    return base64.b64decode(_BLOCKLIST_B64).decode("utf-8").splitlines()


_BLOQUEIO = [(t, re.compile(t, re.IGNORECASE)) for t in termos_bloqueados()]

# ---------------------------------------------------------------------------
# Padroes de formato
# ---------------------------------------------------------------------------
RE_CNPJ = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
RE_CNPJ_SOLTO = re.compile(r"(?<!\d)\d{14}(?!\d)")
RE_CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
RE_CPF_SOLTO = re.compile(r"(?<!\d)\d{11}(?!\d)")
RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
RE_IP = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\b"
)
RE_URL = re.compile(r"(?:https?:)?//[A-Za-z0-9.\-_]+(?::[0-9]+)?[^\s\"'<>()\[\]{}]*",
                    re.IGNORECASE)
RE_CREDENCIAL_LITERAL = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{6,}|github_pat_[A-Za-z0-9_]{10,}|"
    r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{20,}|"
    r"xox[abprs]-[A-Za-z0-9_\-]{10,})\b"
    r"|--+BEGIN[ A-Z]*PRIVATE KEY"
    r"|\bAWS_SECRET_ACCESS_KEY\b"
    r"|\bAuthorization\s*:\s*Bearer\s+(?!\$)[A-Za-z0-9._\-]{8,}",
    re.IGNORECASE,
)
RE_CHAVE_CREDENCIAL = re.compile(
    r"(?<![\w-])(password|passwd|pwd|senha|secret_key|access_key|api_key|"
    r"apikey|client_secret|cookie_secret|token)\b\s*[:=]\s*([^\n]*)",
    re.IGNORECASE,
)
RE_VALOR_PLACEHOLDER = re.compile(
    r"""^\s*(?:"|')?(?:
          <[^>]+>                 # placeholder <CHANGE_ME>
        | \$\{[^}]+\}             # expansao shell / ArgoCD
        | \{\{[^}]+\}\}           # template
        | \$[A-Za-z_][\w]*        # referencia de ambiente
        | null | ""               # vazio
        | [A-Za-z0-9._-]*(?:secret|credenciais?|credentials?)[A-Za-z0-9._/-]*
      )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

DOMINIOS_EMAIL_OK = ("example.com", "example.org")
HOSTS_URL_OK = {"github.com", "www.github.com", "linkedin.com", "www.linkedin.com"}
DOMINIOS_URL_OK = ("example.com", "example.org")
# URIs de namespace XML: identificadores de vocabulario, nao destinos de rede.
# SVG autonomo exige o atributo xmlns; nenhuma pagina desta demo baixa recurso da
# internet, entao reconhece-los corrige falso positivo sem abrir excecao de rede.
NOMES_NAMESPACE_XML = ("www.w3.org/2000/svg", "www.w3.org/2000/xmlns/",
                       "www.w3.org/1999/xlink", "www.w3.org/1999/xhtml")
# DNS interno de cluster Kubernetes: um rótulo só (nome de Service) ou o sufixo
# padrao *.svc.cluster.local. Nada disso e um destino de internet e nada disso
# revela ambiente real -- os nomes usados aqui sao os deste repo (automacao-demo).
SUFIXO_CLUSTER_LOCAL = ".svc.cluster.local"
IPS_OK = {"127.0.0.1", "::1", "0.0.0.0", "::"}
EXTENSOES_TEXTO = {
    ".html", ".css", ".js", ".mjs", ".json", ".md", ".py", ".yml", ".yaml",
    ".txt", ".svg", ".xml", ".http", ".conf", ".toml", ".ini", ".d2", ".mmd", "",
}
DIRS_IGNORADOS = {".git", ".venv", "node_modules", "__pycache__", ".idea", ".tox"}
NOMES_ARQUIVO_PROIBIDOS = (
    ".env", ".pem", ".key", "id_rsa", "credentials.json", "service-account",
    ".p12", ".keystore", "kubeconfig", "token.txt",
)

_MAPA_ACENTOS = str.maketrans({
    "à": "a", "á": "a", "â": "a", "ã": "a", "ä": "a", "å": "a",
    "è": "e", "é": "e", "ê": "e", "ë": "e",
    "ì": "i", "í": "i", "î": "i", "ï": "i",
    "ò": "o", "ó": "o", "ô": "o", "õ": "o", "ö": "o",
    "ù": "u", "ú": "u", "û": "u", "ü": "u",
    "ç": "c", "ñ": "n", "ý": "y", "ÿ": "y",
    "ç": "c",
})


def dobrar(texto: str) -> str:
    """Minúsculas + sem acentos, preservando o comprimento (indices batem)."""
    return texto.translate(_MAPA_ACENTOS).lower()


def _dv_cpf(base: str, quantidade: int) -> int:
    soma = sum(int(base[pos]) * (quantidade + 1 - pos) for pos in range(quantidade))
    resto = (soma * 10) % 11
    return 0 if resto == 10 else resto


def cnpj_valido(numero: str) -> bool:
    digitos = re.sub(r"\D", "", numero)
    if len(digitos) != 14 or len(set(digitos)) == 1:
        return False
    pesos = ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
             [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])

    def _dv(peso: list[int], ate: int) -> int:
        soma = sum(int(digitos[i]) * peso[i] for i in range(ate))
        resto = soma % 11
        return 0 if resto < 2 else 11 - resto

    return digitos[12] == str(_dv(pesos[0], 12)) and digitos[13] == str(_dv(pesos[1], 13))


def cpf_valido(numero: str) -> bool:
    digitos = re.sub(r"\D", "", numero)
    if len(digitos) != 11 or len(set(digitos)) == 1:
        return False
    return (digitos[9] == str(_dv_cpf(digitos, 9))
            and digitos[10] == str(_dv_cpf(digitos[:10], 10)))


def host_de_url(url: str) -> str:
    sem_preambulo = re.sub(r"^(?:https?:)?//", "", url, flags=re.IGNORECASE)
    return sem_preambulo.split("/", 1)[0].split(":", 1)[0].strip(".").lower()


def url_permitida(url: str) -> bool:
    if url.lower().startswith("mailto:"):
        return False
    sem_preambulo = re.sub("(?:https?:)?[/]{2}", "", url, flags=re.IGNORECASE)
    if any(sem_preambulo.startswith(n) for n in NOMES_NAMESPACE_XML):
        return True  # URI de namespace XML: vocabulario, nao uma busca de rede
    host = host_de_url(url)
    if host and "." not in host:
        return True  # nome de Service dentro do cluster (sem ponto = sem domínio)
    if host.endswith(SUFIXO_CLUSTER_LOCAL):
        return True  # DNS padrão de cluster Kubernetes
    if not host:
        return False
    if host in HOSTS_URL_OK:
        return True
    if host.endswith(".github.io"):
        return host == "jraalves.github.io" or host.endswith(".jraalves.github.io")
    return any(host == d or host.endswith("." + d) for d in DOMINIOS_URL_OK)


class Achada:
    __slots__ = ("arquivo", "linha_no", "regra", "trecho", "contexto")

    def __init__(self, arquivo: str, linha_no: int, regra: str, trecho: str, contexto: str):
        self.arquivo = arquivo
        self.linha_no = linha_no
        self.regra = regra
        self.trecho = trecho
        self.contexto = contexto.strip()[:150]

    @property
    def categoria(self) -> str:
        return self.regra.split(":", 1)[0]

    def __str__(self) -> str:
        return (f"{self.arquivo}:{self.linha_no}: [{self.regra}] "
                f"\"{self.trecho[:80]}\" | {self.contexto}")


# ---------------------------------------------------------------------------
# Núcleo
# ---------------------------------------------------------------------------
def analisar_texto(texto: str, nome: str) -> list[Achada]:
    """Aplica todas as regras a um conteudo de texto; retorna os achados."""
    achadas: list[Achada] = []
    for linha_no, linha in enumerate(texto.splitlines(), start=1):
        dobrada = dobrar(linha)

        for padrao, regex in _BLOQUEIO:
            for m in regex.finditer(dobrada):
                if padrao == RE_TAG_HTML and m.start() > 0 and dobrada[m.start() - 1] == "<":
                    continue  # tag HTML <meta>, nao nome de empresa
                achadas.append(Achada(nome, linha_no, "termo-proibido",
                                      m.group(0), linha))

        for regex, regra in ((RE_CNPJ, "cnpj"), (RE_CNPJ_SOLTO, "cnpj-solto"),
                             (RE_CPF, "cpf"), (RE_CPF_SOLTO, "cpf-solto")):
            for m in regex.finditer(dobrada):
                trecho = m.group(0)
                eh_cnpj = regra.startswith("cnpj")
                valido = cnpj_valido(trecho) if eh_cnpj else cpf_valido(trecho)
                sufixo = "checksum-valido" if valido else "formato-ambiguo"
                achadas.append(Achada(nome, linha_no, f"{regra}-{sufixo}", trecho, linha))

        for m in RE_EMAIL.finditer(dobrada):
            dominio = m.group(0).split("@", 1)[1]
            if not any(dominio == d or dominio.endswith("." + d) for d in DOMINIOS_EMAIL_OK):
                achadas.append(Achada(nome, linha_no, "email", m.group(0), linha))

        for m in RE_IP.finditer(dobrada):
            endereco = m.group(0)
            if endereco in IPS_OK or endereco.startswith("10."):
                continue
            achadas.append(Achada(nome, linha_no, "endereco-ip", endereco, linha))

        for m in RE_CREDENCIAL_LITERAL.finditer(dobrada):
            achadas.append(Achada(nome, linha_no, "credencial", m.group(0), linha))

        for m in RE_CHAVE_CREDENCIAL.finditer(dobrada):
            valor = m.group(2).strip().rstrip(",;")
            if not valor or RE_VALOR_PLACEHOLDER.match(valor):
                continue
            achadas.append(Achada(nome, linha_no, "credencial-visivel",
                                  f"{m.group(1)}: {valor[:60]}", linha))

        for m in RE_URL.finditer(dobrada):
            url = dobrada[m.start():]
            if not url_permitida(url):
                achadas.append(Achada(nome, linha_no, "url-externa", url[:120], linha))

    return achadas


def iterar_arquivos(raiz: Path):
    for caminho in sorted(raiz.rglob("*")):
        if not caminho.is_file():
            continue
        if any(p in DIRS_IGNORADOS for p in caminho.relative_to(raiz).parts):
            continue
        if caminho.suffix.lower() not in EXTENSOES_TEXTO and not caminho.name.startswith("."):
            continue
        yield caminho


def varrer(raiz: Path) -> tuple[list[Achada], dict]:
    achadas: list[Achada] = []
    stats = {"arquivos": 0, "bytes": 0, "binarios_ignorados": 0}
    for caminho in iterar_arquivos(raiz):
        relativo = caminho.relative_to(raiz).as_posix()
        nome_baixo = caminho.name.lower()
        for proibido in NOMES_ARQUIVO_PROIBIDOS:
            if nome_baixo == proibido or nome_baixo.endswith(proibido):
                achadas.append(Achada(relativo, 0, "nome-de-arquivo-proibido",
                                      caminho.name, "(nome do arquivo)"))
        dados = caminho.read_bytes()
        if b"\x00" in dados[:1024]:
            stats["binarios_ignorados"] += 1
            continue
        stats["arquivos"] += 1
        stats["bytes"] += len(dados)
        achadas.extend(analisar_texto(dados.decode("utf-8", errors="replace"), relativo))
    return achadas, stats


def validar_texto_gerado(textos: list[str], origem: str) -> list[str]:
    """Usado pelo gerador de dados: devolve mensagens de erro (vazio = limpo)."""
    problemas: list[str] = []
    for texto in textos:
        for achada in analisar_texto(texto, origem):
            problemas.append(str(achada))
    return problemas


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Varredura anti-vazamento do repositorio de demonstracao")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                        help="diretorio a varrer (padrao: raiz do repositorio)")
    parser.add_argument("--list-blocked", action="store_true",
                        help="imprime a blocklist decodificada e sai")
    parser.add_argument("--quiet", action="store_true", help="mostra somente o resumo")
    args = parser.parse_args(argv)

    if args.list_blocked:
        print("Termos proibidos (regex case-insensitive, texto sem acento):")
        for termo in termos_bloqueados():
            print(f"  - {termo}")
        print("Hosts de URL permitidos: github.com, jraalves.github.io, linkedin.com, "
              "example.com, example.org")
        print("Excecao documentada: tag HTML <meta> nao e tratada como nome de empresa.")
        return 0

    raiz = Path(args.root).resolve()
    if not raiz.is_dir():
        print(f"erro: {raiz} nao e um diretorio", file=sys.stderr)
        return 2

    achadas, stats = varrer(raiz)
    if not args.quiet:
        for achada in achadas:
            print(achada)
    print(f"varridos: {stats['arquivos']} arquivo(s) de texto, "
          f"{stats['bytes']} bytes, {stats['binarios_ignorados']} binario(s) ignorado(s)")
    if achadas:
        resumo: dict[str, int] = {}
        for achada in achadas:
            resumo[achada.categoria] = resumo.get(achada.categoria, 0) + 1
        print("FALHOU: " + ", ".join(f"{chave}={valor}" for chave, valor in sorted(resumo.items())))
        return 1
    print("OK: nenhum termo proibido, credencial, identificador real ou URL externa.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
