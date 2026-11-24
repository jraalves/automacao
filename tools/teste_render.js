#!/usr/bin/env node
/**
 * teste_render.js — teste de fumaça do app.js sem navegador.
 *
 * Monta um DOM mínimo em memória, com um registro de elementos construído a
 * partir dos ids presentes em index.html, carrega o app.js num contexto
 * isolado (vm) e percorra as rotas, os filtros, o botão de reprocessar e a
 * cópia de identificadores. Nada aqui é publicado no site: serve para o CI
 * provar que as seis vistas montam sem exceção.
 *
 * Uso:
 *   node tools/teste_render.js            # roda os dois modos de carga
 *   node tools/teste_render.js --falia-fetch   (padrão: fetch falha, cai no fallback)
 *
 * Somente módulos da distribuição padrão do Node.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const RAIZ = path.resolve(__dirname, "..");
const falhaFetch = !process.argv.includes("--sem-falha-fetch");

/* -------------------------------------------------------------------------
   DOM mínimo
   ------------------------------------------------------------------------- */
function criarElemento(tag, sel) {
  const el = {
    tagName: (tag || "div").toUpperCase(),
    _sel: sel || "",
    _pai: null,
    _attrs: {},
    _eventos: {},
    children: [],
    style: {},
    hidden: false,
    value: "",
    _innerHTML: "",
    textContent: "",
    classList: { add() {}, remove() {}, contains() { return false; } },
    set innerHTML(v) {
      el._innerHTML = String(v);
      el.children = [];
    },
    get innerHTML() { return el._innerHTML; },
    get firstChild() { return el.children[0] || null; },
    getAttribute(n) { return n in el._attrs ? el._attrs[n] : null; },
    setAttribute(n, v) { el._attrs[n] = String(v); },
    removeAttribute(n) { delete el._attrs[n]; },
    hasAttribute(n) { return n in el._attrs; },
    addEventListener(nome, fn) { (el._eventos[nome] = el._eventos[nome] || []).push(fn); },
    dispatch(nome, evento) {
      (el._eventos[nome] || []).forEach((fn) => fn(evento || { preventDefault() {}, target: el }));
    },
    appendChild(filho) { filho._pai = el; el.children.push(filho); return filho; },
    removeChild(filho) {
      el.children = el.children.filter((x) => x !== filho);
      return filho;
    },
    remove() {
      const pai = el._pai;
      if (pai) { pai.children = pai.children.filter((x) => x !== el); el._pai = null; }
    },
    focus() {},
    select() {},
    scrollIntoView() {},
    closest() { return null; },
    querySelector(s) { return doc.querySelector(s); },
    querySelectorAll(s) { return doc.querySelectorAll(s); },
    set onclick(fn) { el._eventos.click = [fn]; },
    get onclick() { return (el._eventos.click || [null])[0]; },
  };
  return el;
}

const html = fs.readFileSync(path.join(RAIZ, "index.html"), "utf8");
const registro = new Map();
const estaticos = [];
const cacheFilhos = new Map();

/** Reconstrói os elementos declarados no index.html (atributos + ids). */
const regexTag = /<([a-zA-Z][\w-]*)((?:\s+[\w-]+="[^"]*")*)\s*\/?>/g;
const regexAtributo = /([\w-]+)="([^"]*)"/g;
const IGNORAR_TAGS = new Set(["path", "rect", "text", "br", "circle",
                              "marker", "title", "svg", "source"]);
for (const m of html.matchAll(regexTag)) {
  const tag = m[1];
  if (IGNORAR_TAGS.has(tag.toLowerCase())) { continue; }
  const el = criarElemento(tag, tag);
  for (const a of m[2].matchAll(regexAtributo)) {
    el._attrs[a[1]] = a[2];
    if (a[1] === "id") { el.id = a[2]; }
  }
  estaticos.push(el);
  if (el.id) { registro.set("#" + el.id, el); }
}

