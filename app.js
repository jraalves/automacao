/* ==========================================================================
   Automação de gestão fiscal — demonstração
   Vanilla JS (ES2020), sem build e sem dependências.
   - Carrega data/sinteticos.json via fetch; se a origem bloquear o fetch
     (abertura via file://), cai automaticamente em window.DEMO_DATA
     (data/dados.js).
   - NENHUMA chamada de rede além do carregamento dos próprios arquivos.
   - O botão "reprocessar" altera apenas o estado local desta página.
   ========================================================================== */
(function () {
  "use strict";

  /* ------------------------------------------------------------------------
     Utilitários de DOM e formatação
     ------------------------------------------------------------------------ */
  const $ = (seletor, raiz) => (raiz || document).querySelector(seletor);
  const $$ = (seletor, raiz) => Array.from((raiz || document).querySelectorAll(seletor));

  function esc(valor) {
    return String(valor === null || valor === undefined ? "" : valor)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /** Formato numérico pt-BR sem depender de Intl (mais determinístico). */
  function numero(valor, casas) {
    const negativo = Number(valor) < 0;
    const absoluto = Math.abs(Number(valor));
    let texto = casas === undefined ? String(absoluto) : absoluto.toFixed(casas);
    let inteiro = texto;
    let fracao = "";
    const ponto = texto.indexOf(".");
    if (ponto >= 0) {
      inteiro = texto.slice(0, ponto);
      fracao = "," + texto.slice(ponto + 1);
    }
    inteiro = inteiro.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return (negativo ? "-" : "") + inteiro + fracao;
  }

  /** As datas da demo têm hora fixa (UTC-3); fatiamos a string ISO em vez de
   *  converter para o fuso do navegador, para o valor exibido não mudar. */
  function dataHora(iso) {
    if (!iso || iso.length < 16) return "—";
    const dia = iso.slice(8, 10);
    const mes = iso.slice(5, 7);
    const ano = iso.slice(0, 4);
    const hora = iso.slice(11, 16);
    return dia + "/" + mes + "/" + ano + " " + hora;
  }
  function somenteData(iso) {
    if (!iso) return "—";
    return iso.slice(8, 10) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4);
  }
  function minutosTexto(min) {
    if (min === null || min === undefined) return "—";
    if (min < 60) return min + " min";
    const h = Math.floor(min / 60);
    const m = min % 60;
    return h + " h" + (m ? " " + m + " min" : "");
  }
  function segundosTexto(seg) {
    if (seg === null || seg === undefined) return "—";
    if (seg < 60) return numero(seg, seg < 10 ? 2 : 1) + " s";
    return numero(seg / 60, 1) + " min";
  }
  function horaDe(iso) {
    return iso ? iso.slice(11, 13) + "h" : "";
  }

  const TONS = { ok: "ok", saudavel: "ok", "saudável": "ok", acelerado: "alerta", erro: "erro",
                 alerta: "alerta", info: "info", neutro: "neutro", roxo: "roxo" };
  function tomDe(valor) {
    return TONS[valor] || "neutro";
  }
  function insignia(rotulo, tom) {
    return '<span class="insignia ' + esc(tom || "neutro") + '">' + esc(rotulo) + "</span>";
  }

  /* ------------------------------------------------------------------------
     Estado
     ------------------------------------------------------------------------ */
  const estado = {
    dados: null,
    original: null,
    origemCarga: "",
    erroCarga: null,
    filtros: { empresa: "", tipo: "", status: "", mesIni: "", mesFim: "", texto: "" },
    ordenacao: { coluna: "data_emissao", direcao: "desc" },
    documentoSelecionado: null,
    reprocessados: [],
    vistaAtiva: "visao-geral",
    rotaAnterior: "#/visao-geral"
  };

  const STATUS_POR_CODIGO = {};
  const EMPRESA_POR_ID = {};
  const SLI_POR_ID = {};

  /* ------------------------------------------------------------------------
     Carga de dados (fetch com fallback obrigatório para file://)
     ------------------------------------------------------------------------ */
  async function carregarDados() {
    const candidatos = ["data/sinteticos.json", "./data/sinteticos.json"];
    for (const caminho of candidatos) {
      try {
        const resposta = await fetch(caminho, { cache: "no-store" });
        if (!resposta.ok) throw new Error("HTTP " + resposta.status);
        const json = await resposta.json();
        return { dados: json, origem: "fetch(" + caminho + ")" };
      } catch (erro) {
        estado.erroCarga = erro;
      }
    }
    if (window.DEMO_DATA) {
      return { dados: window.DEMO_DATA, origem: "fallback: data/dados.js (window.DEMO_DATA)" };
    }
    return { dados: null, origem: "nenhuma" };
  }

  /* ------------------------------------------------------------------------
   Gráficos SVG desenhados aqui mesmo (sem biblioteca)
     ------------------------------------------------------------------------ */
  const CORES = {
    azul: "#6ec1ff", verde: "#3fb950", roxo: "#bc8cff",
    ambar: "#d29922", vermelho: "#f85149", suave: "#94a3b8", borda: "#25314a"
  };

  /**
   * Série temporal em SVG.
   * opcoes: { alturas, largura, escalaY: 'linear'|'log', minimo, maximo,
   *            limites: [{valor, rotulo, cor}], pico: {indice, rotulo},
   *            formatoY: fn, formatoX: fn, area: boolean }
   */
  function graficoSerie(pontos, series, opcoes) {
    const opts = opcoes || {};
    const L = opts.largura || 820;
    const A = opts.altura || 250;
    const M = { cima: 14, direita: 14, baixo: 30, esquerda: 48 };
    const areaPlot = L - M.esquerda - M.direita;
    const alturaPlot = A - M.cima - M.baixo;

    const valores = [];
    series.forEach((s) => pontos.forEach((p) => {
      const v = Number(p[s.chave]);
      if (isFinite(v)) valores.push(v);
    }));
    (opts.limites || []).forEach((l) => valores.push(l.valor));

    let minimo = opts.minimo !== undefined ? opts.minimo : Math.min.apply(null, valores);
    let maximo = opts.maximo !== undefined ? opts.maximo : Math.max.apply(null, valores);
    if (opts.escalaY === "log") {
      minimo = Math.max(0.02, Math.min(minimo, 0.08));
      maximo = Math.max(maximo, 1.6) * 1.25;
    } else {
      const margem = (maximo - minimo) * 0.12 || Math.abs(maximo) * 0.1 || 1;
      minimo = opts.minimo !== undefined ? minimo : Math.max(0, minimo - margem);
      maximo = opts.maximo !== undefined ? maximo : maximo + margem;
    }

    const x = (i) => M.esquerda + (pontos.length === 1 ? areaPlot / 2 : (i * areaPlot) / (pontos.length - 1));
    const y = (v) => {
      if (opts.escalaY === "log") {
        const lv = Math.log10(Math.max(minimo, v));
        const lmin = Math.log10(minimo);
        const lmax = Math.log10(maximo);
        return M.cima + alturaPlot - ((lv - lmin) / (lmax - lmin)) * alturaPlot;
      }
      const bruto = (v - minimo) / (maximo - minimo || 1);
      return M.cima + alturaPlot - Math.max(0, Math.min(1, bruto)) * alturaPlot;
    };

    const partes = [];
    partes.push('<svg class="grafico" viewBox="0 0 ' + L + " " + A + '" role="img" ' +
      'aria-label="gráfico gerado da série sintética" preserveAspectRatio="xMidYMid meet">');

    // grade horizontal + eixo Y
    const marcasY = opts.marcasY || [minimo, minimo + (maximo - minimo) / 2, maximo];
    marcasY.forEach((marca) => {
      const yy = y(marca);
      partes.push('<line class="grade-linha" x1="' + M.esquerda + '" y1="' + yy.toFixed(1) +
        '" x2="' + (L - M.direita) + '" y2="' + yy.toFixed(1) + '"/>');
      partes.push('<text x="' + (M.esquerda - 8) + '" y="' + (yy + 3).toFixed(1) +
        '" text-anchor="end">' + esc(opts.formatoY ? opts.formatoY(marca) : numero(marca, 1)) + "</text>");
    });
    // eixos
    partes.push('<line class="eixos" x1="' + M.esquerda + '" y1="' + (M.cima + alturaPlot) +
      '" x2="' + (L - M.direita) + '" y2="' + (M.cima + alturaPlot) + '"/>');
    partes.push('<line class="eixos" x1="' + M.esquerda + '" y1="' + M.cima +
      '" x2="' + M.esquerda + '" y2="' + (M.cima + alturaPlot) + '"/>');

    // rótulos do eixo X (a cada ~4 pontos)
    const passo = Math.max(1, Math.ceil(pontos.length / 7));
    pontos.forEach((p, i) => {
      if (i % passo !== 0 && i !== pontos.length - 1) return;
      partes.push('<text x="' + x(i).toFixed(1) + '" y="' + (A - 10) +
        '" text-anchor="middle">' + esc(opts.formatoX ? opts.formatoX(p, i) : p.hora_rotulo) + "</text>");
    });

    // linhas de limite
    (opts.limites || []).forEach((l) => {
      partes.push('<line class="limite" x1="' + M.esquerda + '" y1="' + y(l.valor).toFixed(1) +
        '" x2="' + (L - M.direita) + '" y2="' + y(l.valor).toFixed(1) + '" stroke="' + (l.cor || CORES.vermelho) + '"/>');
      partes.push('<text class="rotulo-eixo" x="' + (L - M.direita - 4) + '" y="' + (y(l.valor) - 5).toFixed(1) +
        '" text-anchor="end" fill="' + (l.cor || CORES.vermelho) + '">' + esc(l.rotulo) + "</text>");
    });

    // área (apenas primeira série, quando pedido)
    if (opts.area && series.length) {
      const s = series[0];
      const caminhoArea = pontos
        .map((p, i) => (i === 0 ? "M" : "L") + x(i).toFixed(1) + " " + y(Number(p[s.chave])).toFixed(1))
        .join(" ") +
        " L" + x(pontos.length - 1).toFixed(1) + " " + (M.cima + alturaPlot) +
        " L" + x(0).toFixed(1) + " " + (M.cima + alturaPlot) + " Z";
      partes.push('<path class="area" d="' + caminhoArea + '" fill="' + s.cor + '"/>');
    }

    // séries
    series.forEach((s) => {
      const tracos = [];
      pontos.forEach((p, i) => {
        const v = Number(p[s.chave]);
        if (!isFinite(v)) { return; }
        tracos.push((tracos.length ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1));
      });
      partes.push('<path class="serie" d="' + tracos.join(" ") + '" stroke="' + s.cor + '"/>');
      if (s.pontos !== false) {
        pontos.forEach((p, i) => {
          const v = Number(p[s.chave]);
          if (!isFinite(v)) return;
          partes.push('<circle class="ponto" cx="' + x(i).toFixed(1) + '" cy="' + y(v).toFixed(1) +
            ' r="2.6" fill="' + s.cor + '"><title>' + esc(p.hora_rotulo + " — " + s.nome + ": " +
            (s.formato ? s.formato(v) : numero(v, 2))) + "</title></circle>");
        });
      }
    });

    // pico de incidente
    if (opts.pico && opts.pico.indice >= 0) {
      const px = x(opts.pico.indice);
      partes.push('<line class="marcador-incidente" x1="' + px.toFixed(1) + '" y1="' + M.cima +
        '" x2="' + px.toFixed(1) + '" y2="' + (M.cima + alturaPlot) + '"/>');
      partes.push('<text class="texto-incidente" x="' + (px + 5).toFixed(1) + '" y="' + (M.cima + 12) +
        '">' + esc(opts.pico.rotulo || "incidente") + "</text>");
    }

    partes.push("</svg>");
    const legenda = (opts.legenda === false) ? "" :
      '<div class="legenda">' + series.map((s) =>
        '<span><i style="background:' + s.cor + '"></i>' + esc(s.nome) + "</span>").join("") +
      (opts.limites || []).map((l) =>
        '<span><i style="background:' + (l.cor || CORES.vermelho) + '"></i>' + esc(l.rotulo) + "</span>").join("") +
      "</div>";
    return '<div class="envolver-tabela">' + partes.join("") + "</div>" + legenda;
  }

  /** Anel de consumo do orçamento de erro. */
  function medidorAnel(pct, titulo, subtitulo) {
    const raio = 52;
    const circ = 2 * Math.PI * raio;
    const limitado = Math.max(0, Math.min(100, pct));
    const cor = limitado < 60 ? CORES.verde : limitado < 85 ? CORES.ambar : CORES.vermelho;
    return '<div class="medidor">' +
      '<svg viewBox="0 0 140 140" class="grafico" role="img" aria-label="' +
      esc(titulo + ": " + numero(pct, 1) + "% consumido") + '">' +
      '<circle cx="70" cy="70" r="' + raio + '" fill="none" stroke="#1b2434" stroke-width="12"/>' +
      '<circle cx="70" cy="70" r="' + raio + '" fill="none" stroke="' + cor + '" stroke-width="12" ' +
      'stroke-linecap="round" stroke-dasharray="' + (circ * limitado / 100).toFixed(1) + " " + circ.toFixed(1) + '" ' +
      'transform="rotate(-90 70 70)"/>' +
      '<text x="70" y="66" text-anchor="middle" fill="#e8eefc" font-size="22" font-family="monospace">' +
      numero(pct, 1) + "%</text>" +
      '<text x="70" y="86" text-anchor="middle" fill="#94a3b8" font-size="10">consumido</text>' +
      "</svg>" +
      '<div><h4 style="margin:0 0 4px;font-size:13.5px">' + esc(titulo) + "</h4>" +
      '<p style="margin:0;font-size:12.5px;color:var(--texto-suave)">' + esc(subtitulo) + "</p></div>" +
      "</div>";
  }

  /* ------------------------------------------------------------------------
     Roteamento por hash
     ------------------------------------------------------------------------ */
  const VISTAS = ["visao-geral", "busca", "extracao", "sli", "arquitetura", "sobre", "correlacao"];

  function lerRota() {
    const bruto = (location.hash || "#/visao-geral").replace(/^#\/?/, "");
    const pedacos = bruto.split("/");
    let vista = pedacos[0] || "visao-geral";
    if (VISTAS.indexOf(vista) < 0) vista = "visao-geral";
    return { vista: vista, param: decodeURIComponent(pedacos.slice(1).join("/")) };
  }

  function trocarVista(vista) {
    estado.vistaAtiva = vista;
    $$(".aba").forEach((aba) => {
      const ativa = aba.getAttribute("data-rota") === vista;
      aba.setAttribute("aria-selected", ativa ? "true" : "false");
      if (ativa) { aba.setAttribute("aria-current", "page"); } else { aba.removeAttribute("aria-current"); }
    });
    VISTAS.forEach((nome) => {
      const secao = $("#vista-" + nome);
      if (secao) { secao.hidden = nome !== vista; }
    });
    const alvo = $(".abas");
    const selecionada = $('.aba[data-rota="' + vista + '"]');
    if (alvo && selecionada && selecionada.scrollIntoView) {
      try { selecionada.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (e) { /* navegador antigo */ }
    }
  }

  /* ------------------------------------------------------------------------
     Avisos dinâmicos no topo e no rodapé
     ------------------------------------------------------------------------ */
  function cabecalhoDinamico() {
    const id = estado.dados.identidade;
    const aviso = estado.dados.aviso;
    $("#titulo-marca").textContent = id.titulo;
    $("#sub-marca").textContent = id.subtitulo;
    $("#selo-versao").innerHTML = "dados <b>v" + esc(id.versao_dados) + "</b>";
    $("#selo-seed").innerHTML = "seed <b>" + esc(id.seed) + "</b>";
    $("#selo-instante").innerHTML = "janela encerra em <b>" + esc(id.instante_base) + "</b>";
    $("#faixa-sintetico").innerHTML =
      '<span class="marcador">Demonstração</span> ' +
      "<strong>" + esc(aviso.rotulo) + "</strong> " +
      '<span class="sep">·</span> gerado por <code>' + esc(aviso.gerador) + "</code> " +
      '<span class="sep">·</span> <a href="#/sobre">o que é real e o que é fictício</a> ' +
      '<span class="sep">·</span> <a href="data/AVISO-SINTETICO.md">aviso em texto</a>';
    const aut = estado.dados.autenticacao;
    $("#faixa-auth").innerHTML =
      '<span class="marcador">Sem login</span> ' +
      "<strong>a demo roda sem autenticação</strong> " +
      '<span class="sep">·</span> em produção o acesso passa por oauth2-proxy à frente do Service ' +
      "(arquitetura documentada, não executada aqui) " +
      '<span class="sep">·</span> <a href="#/arquitetura">ver desenho</a>';
    $("#rodape-info").innerHTML =
      "Demonstração pessoal de " + esc(id.autor.nome) + " (" + esc(id.autor.papel) + "). " +
      "Dados sintéticos, licença " + esc(id.licenca) + ". " +
      "<code>" + esc(estado.origemCarga) + "</code>";
    $("#rodape-links").innerHTML =
      '<a href="' + esc(id.autor.github) + '" rel="noopener">github.com/jraalves</a> · ' +
      '<a href="' + esc(id.autor.portfolio) + '" rel="noopener">portfólio</a> · ' +
      '<a href="' + esc(id.autor.linkedin) + '" rel="noopener">LinkedIn</a> · ' +
      '<a href="README.md">README</a> · <a href="SECURITY.md">SECURITY</a>';
    void aut;
  }

  /* ------------------------------------------------------------------------
     Vista: Visão geral
     ------------------------------------------------------------------------ */
  function renderVisaoGeral() {
    const d = estado.dados;
    const ind = d.indicadores;
    const ordem = ["documentos_indexados", "extracoes_ultima_hora", "taxa_sucesso", "latencia_p95"];
    $("#kpis").innerHTML = ordem.map((chave) => {
      const k = ind[chave];
      const casas = k.unidade === "%" || k.unidade === "s" ? 2 : 0;
      const seta = k.delta_sentido === "subiu" ? "▲" : k.delta_sentido === "desceu" ? "▼" : "•";
      const corDelta = chave === "latencia_p95"
        ? (k.delta_sentido === "desceu" ? CORES.verde : CORES.ambar)
        : (k.delta_sentido === "subiu" ? CORES.verde : CORES.ambar);
      return '<article class="cartao kpi tom-' + esc(tomDe(k.tom)) + '">' +
        '<div class="rotulo">' + esc(k.rotulo) + "</div>" +
        '<div class="valor">' + esc(numero(k.valor, casas)) +
        '<span class="unid">' + esc(k.unidade) + "</span></div>" +
        '<div class="delta" style="color:' + corDelta + '">' + seta + " " +
        esc(numero(Math.abs(k.delta_pct || 0), 1)) + "% " +
        '<span style="color:var(--texto-suave)">vs. janela anterior</span></div>' +
        '<div class="fonte">' + esc(k.detalhe) + "</div>" +
        '<div class="fonte">fonte: <code>' + esc(k.fonte) + "</code></div>" +
        "</article>";
    }).join("");

    $("#nota-indicadores").innerHTML =
      '<p class="nota-pequena">Os quatro cartões vêm de <code>data/sinteticos.json</code>, bloco ' +
      "<code>indicadores</code>. O valor de documentos indexados é um agregado ilustrativo: a tabela " +
      "da aba <a href=\"#/busca\">Busca fiscal</a> mostra apenas " + d.documentos.length +
      " documentos de exemplo. Nada aqui foi medido em um ambiente real.</p>";

    const estadosRotulo = {
      executando: ["executando", "info"], concluido: ["concluído", "ok"],
      na_fila: ["na fila", "neutro"], bloqueado: ["bloqueado", "alerta"],
      falha_parcial: ["falha parcial", "erro"]
    };
    $("#corpo-workflows").innerHTML = d.workflows.map((w) => {
      const [rotulo, tom] = estadosRotulo[w.estado] || [w.estado, "neutro"];
      return "<tr>" +
        '<td><div class="destaque">' + esc(w.nome) + '</div><div class="idoc">' + esc(w.id) + " · " + esc(w.empresa_id) + "</div></td>" +
        "<td>" + insignia(rotulo, tom) + "</td>" +
        "<td>" + barraProgresso(w.progresso_pct) + "</td>" +
        '<td class="numero">' + esc(numero(w.documentos_concluidos) + " / " + numero(w.documentos_previstos)) + "</td>" +
        '<td class="numero">' + esc(numero(w.fila_de_mensagens)) + "</td>" +
        '<td><code>' + esc(w.cron) + "</code><div class=\"idoc\">" + esc(w.gatilho) + "</div></td>" +
        '<td class="idoc">' + esc(dataHora(w.inicio)) + "<br>próx.: " + esc(dataHora(w.proxima_execucao)) + "</td>" +
        "</tr>";
    }).join("");

    $("#resumo-pipeline").innerHTML = d.pipeline.etapas.map((e) => {
      const total = Math.max(1, e.entradas_24h);
      const ok = (e.concluidas_24h / total) * 100;
      const andamento = (e.em_andamento / total) * 100;
      const falha = (e.falhas_24h / total) * 100;
      return "<tr>" +
        '<td><div class="destaque">' + esc(e.nome) + '</div><div class="idoc">' + esc(e.servico) + "</div></td>" +
        '<td class="numero">' + esc(numero(e.entradas_24h)) + "</td>" +
        '<td class="numero">' + esc(numero(e.concluidas_24h)) + "</td>" +
        '<td class="numero">' + esc(numero(e.em_andamento)) + "</td>" +
        '<td class="numero" style="color:' + (e.falhas_24h > 8 ? CORES.vermelho : CORES.ambar) + '">' +
        esc(numero(e.falhas_24h)) + "</td>" +
        '<td class="numero">' + esc(segundosTexto(e.mediana_seg)) + "</td>" +
        '<td style="min-width:150px"><div class="barra-fluxo">' +
        '<i class="i-ok" style="width:' + ok.toFixed(1) + '%"></i>' +
        '<i class="i-andamento" style="width:' + andamento.toFixed(1) + '%"></i>' +
        '<i class="i-falha" style="width:' + falha.toFixed(1) + '%"></i></div></td>' +
        "</tr>";
    }).join("");

    const recentes = d.incidentes.slice().sort((a, b) => (a.inicio < b.inicio ? 1 : -1)).slice(0, 3);
    $("#corpo-incidentes-resumo").innerHTML = recentes.map((i) =>
      "<tr><td><code>" + esc(i.id) + "</code></td>" +
      '<td class="idoc">' + esc(dataHora(i.inicio)) + "</td>" +
      "<td>" + insignia(i.severidade, i.severidade === "S1" ? "erro" : i.severidade === "S2" ? "alerta" : "neutro") + "</td>" +
      "<td>" + esc(i.titulo) + "</td>" +
      '<td class="numero">' + esc(minutosTexto(i.mttr_min)) + "</td>" +
      '<td><a href="#/sli">ver SLI</a></td></tr>').join("");

    $("#corpo-cobertura-empresa").innerHTML = linhasCobertura(d.cobertura_por_empresa);
  }

  /** Linhas da tabela de cobertura por empresa (usada em duas vistas). */
  function linhasCobertura(lista) {
    return lista.map((c) => {
      const nome = '<div class="destaque">' + esc(c.empresa_nome) + '</div>' +
        '<div class="idoc">' + esc(c.empresa_id) + ' \u00b7 ' + esc(c.uf) + '</div>';
      return '<tr><td>' + nome + '</td>' +
        '<td class="numero">' + esc(numero(c.documentos_esperados)) + '</td>' +
        '<td class="numero">' + esc(numero(c.documentos_publicados)) + '</td>' +
        '<td class="numero">' + esc(numero(c.pendentes)) + '</td>' +
        '<td class="numero">' + esc(numero(c.cobertura_pct, 2)) + '%</td>' +
        '<td>' + insignia(c.atingiu_objetivo ? 'no objetivo' : 'abaixo de 99,0%',
          c.atingiu_objetivo ? 'ok' : 'alerta') + '</td></tr>';
    }).join('');
  }

  function barraProgresso(pct) {
    const cor = pct >= 99 ? CORES.verde : pct > 0 ? CORES.azul : CORES.suave;
    return '<div style="display:flex;align-items:center;gap:8px">' +
      '<div class="barra-fluxo" style="flex:1;margin:0;min-width:90px">' +
      '<i style="width:' + Math.max(0, Math.min(100, pct)).toFixed(1) + '%;background:' + cor + '"></i></div>' +
      '<span class="idoc">' + esc(numero(pct, 1)) + "%</span></div>";
  }

  /* ------------------------------------------------------------------------
     Vista: Busca fiscal
     ------------------------------------------------------------------------ */
  function montarFiltros() {
    const d = estado.dados;
    const sel = (id, itens, comVazio, rotuloCampo) => {
      const alvo = $("#" + id);
      alvo.innerHTML = (comVazio ? '<option value="">todas</option>' : "") +
        itens.map((i) => '<option value="' + esc(i.valor) + '">' + esc(i.rotulo) + "</option>").join("");
      void rotuloCampo;
    };
    sel("filtro-empresa", d.empresas.map((e) => ({ valor: e.id, rotulo: e.id + " — " + e.nome })), true);
    sel("filtro-tipo", d.tipos_documento.map((t) => ({ valor: t.codigo, rotulo: t.rotulo })), true);
    sel("filtro-status", d.status_extracao.map((s) => ({ valor: s.codigo, rotulo: s.rotulo })), true);
    const meses = Array.from(new Set(d.documentos.map((x) => x.data_referencia))).sort().reverse();
    sel("filtro-mes-ini", meses.map((m) => ({ valor: m, rotulo: m })), true, "de");
    sel("filtro-mes-fim", meses.map((m) => ({ valor: m, rotulo: m })), true, "até");

    $("#filtro-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      lerFiltros();
      aplicarFiltro();
    });
    $$("[data-filtro]", $("#filtro-form")).forEach((campo) => {
      campo.addEventListener("change", () => { lerFiltros(); aplicarFiltro(); });
      campo.addEventListener("input", () => { lerFiltros(); aplicarFiltro(); });
    });
    $("#filtro-limpar").addEventListener("click", () => {
      estado.filtros = { empresa: "", tipo: "", status: "", mesIni: "", mesFim: "", texto: "" };
      $$("[data-filtro]", $("#filtro-form")).forEach((c) => { c.value = ""; });
      estado.documentoSelecionado = null;
      aplicarFiltro();
      avisar("Filtros limpos. Estado local apenas.", "ok");
    });
  }

  function lerFiltros() {
    $$("[data-filtro]", $("#filtro-form")).forEach((c) => {
      estado.filtros[c.getAttribute("data-filtro")] = c.value;
    });
  }

  function documentosFiltrados() {
    const f = estado.filtros;
    const texto = (f.texto || "").trim().toLowerCase();
    let lista = estado.dados.documentos.filter((doc) => {
      if (f.empresa && doc.empresa_id !== f.empresa) return false;
      if (f.tipo && doc.tipo !== f.tipo) return false;
      if (f.status && doc.status !== f.status) return false;
      if (f.mesIni && doc.data_referencia < f.mesIni) return false;
      if (f.mesFim && doc.data_referencia > f.mesFim) return false;
      if (texto) {
        const bagagem = [doc.id, doc.numero_documento_demo, doc.empresa_id, doc.empresa_nome,
          doc.tipo_rotulo, doc.status_rotulo, doc.caminho_origem, doc.request_id, doc.trace_id,
          doc.motivo_falha ? doc.motivo_falha.codigo_erro : ""]
          .join(" ").toLowerCase();
        if (bagagem.indexOf(texto) < 0) return false;
      }
      return true;
    });
    const { coluna, direcao } = estado.ordenacao;
    lista = lista.slice().sort((a, b) => {
      let x = a[coluna];
      let y = b[coluna];
      if (coluna === "status") { x = a.status_rotulo; y = b.status_rotulo; }
      if (coluna === "latencia") { x = a.latencia_extracao_seg; y = b.latencia_extracao_seg; }
      if (typeof x === "number" && typeof y === "number") { return direcao === "asc" ? x - y : y - x; }
      x = String(x || ""); y = String(y || "");
      return direcao === "asc" ? x.localeCompare(y) : y.localeCompare(x);
    });
    return lista;
  }

  function aplicarFiltro() {
    const lista = documentosFiltrados();
    $("#contagem-filtro").innerHTML = "mostrando <b>" + lista.length + "</b> de <b>" +
      estado.dados.documentos.length + "</b> documentos sintéticos" +
      (estado.reprocessados.length ? " · " + estado.reprocessados.length + " reprocessado(s) no estado local" : "");
    $$("#tabela-documentos th[data-coluna]").forEach((th) => {
      const chave = th.getAttribute("data-coluna");
      const ativa = estado.ordenacao.coluna === chave;
      th.setAttribute("aria-sort", ativa ? (estado.ordenacao.direcao === "asc" ? "ascending" : "descending") : "none");
      th.querySelector(".seta-ordem").textContent = ativa ? (estado.ordenacao.direcao === "asc" ? "▲" : "▼") : "";
      th.onclick = () => {
        if (estado.ordenacao.coluna === chave) {
          estado.ordenacao.direcao = estado.ordenacao.direcao === "asc" ? "desc" : "asc";
        } else {
          estado.ordenacao.coluna = chave;
          estado.ordenacao.direcao = chave === "data_emissao" ? "desc" : "asc";
        }
        aplicarFiltro();
      };
    });

    if (!lista.length) {
      $("#corpo-documentos").innerHTML = '<tr><td class="vazio" colspan="9">Nenhum documento ' +
        "sintético bate com esses filtros. Ajuste o período ou use o botão " +
        "<em>Limpar filtros</em> acima.</td></tr>";
      renderDetalhe(null);
      return;
    }

    $("#corpo-documentos").innerHTML = lista.map((doc) => {
      const selecionado = estado.documentoSelecionado === doc.id;
      const reprocessado = estado.reprocessados.indexOf(doc.id) >= 0;
      return '<tr class="linha-acao' + (selecionado ? " selecionada" : "") + '" data-doc="' + esc(doc.id) + '">' +
        "<td><div class=\"destaque\">" + esc(doc.numero_documento_demo) + "</div>" +
        '<div class="idoc">' + esc(doc.id) + (reprocessado ? " · reprocessado local" : "") + "</div></td>" +
        "<td>" + esc(doc.empresa_id) + '<div class="idoc">' + esc(doc.uf) + " · " + esc(doc.empresa_nome) + "</div></td>" +
        "<td>" + esc(doc.tipo_rotulo) + '<div class="idoc">.' + esc(doc.extensao) + "</div></td>" +
        '<td class="idoc">' + esc(doc.data_referencia) + "<br>" + esc(somenteData(doc.data_emissao)) + "</td>" +
        "<td>" + insignia(doc.status_rotulo, tomDe(doc.status)) + "</td>" +
        '<td class="numero">' + esc(doc.latencia_extracao_seg ? numero(doc.latencia_extracao_seg, 2) + " s" : "—") + "</td>" +
        '<td class="numero">' + esc(doc.tentativas) + "</td>" +
        '<td><button class="itraze" type="button" data-trace="' + esc(doc.request_id) + '" ' +
        'title="abrir correlação de trace e logs">' + esc(doc.request_id) + "</button>" +
        '<div class="idoc">' + esc(doc.trace_id.slice(0, 16)) + "…</div></td>" +
        "<td>" + esc(dataHora(doc.data_entrada_repositorio)) + "</td>" +
        "</tr>";
    }).join("");

    $$("#corpo-documentos tr[data-doc]").forEach((tr) => {
      tr.addEventListener("click", (ev) => {
        const botao = ev.target.closest("[data-trace]");
        if (botao) {
          estado.rotaAnterior = "#/busca";
          location.hash = "#/correlacao/" + botao.getAttribute("data-trace");
          return;
        }
        estado.documentoSelecionado = tr.getAttribute("data-doc");
        aplicarFiltro();
      });
    });

    renderDetalhe(estado.documentoSelecionado
      ? estado.dados.documentos.filter((x) => x.id === estado.documentoSelecionado)[0] || lista[0]
      : lista[0]);
  }

  function renderDetalhe(doc) {
    const painel = $("#painel-detalhe");
    if (!doc) {
      painel.innerHTML = '<header><h4>Detalhe do documento</h4><span class="nota">nenhum selecionado</span></header>' +
        '<div class="corpo"><p class="vazio">Selecione uma linha da tabela.</p></div>';
      return;
    }
    const trilha = doc.trilha_auditoria.map((ev) => {
      const classe = ev.resultado === "falha" ? "falha" : (doc.status === "processando" && ev.sequencia === doc.trilha_auditoria.length ? "andamento" : "");
      return '<li class="' + classe + '">' +
        '<div class="evento">' + esc(ev.evento) + " · " + esc(ev.ator) + "</div>" +
        '<div class="hora-evento">' + esc(dataHora(ev.instante)) + " · " + esc(ev.resultado) + "</div>" +
        '<div class="detalhe-evento">' + esc(ev.detalhe) + "</div>" +
        "</li>";
    }).join("");

    const motivo = doc.motivo_falha
      ? '<p style="margin:8px 0 0">' + insignia(doc.motivo_falha.codigo_erro, "erro") + " " +
        esc(doc.motivo_falha.descricao) + "</p>"
      : "";
    const acoes = (doc.status === "falha")
      ? '<div class="botoes" style="margin-top:12px">' +
        '<button class="botao principal" type="button" data-reprocessar="' + esc(doc.id) + '">Reprocessar (estado local)</button>' +
        "</div>"
      : "";

    painel.innerHTML =
      "<header><h4>" + esc(doc.numero_documento_demo) + "</h4>" +
      insignia(doc.status_rotulo, tomDe(doc.status)) + "</header>" +
      '<div class="corpo">' +
      '<dl>' +
      linha("ID sintético", doc.id, true) +
      linha("Empresa", doc.empresa_id) +
      linha("Nome fictício", doc.empresa_nome, true) +
      linha("Tipo", doc.tipo_rotulo + " (" + doc.tipo + ")", true) +
      linha("Série/subsérie", String(doc.serie) + "/" + String(doc.subserie), true) +
      linha("Chave de acesso", doc.chave_acesso, true) +
      linha("Emissão", dataHora(doc.data_emissao), true) +
      linha("Referência", doc.data_referencia, true) +
      linha("Entrada no repositório", dataHora(doc.data_entrada_repositorio), true) +
      linha("Extensão/MIME", doc.extensao + " · " + doc.mime, true) +
      linha("Tamanho", numero(doc.tamanho_bytes) + " bytes", true) +
      linha("Checksum", doc.checksum_sha256 + "… (16 hex)", true) +
      linha("Valor do documento", doc.valor_documento, true) +
      linha("Base tributável", doc.base_tributavel, true) +
      linha("ICMS", doc.icms, true) +
      linha("Extração", doc.latencia_extracao_seg ? numero(doc.latencia_extracao_seg, 2) + " s em " + doc.tentativas + " tentativa(s)" : "sem execução", true) +
      linha("Origem", doc.caminho_origem, true) +
      linha("Destino", doc.caminho_destino || "— (ainda não publicado)", true) +
      "</dl>" + motivo + acoes +
      '<div style="margin:14px 0 6px"><span class="insignia roxo">correlação</span></div>' +
      '<div class="codigo">X-Request-Id: ' + esc(doc.request_id) + "\ntrace_id:       " + esc(doc.trace_id) +
      "\nspan_id (raiz): " + esc(doc.span_id) + "</div>" +
      '<div class="botoes" style="margin-top:10px">' +
      '<a class="botao" href="#/correlacao/' + esc(doc.request_id) + '">Abrir correlação de trace e logs</a>' +
      '<button class="botao" type="button" data-copiar="' + esc(doc.request_id) + '">Copiar X-Request-Id</button>' +
      "</div>" +
      '<h4 style="margin:18px 0 8px;font-size:13px">Trilha de auditoria</h4>' +
      '<ul class="linha-tempo">' + trilha + "</ul>" +
      '<p class="nota-pequena" style="margin-top:12px">Rótulos: ' +
      esc(doc.rotulos.join(", ")) + ". Identificadores gerados por <code>tools/gen_data.py</code>; " +
      "nenhum dado real de cliente.</p>" +
      "</div>";

    $$("[data-reprocessar]", painel).forEach((b) =>
      b.addEventListener("click", () => reprocessar(b.getAttribute("data-reprocessar"))));
    $$("[data-copiar]", painel).forEach((b) =>
      b.addEventListener("click", () => copiar(b.getAttribute("data-copiar"))));
  }

  function linha(rotulo, valor, emTexto) {
    return "<dt>" + esc(rotulo) + "</dt><dd" + (emTexto ? ' class="texto"' : "") + ">" + esc(valor) + "</dd>";
  }

  /* ------------------------------------------------------------------------
     Vista: Extração & workflow
     ------------------------------------------------------------------------ */
  function renderExtracao() {
    const d = estado.dados;
    $("#pipeline").innerHTML = d.pipeline.etapas.map((e, i) => {
      const total = Math.max(1, e.entradas_24h);
      const ok = (e.concluidas_24h / total) * 100;
      const andamento = (e.em_andamento / total) * 100;
      const falha = (e.falhas_24h / total) * 100;
      return '<div class="etapa" data-etapa="' + esc(e.codigo) + '">' +
        '<div class="passo">ETAPA ' + (i + 1) + " DE " + d.pipeline.etapas.length + "</div>" +
        "<h4>" + esc(e.nome) + "</h4>" +
        '<div class="descricao">' + esc(e.descricao) + "</div>" +
        '<div class="medidores">' +
        "<span><b>" + esc(numero(e.entradas_24h)) + "</b> entradas</span>" +
        "<span><b>" + esc(numero(e.concluidas_24h)) + "</b> concluídas</span>" +
        "<span><b>" + esc(numero(e.em_andamento)) + "</b> em andamento</span>" +
        "<span><b>" + esc(numero(e.falhas_24h)) + "</b> falhas</span>" +
        "<span>mediana <b>" + esc(segundosTexto(e.mediana_seg)) + "</b></span>" +
        "<span>p95 <b>" + esc(segundosTexto(e.p95_seg)) + "</b></span>" +
        "<span>fila <b>" + esc(numero(e.fila_espera)) + "</b></span>" +
        "<span><b>" + esc(e.reprocessavel ? "reprocessável" : "sem retry") + "</b></span>" +
        "</div>" +
        '<div class="barra-fluxo">' +
        '<i class="i-ok" style="width:' + ok.toFixed(1) + '%"></i>' +
        '<i class="i-andamento" style="width:' + andamento.toFixed(1) + '%"></i>' +
        '<i class="i-falha" style="width:' + falha.toFixed(1) + '%"></i></div>' +
        '<div class="idoc" style="margin-top:6px">' + esc(e.servico) + "</div>" +
        "</div>";
    }).join("");

    $("#cabecalho-janela").textContent = d.pipeline.janela;
    const pendentes = d.pipeline.fila_falhas.filter((f) => !f.reprocessado).length;
    $("#contador-falhas").textContent = pendentes === 0
      ? "fila vazia neste estado local (reprocessamentos j\u00e1 movidos aqui: " +
        estado.reprocessados.length + ")"
      : pendentes + " item(ns) aguardando reprocessamento \u00b7 " +
        estado.reprocessados.length + " movido(s) apenas no estado local desta p\u00e1gina";
    $("#nota-retry").innerHTML = "Política de reprocessamento: <code>" + esc(d.pipeline.politica_retry) +
      "</code>. Métrica usada no painel: <code>" + esc(d.pipeline.metrica_fila) + "</code>.";

    const mapaDocumentos = {};
    d.documentos.forEach((x) => { mapaDocumentos[x.id] = x; });

    $("#corpo-fila-falhas").innerHTML = d.pipeline.fila_falhas.map((f) => {
      const doc = mapaDocumentos[f.documento_id];
      const reprocessado = estado.reprocessados.indexOf(f.documento_id) >= 0;
      return '<tr class="' + (reprocessado ? "reprocessado" : "") + '">' +
        '<td><code>' + esc(f.documento_id) + '</code><div class="idoc">' + esc(f.empresa_id) + "</div></td>" +
        "<td>" + esc(f.tipo_rotulo) + " " + esc(f.numero_documento_demo) + "</td>" +
        '<td><div class="destaque">' + esc(f.motivo_classe) + '</div><div class="idoc">' + esc(f.etapa) + "</div></td>" +
        "<td style=\"min-width:260px\">" + esc(f.descricao) + "</td>" +
        '<td class="numero">' + esc(f.tentativas) + "</td>" +
        '<td class="idoc">' + esc(dataHora(f.ultima_tentativa)) + "</td>" +
        "<td>" + esc(f.proxima_acao) + "</td>" +
        "<td>" + (reprocessado
          ? insignia("reprocessado local", "ok")
          : '<button class="botao mini" type="button" data-reprocessar="' + esc(f.documento_id) + '"' +
            (f.reprocessavel ? "" : " disabled") + ">Reprocessar</button>") + "</td>" +
        "<td>" + (doc
          ? '<button class="itraze" type="button" data-trace="' + esc(doc.request_id) + '">' + esc(doc.request_id) + "</button>"
          : "") + "</td>" +
        "</tr>";
    }).join("");

    $$("[data-reprocessar]").forEach((b) => {
      b.onclick = () => reprocessar(b.getAttribute("data-reprocessar"));
    });
    $$("[data-trace]").forEach((b) => {
      b.onclick = () => {
        estado.rotaAnterior = "#/extracao";
        location.hash = "#/correlacao/" + b.getAttribute("data-trace");
      };
    });

    $("#corpo-schema").innerHTML = d.schema_extracao.map((c) =>
      "<tr><td><code>" + esc(c.campo) + "</code></td><td>" + esc(c.origem) + "</td>" +
      "<td>" + insignia(c.obrigatorio ? "obrigatório" : "opcional", c.obrigatorio ? "info" : "neutro") + "</td>" +
      "<td>" + esc(c.validacao) + "</td></tr>").join("");

    atualizarAvisoLocal();
  }

  function atualizarAvisoLocal() {
    const alvo = $("#estado-local");
    if (!alvo) return;
    if (!estado.reprocessados.length) {
      alvo.innerHTML = '<span class="nota-pequena">Nenhuma ação local ainda. O botão ' +
        "<em>Reprocessar</em> não faz chamada de rede: ele apenas movimenta o estado desta página " +
        "para você ver o fluxo de uma fila de falha.</span>";
      return;
    }
    alvo.innerHTML = '<div class="botoes"><span class="insignia ok">' + estado.reprocessados.length +
      " documento(s) reprocessado(s) <u>apenas no estado local desta página</u></span>" +
      '<button class="botao" type="button" id="desfazer-local">Desfazer (restaurar dados originais)</button>' +
      '<span class="nota-pequena">Recarregar a página também desfaz, porque nada é enviado a lugar nenhum.</span></div>';
    const desfazer = $("#desfazer-local");
    if (desfazer) desfazer.addEventListener("click", desfazerLocal);
  }

  function reprocessar(documentoId) {
    const d = estado.dados;
    const doc = d.documentos.filter((x) => x.id === documentoId)[0];
    const item = d.pipeline.fila_falhas.filter((f) => f.documento_id === documentoId)[0];
    if (!doc || !item) {
      avisar("Documento não encontrado na fila local.", "alerta");
      return;
    }
    if (!item.reprocessavel) {
      avisar("Classe de erro “" + item.motivo_classe + "” exige análise humana na demo.", "alerta");
      return;
    }
    if (estado.reprocessados.indexOf(documentoId) >= 0) {
      avisar(documentoId + " já foi reprocessado neste estado local.", "info");
      return;
    }

    const agora = new Date().toISOString();
    item.reprocessado = true;
    doc.status = "fila";
    doc.status_rotulo = (d.status_extracao.filter((s) => s.codigo === "fila")[0] || {}).rotulo || "Na fila de execução";
    doc.tentativas = doc.tentativas + 1;
    doc.trilha_auditoria.push({
      sequencia: doc.trilha_auditoria.length + 1,
      instante: agora,
      evento: "reprocessamento_solicitado",
      ator: "botao-reprocessar (estado local da página)",
      resultado: "ok",
      detalhe: "Simulação local: nenhuma chamada de rede foi feita; " +
        "o instante registrado é o relógio do seu navegador."
    });

    const etapa = d.pipeline.etapas.filter((e) => e.codigo === item.etapa)[0];
    if (etapa) {
      etapa.falhas_24h = Math.max(0, etapa.falhas_24h - 1);
      etapa.em_andamento = etapa.em_andamento + 1;
      etapa.fila_espera = etapa.fila_espera + 1;
    }

    estado.reprocessados.push(documentoId);
    estado.documentoSelecionado = documentoId;
    avisar("Reprocessamento simulado: " + documentoId + " voltou para a fila (" +
      (etapa ? etapa.nome : item.etapa) + "). Estado local apenas.", "ok");
    renderExtracao();
    aplicarFiltro();
    trocarVista(estado.vistaAtiva);
  }

  function desfazerLocal() {
    estado.dados = JSON.parse(JSON.stringify(estado.original));
    estado.reprocessados = [];
    estado.documentoSelecionado = null;
    Object.keys(STATUS_POR_CODIGO).forEach((k) => { delete STATUS_POR_CODIGO[k]; });
    Object.keys(EMPRESA_POR_ID).forEach((k) => { delete EMPRESA_POR_ID[k]; });
    Object.keys(SLI_POR_ID).forEach((k) => { delete SLI_POR_ID[k]; });
    mapearIndice();
    renderVisaoGeral();
    renderExtracao();
    renderSLI();
    aplicarFiltro();
    avisar("Estado local restaurado a partir de data/sinteticos.json.", "info");
  }

  /* ------------------------------------------------------------------------
     Vista: Cobertura & SLI
     ------------------------------------------------------------------------ */
  function renderSLI() {
    const d = estado.dados;
    const porSli = {};
    d.error_budget.por_sli.forEach((x) => { porSli[x.sli_id] = x; });

    $("#cartoes-sli").innerHTML = d.slis.map((s) => {
      const b = porSli[s.id] || {};
      const consumido = b.consumido_pct === undefined ? 0 : b.consumido_pct;
      const observado = b.observado_mes_pct !== undefined
        ? numero(b.observado_mes_pct, 2) + "%"
        : (b.p95_mes_seg !== undefined ? numero(b.p95_mes_seg, 2) + " s" : "—");
      return '<article class="cartao kpi tom-' + tomDe(b.ritmo === "saudável" ? "ok" : b.ritmo === "acelerado" ? "alerta" : "erro") + '">' +
        '<div class="rotulo">' + esc(s.nome) + "</div>" +
        '<div class="valor">' + esc(observado) + '<span class="unid">no mês</span></div>' +
        '<div class="fonte">objetivo: <b class="destaque">' + esc(s.objetivo) + "</b> (" + esc(s.janela) + ")</div>" +
        '<div class="delta">orçamento consumido: <b style="color:' +
        (consumido < d.error_budget.janela.limiar_saudavel_pct ? CORES.verde : CORES.ambar) + '">' +
        esc(numero(consumido, 1)) + "%</b> · restante " + esc(numero(b.restante_pct || 0, 1)) + "%</div>" +
        '<div class="fonte">ritmo: ' + insignia(b.ritmo || "—", b.ritmo === "saudável" ? "ok" : b.ritmo === "acelerado" ? "alerta" : "erro") + "</div>" +
        '<div class="fonte">medida: ' + esc(s.medida) + "</div>" +
        '<div class="fonte"><code>' + esc(s.consulta_prometheus) + "</code></div>" +
        "</article>";
    }).join("");

    const principal = d.error_budget.principal;
    $("#medidor-orcamento").innerHTML = medidorAnel(
      principal.consumido_pct,
      "Orçamento de erro — " + (d.slis.filter((s) => s.id === "taxa_erro_worker")[0] || {}).nome,
      "Mês de referência " + d.error_budget.janela.rotulo + " (" + d.error_budget.janela.dias_decorridos +
      " de " + d.error_budget.janela.dias_totais + " dias). Observado no mês: " +
      numero(principal.observado_mes_pct, 3) + "% de extrações com falha definitiva, " +
      "contra objetivo de " + numero(principal.objetivo_pct, 2) + "%. Queima média " +
      numero(principal.queima_media, 3) + "x o ritmo do orçamento."
    ) +
      '<p class="nota-pequena" style="margin-top:10px">Na janela curta de 26 h a taxa observada foi ' +
      "<b>" + esc(numero(principal.observado_janela_26h_pct, 3)) + "%</b> (queima " +
      esc(numero(principal.queima_janela_26h, 2)) + "x) por causa do incidente " +
      '<code>' + esc(d.error_budget.incidente_na_janela) + "</code> às " +
      esc(horaDe(d.error_budget.instante_do_pico)) + ". Fórmula: " + esc(d.error_budget.formula) + "</p>" +
      '<div class="botoes" style="margin-top:6px">' +
      '<a class="botao" href="observabilidade/prometheus-rules.yml">regras PromQL da demo</a>' +
      '<a class="botao" href="observabilidade/alertmanager-routing.yml">roteamento de alertas</a></div>';

    const pico = d.serie_oraria.findIndex((p) => p.instante === d.error_budget.instante_do_pico);
    $("#grafico-burn").innerHTML = graficoSerie(d.burn_rate, [
      { chave: "queima_1h", nome: "janela 1 h", cor: CORES.vermelho, formato: (v) => numero(v, 2) + "x" },
      { chave: "queima_6h", nome: "janela 6 h", cor: CORES.ambar, formato: (v) => numero(v, 2) + "x" },
      { chave: "queima_24h", nome: "janela 24 h", cor: CORES.azul, formato: (v) => numero(v, 2) + "x" }
    ], {
      escalaY: "log",
      limites: [
        { valor: 14.4, rotulo: "14,4x — página", cor: CORES.vermelho },
        { valor: 6, rotulo: "6x — ticket urgente", cor: CORES.ambar },
        { valor: 1, rotulo: "1x — ritmo do orçamento", cor: CORES.suave }
      ],
      pico: { indice: pico, rotulo: d.error_budget.incidente_na_janela },
      formatoY: (v) => (v >= 1 ? numero(v, v < 10 ? 1 : 0) + "x" : numero(v, 2)),
      marcasY: [0.1, 1, 10],
      formatoX: (p) => p.hora_rotulo.slice(0, 5) + " " + horaDe(p.instante)
    }) +
      '<p class="nota-pequena" style="margin-top:8px">' + esc(d.error_budget.janela_26h.nota) + ". " +
      "Escala vertical logarítmica, como em um painel de burn rate clássico: valores acima de 1x " +
      "significam queima mais rápida que o orçamento permite.</p>";

    $("#grafico-cobertura").innerHTML = graficoSerie(d.serie_oraria, [
      { chave: "cobertura_pct", nome: "cobertura por hora", cor: CORES.roxo, formato: (v) => numero(v, 2) + "%" }
    ], {
      minimo: 92, maximo: 100.2, area: true,
      limites: [{ valor: 99, rotulo: "objetivo 99,0%", cor: CORES.vermelho }],
      pico: { indice: pico, rotulo: d.error_budget.incidente_na_janela },
      formatoY: (v) => numero(v, 1) + "%",
      marcasY: [94, 97, 100],
      formatoX: (p) => p.hora_rotulo.slice(0, 5) + " " + horaDe(p.instante)
    }) +
      '<p class="nota-pequena" style="margin-top:8px">Cobertura = documentos validados e publicados ' +
      "÷ documentos esperados pela varredura, por hora. A série tem 26 pontos porque a demonstração " +
      "mostra exatamente o que o gerador produz — nada de janela móvel real.</p>";

    $("#grafico-latencia").innerHTML = graficoSerie(d.serie_oraria, [
      { chave: "latencia_p95_seg", nome: "p95 extração (s)", cor: CORES.azul, formato: (v) => numero(v, 2) + " s" },
      { chave: "latencia_p50_seg", nome: "p50 extração (s)", cor: CORES.verde, formato: (v) => numero(v, 2) + " s" }
    ], {
      limites: [{ valor: 12, rotulo: "objetivo p95 ≤ 12 s", cor: CORES.vermelho }],
      minimo: 0,
      formatoY: (v) => numero(v, 0) + " s",
      pico: { indice: pico, rotulo: d.error_budget.incidente_na_janela },
      formatoX: (p) => p.hora_rotulo.slice(0, 5) + " " + horaDe(p.instante)
    });

    $("#corpo-incidentes").innerHTML = d.incidentes.slice()
      .sort((a, b) => (a.inicio < b.inicio ? 1 : -1))
      .map((i) =>
        "<tr>" +
        '<td><code>' + esc(i.id) + "</code></td>" +
        "<td>" + insignia(i.severidade, i.severidade === "S1" ? "erro" : i.severidade === "S2" ? "alerta" : "neutro") + "</td>" +
        '<td><div class="destaque">' + esc(i.titulo) + '</div><div class="idoc">SLI: ' + esc(i.sli_id) +
        (i.dentro_da_janela_da_serie ? " · visível na série de 26 h" : "") + "</div></td>" +
        '<td class="idoc">' + esc(dataHora(i.inicio)) + "</td>" +
        '<td class="numero">' + esc(minutosTexto(i.mttd_min)) + "</td>" +
        '<td class="numero">' + esc(minutosTexto(i.mttr_min)) + "</td>" +
        "<td>" + esc(i.detectado_por) + "</td>" +
        '<td style="min-width:220px">' + esc(i.causa_sintetica) +
        '<div class="idoc">desfecho: ' + esc(i.desfecho) + "</div></td>" +
        "<td>" + insignia(i.slo_furado ? "orçamento faturado" : "sem faturamento", i.slo_furado ? "alerta" : "ok") + "</td>" +
        "</tr>").join("");

    $("#corpo-cobertura-sli").innerHTML = linhasCobertura(d.cobertura_por_empresa);

    const mttd = d.incidentes.map((i) => i.mttd_min);
    const mttr = d.incidentes.map((i) => i.mttr_min);
    $("#nota-incidentes").innerHTML =
      "Média sintética: MTTD " + esc(minutosTexto(Math.round(mttd.reduce((a, b) => a + b, 0) / mttd.length))) +
      ", MTTR " + esc(minutosTexto(Math.round(mttr.reduce((a, b) => a + b, 0) / mttr.length))) +
      ". Os oito incidentes são ficcionais e servem para mostrar a forma do registro " +
      "(detecção, causa, desfecho, ação corretiva) — não descrevem eventos reais.";
  }

  /* ------------------------------------------------------------------------
     Vista: Sobre
     ------------------------------------------------------------------------ */
  function renderSobre() {
    const d = estado.dados;
    $("#escala-itens").innerHTML = d.escala_ilustrativa.itens.map((i) =>
      "<tr><td>" + esc(i.nome) + '</td><td class="numero"><b>' + esc(i.faixa) + "</b></td>" +
      '<td class="idoc">' + esc(i.nota) + "</td></tr>").join("");
    $("#escala-aviso").textContent = d.escala_ilustrativa.aviso;

    $("#links-autor").innerHTML = [
      ["Perfil no GitHub", d.identidade.autor.github],
      ["Case descrito (repo público)", d.identidade.autor.github + "/fiscal-automation-case"],
      ["Portfólio", d.identidade.autor.portfolio],
      ["LinkedIn", d.identidade.autor.linkedin]
    ].map(([rotulo, url]) =>
      '<a class="botao" href="' + esc(url) + '" rel="noopener noreferrer">' + esc(rotulo) +
      '<span class="idoc">' + esc(url.slice(url.indexOf("//") + 2)) + "</span></a>").join("");

    $("#real-lista").innerHTML = d.aviso.o_que_e_valido.map((x) => "<li>" + esc(x) + "</li>").join("");
    $("#falso-lista").innerHTML = d.aviso.o_que_e_ficticio.map((x) => "<li>" + esc(x) + "</li>").join("");
    $("#como-reproduzir").textContent = d.identidade.como_reproduzir;
    $("#referencias-aut").innerHTML = d.autenticacao.referencias.map((r) =>
      '<a class="botao mini" href="' + esc(r) + '">' + esc(r) + "</a>").join("");
  }

  /* ------------------------------------------------------------------------
     Vista: Correlação (destino do X-Request-Id clicável)
     ------------------------------------------------------------------------ */
  function tracePorRequest(requestId) {
    return estado.dados.tracos.filter((t) => t.request_id === requestId)[0] || null;
  }
  function documentoPorRequest(requestId) {
    return estado.dados.documentos.filter((doc) => doc.request_id === requestId)[0] || null;
  }

  function renderCorrelacao(requestId) {
    const alvo = $("#correlacao-conteudo");
    const trace = tracePorRequest(requestId);
    if (!trace) {
      alvo.innerHTML = '<div class="painel"><div class="corpo">' +
        "<h4>Identificador não encontrado</h4>" +
        '<p class="nota-pequena">O identificador <code>' + esc(requestId) + "</code> não existe na " +
        "amostra sintética desta página. Volte para a <a href=\"#/busca\">Busca fiscal</a> e clique em " +
        "um <code>X-Request-Id</code> de uma linha.</p></div></div>";
      return;
    }
    const doc = documentoPorRequest(trace.request_id);
    const total = Math.max(1, trace.duracao_total_ms);

    const agua = trace.spans.map((s) => {
      const esquerda = (s.inicio_ms / total) * 100;
      const largura = Math.max(0.6, (s.duracao_ms / total) * 100);
      const recuo = "margin-left:" + (s.profundidade * 12) + "px";
      return '<div class="linha-agua">' +
        '<div class="nome" style="' + recuo + '" title="' + esc(s.servico + " — " + s.nome) + '">' +
        esc(s.servico) + " · " + esc(s.nome) + "</div>" +
        '<div class="barra"><i class="' + (s.status === "erro" ? "erro" : "") + '" style="left:' +
        esquerda.toFixed(2) + "%;width:" + largura.toFixed(2) + '%"></i></div>' +
        '<div class="duracao">' + esc(numero(s.duracao_ms)) + " ms</div>" +
        "</div>";
    }).join("");

    const logs = trace.logs.map((l) =>
      "<tr>" +
      '<td class="idoc">' + esc(dataHora(l.instante)) + "</td>" +
      '<td class="nivel-' + esc(l.nivel) + '">' + esc(l.nivel) + "</td>" +
      "<td>" + esc(l.servico) + "</td>" +
      "<td>" + esc(l.mensagem) + "</td>" +
      '<td class="idoc">' + esc(l.span_id) + "</td>" +
      "</tr>").join("");

    const logql = [
      '{namespace="automacao-demo"} | trace_id="' + trace.trace_id + '"',
      '{service_name="worker-extracao"} | request_id="' + trace.request_id + '" |~ "(?i)(error|warn|falha)"',
      '{namespace="automacao-demo"} | json | trace_id="' + trace.trace_id + '" | line_format "{{.mensagem}}"'
    ];
    const promql = [
      'sum(rate(extracoes_total{empresa_id="' + trace.empresa_id + '",resultado="falha_definitiva"}[30m])) ' +
      '/ sum(rate(extracoes_total{empresa_id="' + trace.empresa_id + '"}[30m]))',
      'histogram_quantile(0.95, sum by (le) (rate(extracao_duracao_seconds_bucket{service_name="worker-extracao"}[10m])))',
      'sum by (trace_id) (traces_of_spe_span_duration_milliseconds_count{trace_id="' + trace.trace_id + '"})'
    ];

    alvo.innerHTML =
      '<div class="painel"><header>' +
      "<h4>Correlação de request, trace e logs</h4>" +
      '<span class="nota">dados sintéticos · nenhuma telemetria real</span></header>' +
      '<div class="corpo">' +
      '<div class="cabecalho-correlacao">' +
      '<span class="identificador"><span>X-Request-Id</span><code>' + esc(trace.request_id) + "</code>" +
      '<button class="botao mini" type="button" data-copiar="' + esc(trace.request_id) + '">copiar</button></span>' +
      '<span class="identificador"><span>trace_id</span><code>' + esc(trace.trace_id) + "</code>" +
      '<button class="botao mini" type="button" data-copiar="' + esc(trace.trace_id) + '">copiar</button></span>' +
      '<span class="identificador"><span>span raiz</span><code>' + esc(trace.span_raiz) + "</code></span>" +
      '<span class="identificador"><span>duração total</span><code>' + esc(numero(total)) + " ms</code></span>" +
      "</div>" +
      (doc ? '<p class="nota-pequena" style="margin-top:10px">Documento associado: ' +
        '<code>' + esc(doc.id) + "</code> (" + esc(doc.tipo_rotulo) + " " + esc(doc.numero_documento_demo) +
        ") · empresa " + esc(doc.empresa_id) + " · status <b>" + esc(doc.status_rotulo) + "</b> · " +
        '<a href="#" id="corr-voltar">voltar de onde você veio</a></p>' : "") +
      '<p class="nota-pequena">' + esc(trace.amostragem) + ". Serviço de entrada: <code>" +
      esc(trace.servico_origem) + "</code>. Em produção o mesmo identificador trafega no cabeçalho " +
      "<code>traceparent</code> (W3C Trace Context) e é anexado a cada log em JSON pelo OpenTelemetry " +
      "Collector; aqui ele é gerado pelo script de dados sintéticos.</p>" +
      "</div></div>" +

      '<div class="grade g-2" style="margin-top:14px">' +
      '<div class="painel"><header><h4>Waterfall de spans (OTel)</h4>' +
      '<span class="nota">' + trace.spans.length + " spans</span></header>" +
      '<div class="corpo"><div class="agua">' + agua + "</div>" +
      '<p class="nota-pequena" style="margin-top:10px">Larguras proporcionais à duração sintética de cada ' +
      "span; barras vermelhas indicam o span com status de erro, quando o documento está em falha.</p></div></div>" +

      '<div class="painel"><header><h4>Logs correlatos (padrão Loki)</h4>' +
      '<span class="nota">' + trace.logs.length + " linhas</span></header>" +
      '<div class="corpo sem-padding"><div class="envolver-tabela"><table class="logs">' +
      "<thead><tr><th>instante</th><th>nível</th><th>serviço</th><th>mensagem</th><th>span_id</th></tr></thead>" +
      "<tbody>" + logs + "</tbody></table></div></div></div>" +
      "</div>" +

      '<div class="grade g-2" style="margin-top:14px">' +
      '<div class="painel"><header><h4>LogQL equivalente</h4><span class="nota">para um Loki real</span></header>' +
      '<div class="corpo">' + logql.map((q) => '<div class="codigo">' + esc(q) + "</div>").join("") +
      '<p class="nota-pequena" style="margin-top:8px"><a href="observabilidade/loki-logql-exemplos.md">' +
      "todas as consultas de exemplo</a></p></div></div>" +
      '<div class="painel"><header><h4>PromQL correlato</h4><span class="nota">para um Prometheus real</span></header>' +
      '<div class="corpo">' + promql.map((q) => '<div class="codigo">' + esc(q) + "</div>").join("") +
      '<p class="nota-pequena" style="margin-top:8px"><a href="observabilidade/prometheus-rules.yml">' +
      "regras de SLI e burn rate</a> · <a href=\"observabilidade/dashboards/grafana-demo.json\">dashboard</a></p></div></div>" +
      "</div>";

    $$("[data-copiar]", alvo).forEach((b) =>
      b.addEventListener("click", () => copiar(b.getAttribute("data-copiar"))));
    if (doc) {
      const voltar = alvo.querySelector("#corr-voltar");
      if (voltar) {
        voltar.setAttribute("href", estado.rotaAnterior || "#/busca");
        voltar.addEventListener("click", () => {
          estado.documentoSelecionado = doc.id;
          estado.filtros = { empresa: "", tipo: "", status: "", mesIni: "", mesFim: "", texto: doc.id };
          $$("[data-filtro]", $("#filtro-form")).forEach((c) => {
            c.value = c.getAttribute("data-filtro") === "texto" ? doc.id : "";
          });
        });
      }
    }
  }

  /* ------------------------------------------------------------------------
     Micro-interações: avisos e área de transferência
     ------------------------------------------------------------------------ */
  let filaToast = null;
  function avisar(mensagem, tom) {
    const caixa = $("#toasts");
    const item = document.createElement("div");
    item.className = tom === "ok" || tom === "erro" || tom === "alerta" ? tom : "";
    item.setAttribute("role", "status");
    item.textContent = mensagem;
    caixa.appendChild(item);
    window.setTimeout(() => { item.remove(); }, 5200);
    while (caixa.children.length > 3) { caixa.firstChild.remove(); }
    void filaToast;
  }

  function copiar(texto) {
    const terminar = (sucesso) => avisar(sucesso
      ? "Copiado para a área de transferência: " + texto
      : "Seu navegador bloqueou a cópia; o valor é " + texto, sucesso ? "ok" : "alerta");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(texto).then(() => terminar(true), () => terminar(false));
      return;
    }
    try {
      const campo = document.createElement("textarea");
      campo.value = texto;
      campo.setAttribute("readonly", "readonly");
      campo.style.position = "fixed";
      campo.style.left = "-9999px";
      document.body.appendChild(campo);
      campo.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(campo);
      terminar(ok);
    } catch (erro) {
      terminar(false);
      void erro;
    }
  }

  /* ------------------------------------------------------------------------
     Índices auxiliares e inicialização
     ------------------------------------------------------------------------ */
  function mapearIndice() {
    estado.dados.status_extracao.forEach((s) => { STATUS_POR_CODIGO[s.codigo] = s; });
    estado.dados.empresas.forEach((e) => { EMPRESA_POR_ID[e.id] = e; });
    estado.dados.slis.forEach((s) => { SLI_POR_ID[s.id] = s; });
  }

  function ligarNavegacao() {
    $$(".aba").forEach((aba) => {
      aba.addEventListener("click", () => {
        const rota = aba.getAttribute("data-rota");
        location.hash = "#/" + rota;
      });
    });
    window.addEventListener("hashchange", renderizar);
  }

  function renderizar() {
    if (!estado.dados) return;
    const rota = lerRota();
    if (!location.hash) { location.replace("#/visao-geral"); }
    trocarVista(rota.vista);
    try {
      if (rota.vista === "visao-geral") { renderVisaoGeral(); }
      else if (rota.vista === "busca") { aplicarFiltro(); }
      else if (rota.vista === "extracao") { renderExtracao(); }
      else if (rota.vista === "sli") { renderSLI(); }
      else if (rota.vista === "sobre") { renderSobre(); }
      else if (rota.vista === "correlacao") { renderCorrelacao(rota.param); }
      $("#erro-carga").hidden = true;
    } catch (erro) {
      $("#erro-carga").hidden = false;
      $("#erro-carga").innerHTML = '<strong>Falha ao montar a vista "' + esc(rota.vista) + '".</strong> ' +
        esc(erro && erro.message ? erro.message : String(erro)) +
        ' — abra o console do navegador para o rastro completo.';
      throw erro;
    }
    document.title = tituloVista(rota.vista) + " — automação fiscal (demonstração)";
    const h1 = $(".titulo-vista h2", $("#vista-" + rota.vista));
    if (h1) { h1.setAttribute("tabindex", "-1"); }
  }

  function tituloVista(vista) {
    const mapa = {
      "visao-geral": "Visão geral", busca: "Busca fiscal", extracao: "Extração & workflow",
      sli: "Cobertura & SLI", arquitetura: "Arquitetura", sobre: "Sobre o case",
      correlacao: "Correlação"
    };
    return mapa[vista] || "Visão geral";
  }

  async function iniciar() {
    const carga = await carregarDados();
    estado.dados = carga.dados;
    estado.origemCarga = carga.origem;
    if (!estado.dados) {
      $("#erro-carga").hidden = false;
      $("#erro-carga").innerHTML = '<strong>Não foi possível carregar os dados da demonstração.</strong> ' +
        "Esperava <code>data/sinteticos.json</code> (fetch) ou <code>window.DEMO_DATA</code> de " +
        "<code>data/dados.js</code>. Se você abriu <code>index.html</code> por <code>file://</code>, " +
        "o fallback deveria ter funcionado: verifique se os dois arquivos foram copiados juntos.";
      return;
    }
    estado.original = JSON.parse(JSON.stringify(estado.dados));
    mapearIndice();
    cabecalhoDinamico();
    montarFiltros();
    ligarNavegacao();
    renderVisaoGeral();
    renderSLI();
    renderSobre();
    renderizar();
    $("#carregando").hidden = true;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }
})();