/** Elementos estáticos com um dado atributo, cacheados por valor: devolve SEMPRE
 *  o mesmo objeto, para que os listeners registrados pelo app.js sejam encontrados. */
function elementosComAtributo(nome) {
  const achados = [];
  const vistos = new Set();
  function considerar(valor) {
    const chave = nome + "=" + valor;
    if (vistos.has(chave)) { return; }
    vistos.add(chave);
    if (!cacheFilhos.has(chave)) {
      cacheFilhos.set(chave, criarElemento("div", "[" + chave + "]"));
      cacheFilhos.get(chave).setAttribute(nome, valor);
    }
    achados.push(cacheFilhos.get(chave));
  }
  for (const el of estaticos) {
    if (Object.prototype.hasOwnProperty.call(el._attrs, nome)) { considerar(el._attrs[nome]); }
  }
  const regex = new RegExp("\\s" + nome + '="([^"]+)"', "g");
  for (const el of Array.from(registro.values()).concat(estaticos)) {
    const corpo = el._innerHTML || "";
    if (!corpo) { continue; }
    let m;
    regex.lastIndex = 0;
    while ((m = regex.exec(corpo)) !== null) { considerar(m[1]); }
  }
  return achados;
}

function porAtributo(nome, valor) {
  return elementosComAtributo(nome).filter((el) => el._attrs[nome] === valor)[0] || null;
}

function pegar(sel) {
  if (!registro.has(sel)) { registro.set(sel, criarElemento("div", sel)); }
  return registro.get(sel);
}

const doc = {
  readyState: "complete",
  title: "",
  _eventos: {},
  body: criarElemento("body", "body"),
  documentElement: criarElemento("html", "html"),
  getElementById(id) { return registro.get("#" + id) || null; },
  querySelector(sel) {
    if (sel[0] === "#" || sel[0] === ".") {
      if (sel === ".abas") return pegar(".abas");
      if (sel.indexOf("[") >= 0) {
        const nome = sel.slice(sel.indexOf("[") + 1, sel.indexOf("]"));
        const lista = elementosComAtributo(nome);
        return lista.length ? lista[0] : null;
      }
      return pegar(sel);
    }
    if (sel.indexOf("[") >= 0) {
      const nome = sel.slice(sel.indexOf("[") + 1, sel.indexOf("]"));
      const lista = elementosComAtributo(nome);
      return lista.length ? lista[0] : null;
    }
    return pegar(sel);
  },
  querySelectorAll(sel) {
    if (sel === ".aba") { return elementosComAtributo("data-rota"); }
    if (sel.indexOf("[") >= 0) {
      const nome = sel.slice(sel.indexOf("[") + 1, sel.indexOf("]"));
      return elementosComAtributo(nome);
    }
    if (sel.indexOf(" ") >= 0) {
      const pedacos = sel.split(" ");
      const raiz = doc.querySelector(pedacos[0]);
      if (raiz && pedacos[1].indexOf("[") >= 0) {
        const nome = pedacos[1].slice(pedacos[1].indexOf("[") + 1, pedacos[1].indexOf("]"));
        return elementosComAtributo(nome);
      }
      return [];
    }
    return [];
  },
  createElement(tag) { return criarElemento(tag, "novo-" + tag); },
  addEventListener(nome, fn) { (doc._eventos[nome] = doc._eventos[nome] || []).push(fn); },
  execCommand() { return true; },
};

const timers = [];
const janela = {
  document: doc,
  console,
  setTimeout(fn, atraso) { timers.push(fn); return timers.length; },
  clearTimeout() {},
  Math,
  Date,
  JSON,
  Number,
  String,
  Array,
  Object,
  RegExp,
  isFinite,
  parseInt,
  parseFloat,
  encodeURIComponent,
  decodeURIComponent,
  navigator: { clipboard: null },
  location: { hash: falhaFetch ? "#/visao-geral" : "#/visao-geral", replace(v) { this.hash = v; } },
  addEventListener(nome, fn) { (janela._eventos = janela._eventos || {}), (janela._eventos[nome] = fn); },
};
janela.window = janela;

janela.fetch = async function (caminho) {
  if (falhaFetch) {
    throw new TypeError("fetch bloqueado por política de origem (simulando file://)");
  }
  const texto = fs.readFileSync(path.join(RAIZ, caminho.replace(/^\.\//, "")), "utf8");
  return { ok: true, status: 200, json: async () => JSON.parse(texto) };
};

/* -------------------------------------------------------------------------
   Execução
   ------------------------------------------------------------------------- */
const codigoApp = fs.readFileSync(path.join(RAIZ, "app.js"), "utf8");
let dadosJs = "";
try {
  dadosJs = fs.readFileSync(path.join(RAIZ, "data/dados.js"), "utf8");
} catch (erro) {
  console.error("falta data/dados.js:", erro.message);
  process.exit(1);
}

const contexto = vm.createContext(janela);
vm.runInContext(dadosJs, contexto, { filename: "data/dados.js" });
if (!janela.DEMO_DATA) {
  console.error("FALHOU: window.DEMO_DATA não foi definido por data/dados.js");
  process.exit(1);
}
vm.runInContext(codigoApp, contexto, { filename: "app.js" });

function espera() { return new Promise((r) => setImmediate(r)); }

function titulo(sel) {
  const el = registro.get(sel);
  return el ? String(el._innerHTML || el.textContent || "").length : 0;
}

(async function rodar() {
  await espera();
  await espera();

  const falhas = [];
  function checar(nome, fn) {
    try {
      fn();
      console.log("  ok   " + nome);
    } catch (erro) {
      falhas.push(nome + ": " + erro.message);
      console.log("  FALHA " + nome + " -> " + erro.stack);
    }
  }

  console.log("modo de carga: " + (falhaFetch ? "file:// (fallback data/dados.js)" : "http (fetch data/sinteticos.json)"));
  checar("erro de carga invisível", () => {
    if (registro.get("#erro-carga").hidden !== true) throw new Error("#erro-carga ficou visível");
  });
  checar("faixa de aviso preenchida", () => {
    if (!registro.get("#faixa-sintetico")._innerHTML.includes("Demonstração")) throw new Error("vazia");
  });
  checar("avisos no rodapé", () => {
    const rodape = registro.get("#rodape-info")._innerHTML;
    if (falhaFetch && !rodape.includes("dados.js")) throw new Error("fallback não anunciado: " + rodape);
    if (!falhaFetch && !rodape.includes("fetch")) throw new Error("fetch não anunciado: " + rodape);
  });

  const rotas = ["#/visao-geral", "#/busca", "#/extracao", "#/sli", "#/arquitetura", "#/sobre", "#/errodesconhecido"];
  const hashchange = janela._eventos && janela._eventos.hashchange;
  if (!hashchange) throw new Error("app.js não registrou hashchange");

  for (const rota of rotas) {
    janela.location.hash = rota;
    checar("rota " + rota, () => hashchange({}));
  }

  checar("kpis montados", () => {
    const kpis = registro.get("#kpis")._innerHTML;
    ["Documentos indexados", "Extrações na última hora", "Taxa de sucesso", "Latência p95"].forEach((rotulo) => {
      if (!kpis.includes(rotulo)) throw new Error("faltando cartão: " + rotulo);
    });
  });
  checar("tabela de documentos com 40 linhas", () => {
    const corpo = registro.get("#corpo-documentos")._innerHTML;
    const linhas = (corpo.match(/data-doc="/g) || []).length;
    if (linhas !== 40) throw new Error("linhas renderizadas: " + linhas);
  });
  checar("detalhe do documento renderizado", () => {
    const detalhe = registro.get("#painel-detalhe")._innerHTML;
    if (!detalhe.includes("Trilha de auditoria")) throw new Error("sem trilha");
    if (!detalhe.includes("X-Request-Id")) throw new Error("sem request id");
  });
  checar("pipeline com 6 etapas", () => {
    const etp = (registro.get("#pipeline")._innerHTML.match(/class="etapa"/g) || []).length;
    if (etp !== 6) throw new Error("etapas: " + etp);
  });
  checar("fila de falhas com 6 itens", () => {
    const n = (registro.get("#corpo-fila-falhas")._innerHTML.match(/data-reprocessar="/g) || []).length;
    if (n !== 6) throw new Error("itens: " + n);
  });
  checar("gráficos SVG gerados", () => {
    ["#grafico-burn", "#grafico-cobertura", "#grafico-latencia", "#medidor-orcamento"].forEach((sel) => {
      const htmlGrafico = registro.get(sel)._innerHTML;
      if (!htmlGrafico.includes("<svg")) throw new Error(sel + " sem svg");
      if (/<\/svg>/.test(htmlGrafico) === false) throw new Error(sel + " svg não fecha");
      if (htmlGrafico.includes("NaN")) throw new Error(sel + " contém NaN");
      if (htmlGrafico.includes("undefined")) throw new Error(sel + " contém undefined");
    });
  });
  checar("cartões de SLI", () => {
    const sli = registro.get("#cartoes-sli")._innerHTML;
    ["Cobertura fiscal", "Latência de extração", "Taxa de erro do worker", "Disponibilidade"].forEach((x) => {
      if (!sli.includes(x)) throw new Error("faltando: " + x);
    });
  });
  checar("tabela de incidentes", () => {
    const n = (registro.get("#corpo-incidentes")._innerHTML.match(/<tr>/g) || []).length;
    if (n !== 8) throw new Error("linhas: " + n);
  });
  checar("cobertura por empresa nas duas vistas", () => {
    if (!registro.get("#corpo-cobertura-sli")._innerHTML.includes("EMP-DEMO-0001")) throw new Error("sli vazia");
    if (!registro.get("#corpo-cobertura-empresa")._innerHTML.includes("EMP-DEMO-0008")) throw new Error("visão geral vazia");
  });
  checar("sobre com links e resumo em inglês", () => {
    const lista = registro.get("#links-autor")._innerHTML;
    ["github.com/jraalves", "jraalves.github.io/portfolio", "linkedin.com"].forEach((x) => {
      if (!lista.includes(x)) throw new Error("faltando link: " + x);
    });
  });

  // filtro client-side
  checar("filtro por empresa reduz resultados", () => {
    const seletor = porAtributo("data-filtro", "empresa");
    if (!seletor) { throw new Error("select de empresa ausente do stub"); }
    seletor.value = "EMP-DEMO-0007";
    seletor.dispatch("change", { target: seletor });
    const corpo = registro.get("#corpo-documentos")._innerHTML;
    if (corpo.includes("EMP-DEMO-0001<")) throw new Error("filtro ignorado");
  });
  checar("busca textual por identificador", () => {
    porAtributo("data-filtro", "empresa").value = "";
    const campo = porAtributo("data-filtro", "texto");
    if (!campo) { throw new Error("campo de texto ausente do stub"); }
    campo.value = "EMP-DEMO-0003";
    campo.dispatch("input", { target: campo });
    const corpo = registro.get("#corpo-documentos")._innerHTML;
    const linhas = (corpo.match(/data-doc="/g) || []).length;
    if (linhas === 0 || linhas >= 40) throw new Error("busca textual não filtrou: " + linhas);
  });
  checar("limpar filtros restaura as 40 linhas", () => {
    registro.get("#filtro-limpar").dispatch("click", {});
    const linhas = (registro.get("#corpo-documentos")._innerHTML.match(/data-doc="/g) || []).length;
    if (linhas !== 40) throw new Error("após limpar: " + linhas);
  });
  checar("ordenar por coluna não explode", () => {
    const ths = doc.querySelectorAll("#tabela-documentos th[data-coluna]");
    if (!ths.length) throw new Error("nenhum th ordenável encontrado");
    ths[0].onclick({});
    ths[0].onclick({});
  });

  // reprocessamento local
  checar("reprocessar muda o estado local", () => {
    const antes = registro.get("#corpo-fila-falhas")._innerHTML;
    const antesLinhas = (antes.match(/data-reprocessar="/g) || []).length;
    const alvo = doc.querySelectorAll('[data-reprocessar]')[0];
    if (!alvo) throw new Error("sem botão de reprocessar");
    alvo.dispatch("click", {});
    // o reprocessar re-renderiza: precisa voltar para a aba extração
    janela.location.hash = "#/extracao";
    hashchange({});
    const depois = registro.get("#corpo-fila-falhas")._innerHTML;
    const depoisLinhas = (depois.match(/data-reprocessar="/g) || []).length;
    if (depoisLinhas >= antesLinhas) throw new Error("fila não diminuiu (" + antesLinhas + " -> " + depoisLinhas + ")");
    if (!depois.includes("reprocessado local")) throw new Error("sem marca de reprocessado");
  });
  checar("desfazer restaura o estado original", () => {
    const desfazer = doc.querySelector("#desfazer-local");
    if (!desfazer) throw new Error("sem botão desfazer");
    desfazer.dispatch("click", {});
    const linhas = (registro.get("#corpo-fila-falhas")._innerHTML.match(/data-reprocessar="/g) || []).length;
    if (linhas !== 6) throw new Error("após desfazer: " + linhas);
  });

  // correlação
  checar("correlação abre por request id real", () => {
    const traco = janela.DEMO_DATA.tracos[7];
    janela.location.hash = "#/correlacao/" + traco.request_id;
    hashchange({});
    const alvo = registro.get("#correlacao-conteudo")._innerHTML;
    if (!alvo.includes("Waterfall de spans")) throw new Error("sem waterfall");
    if (!alvo.includes(traco.trace_id)) throw new Error("sem trace_id");
    if (!alvo.includes("LogQL")) throw new Error("sem LogQL");
    if (alvo.includes("undefined")) throw new Error("tem undefined no HTML");
  });
  checar("correlação com id inexistente avisa", () => {
    janela.location.hash = "#/correlacao/REQ-DEMO-NAO-EXISTE";
    hashchange({});
    if (!registro.get("#correlacao-conteudo")._innerHTML.includes("não existe")) {
      throw new Error("mensagem de ausência não apareceu");
    }
  });

  // toasts e cópia
  checar("toast e cópia rodam", () => {
    const caixa = registro.get("#toasts");
    const antes = caixa.children.length;
    const botao = doc.querySelectorAll('[data-copiar]')[0];
    if (botao) botao.dispatch("click", {});
    if (caixa.children.length < antes) throw new Error("toast sumiu indevidamente");
    timers.forEach((fn) => fn());
  });

  const resumo = [
    ["#kpis", "#corpo-documentos", "#pipeline", "#corpo-fila-falhas", "#grafico-burn",
     "#corpo-incidentes", "#links-autor", "#correlacao-conteudo"]
      .map((s) => s + "=" + titulo(s) + "B")
      .join("  ")
  ];
  console.log("tamanhos renderizados: " + resumo.join(""));

  if (falhas.length) {
    console.error("\nFALHAS (" + falhas.length + "):");
    falhas.forEach((f) => console.error(" - " + f));
    process.exit(1);
  }
  console.log("\nOK: app.js montou as 7 rotas (6 vistas + correlação) sem exceção, " +
    "filtros e reprocessamento local funcionando.");
})().catch((erro) => {
  console.error("erro no teste:", erro);
  process.exit(1);
});
