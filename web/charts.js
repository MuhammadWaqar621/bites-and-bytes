/* ==========================================================================
   Chart rendering — inline SVG, no library.

   A tool result may carry a `charts` array; each entry is drawn under the
   assistant's reply. Three forms, chosen by what the data has to do:

     kpi   a handful of headline numbers  → stat tiles, not a one-bar chart
     line  a measure over consecutive periods → area + line, single series
     bar   ranked magnitudes → horizontal bars (dish names are long)

   Colour follows the MEASURE, not the chart: spend is always blue, order
   counts always orange. Both were validated against this app's dark panel
   surface for lightness band, chroma, CVD separation and contrast.

   Two rules this file will not bend:
     · one y-axis per chart. Spend and order counts are different units, so
       `get_order_trend` returns them as two charts rather than one with twin
       axes — the most misread chart there is.
     · every chart ships a table view. Colour and geometry are never the only
       way to read the numbers.
   ========================================================================== */

const MEASURES = {
  //                    mark       soft fill for the area under a line
  spend:  { color: "#3987e5", fill: "rgba(57, 135, 229, 0.16)" },
  orders: { color: "#d95926", fill: "rgba(217, 89, 38, 0.16)" },
};

const AXIS = "rgba(255,255,255,0.10)";   // recessive grid
const INK = "#8b93a8";                    // axis labels — text token, not series colour

/** Render every chart in a tool trace, appended to `host`. */
function renderCharts(host, trace) {
  const specs = (trace || []).flatMap((call) => call.result?.charts || []);
  specs.forEach((spec) => host.appendChild(buildChart(spec)));
  return specs.length;
}

function buildChart(spec) {
  const figure = document.createElement("figure");
  figure.className = "chart";

  const measure = MEASURES[spec.measure] || MEASURES.spend;
  const currency = spec.currency || "";

  const head = document.createElement("figcaption");
  head.className = "chart-head";
  head.innerHTML = `<span class="chart-title">${escape(spec.title)}</span>`;

  // The table view is the accessibility fallback, so it is a real control on
  // every chart rather than a debug affordance.
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "chart-toggle";
  toggle.textContent = "Table";
  head.appendChild(toggle);
  figure.appendChild(head);

  const body = document.createElement("div");
  body.className = "chart-body";
  if (spec.kind === "kpi") body.appendChild(kpiTiles(spec, currency));
  else if (spec.kind === "bar") body.appendChild(barChart(spec, measure, currency));
  else body.appendChild(lineChart(spec, measure, currency));
  figure.appendChild(body);

  const table = dataTable(spec, currency);
  table.hidden = true;
  figure.appendChild(table);

  toggle.addEventListener("click", () => {
    const showing = table.hidden;
    table.hidden = !showing;
    body.hidden = showing;
    toggle.textContent = showing ? "Chart" : "Table";
  });

  return figure;
}

/* ── KPI tiles ────────────────────────────────────────────────────────────
   Four numbers are not a chart. A stat row is read faster than any bar. */

function kpiTiles(spec, currency) {
  const row = document.createElement("div");
  row.className = "kpi-row";
  row.innerHTML = (spec.tiles || []).map((tile) => `
    <div class="kpi">
      <div class="kpi-value">${tile.format === "money"
        ? escape(currency) + formatNumber(tile.value)
        : formatNumber(tile.value)}</div>
      <div class="kpi-label">${escape(tile.label)}</div>
    </div>`).join("");
  return row;
}

/* ── Line / area ─────────────────────────────────────────────────────────── */

function lineChart(spec, measure, currency) {
  const points = spec.points || [];
  if (points.length < 2) return emptyNote("Not enough periods to draw a trend.");

  const W = 520, H = 190;
  const pad = { top: 16, right: 16, bottom: 30, left: 52 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  // Always include zero: a line chart of money that starts the axis at the
  // minimum exaggerates every wiggle into a cliff.
  const max = Math.max(...points.map((p) => p.value), 0);
  const ceiling = niceCeiling(max);
  const x = (i) => pad.left + (plotW * i) / (points.length - 1);
  const y = (v) => pad.top + plotH - (ceiling ? (v / ceiling) * plotH : 0);

  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const area = `${line} L${x(points.length - 1).toFixed(1)},${(pad.top + plotH).toFixed(1)} L${x(0).toFixed(1)},${(pad.top + plotH).toFixed(1)} Z`;

  const ticks = [0, ceiling / 2, ceiling];
  const money = spec.measure === "spend";

  // Label the last point only. A number on every point is noise.
  const lastIndex = points.length - 1;
  const svg = `
    <svg viewBox="0 0 ${W} ${H}" class="chart-svg" role="img"
         aria-label="${escape(spec.title)}">
      ${ticks.map((t) => `
        <line x1="${pad.left}" x2="${W - pad.right}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}"
              stroke="${AXIS}" stroke-width="1" />
        <text x="${pad.left - 8}" y="${(y(t) + 3.5).toFixed(1)}" text-anchor="end"
              fill="${INK}" font-size="10">${money ? currency : ""}${compact(t)}</text>`).join("")}

      <path d="${area}" fill="${measure.fill}" />
      <path d="${line}" fill="none" stroke="${measure.color}" stroke-width="2"
            stroke-linejoin="round" stroke-linecap="round" />

      ${points.map((p, i) => `
        <circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="4"
                fill="${measure.color}" stroke="#0f1219" stroke-width="2"
                class="chart-dot" data-i="${i}" />`).join("")}

      <text x="${x(lastIndex).toFixed(1)}" y="${(y(points[lastIndex].value) - 12).toFixed(1)}"
            text-anchor="end" fill="#e9ecf4" font-size="11" font-weight="600">
        ${money ? escape(currency) : ""}${formatNumber(points[lastIndex].value)}
      </text>

      ${points.map((p, i) => (shouldLabel(i, points.length)
        ? `<text x="${x(i).toFixed(1)}" y="${H - 10}" text-anchor="middle"
                 fill="${INK}" font-size="10">${escape(p.label)}</text>` : "")).join("")}
    </svg>`;

  return withHover(svg, points, spec, currency);
}

/* ── Horizontal bars ─────────────────────────────────────────────────────── */

function barChart(spec, measure, currency) {
  const points = spec.points || [];
  if (!points.length) return emptyNote("Nothing to break down yet.");

  const rowH = 26, gap = 6;                     // gap = the 2px+ surface spacer
  const W = 520, labelW = 150, valueW = 70;
  const H = points.length * (rowH + gap);
  const plotW = W - labelW - valueW;
  const max = Math.max(...points.map((p) => p.value), 0) || 1;
  const money = spec.measure === "spend";

  const svg = `
    <svg viewBox="0 0 ${W} ${H}" class="chart-svg" role="img"
         aria-label="${escape(spec.title)}">
      ${points.map((p, i) => {
        const y = i * (rowH + gap);
        const w = Math.max((p.value / max) * plotW, 2);
        return `
          <text x="${labelW - 10}" y="${y + rowH / 2 + 4}" text-anchor="end"
                fill="${INK}" font-size="11">${escape(truncate(p.label, 22))}</text>
          <rect x="${labelW}" y="${y + 3}" width="${w.toFixed(1)}" height="${rowH - 6}"
                rx="4" fill="${measure.color}" class="chart-bar" data-i="${i}" />
          <text x="${labelW + w + 8}" y="${y + rowH / 2 + 4}"
                fill="#e9ecf4" font-size="11" font-weight="600">
            ${money ? escape(currency) : ""}${compact(p.value)}
          </text>`;
      }).join("")}
    </svg>`;

  return withHover(svg, points, spec, currency);
}

/* ── Hover layer ─────────────────────────────────────────────────────────── */

function withHover(svgMarkup, points, spec, currency) {
  const wrap = document.createElement("div");
  wrap.className = "chart-plot";
  wrap.innerHTML = svgMarkup;

  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  wrap.appendChild(tip);

  const money = spec.measure === "spend";
  wrap.querySelectorAll(".chart-dot, .chart-bar").forEach((mark) => {
    // Hit targets are the marks themselves plus generous CSS padding via
    // pointer-events on the group; small dots would otherwise be unhittable.
    mark.addEventListener("mouseenter", () => {
      const point = points[Number(mark.dataset.i)];
      tip.innerHTML = `<strong>${escape(point.label)}</strong>${
        money ? escape(currency) : ""}${formatNumber(point.value)}`;
      tip.hidden = false;
      const box = mark.getBoundingClientRect();
      const host = wrap.getBoundingClientRect();
      tip.style.left = `${box.left - host.left + box.width / 2}px`;
      tip.style.top = `${box.top - host.top - 10}px`;
    });
    mark.addEventListener("mouseleave", () => { tip.hidden = true; });
  });

  return wrap;
}

/* ── Table view ──────────────────────────────────────────────────────────── */

function dataTable(spec, currency) {
  const wrap = document.createElement("div");
  wrap.className = "chart-table";

  const rows = spec.kind === "kpi"
    ? (spec.tiles || []).map((t) => [t.label, t.format === "money"
        ? currency + formatNumber(t.value) : formatNumber(t.value)])
    : (spec.points || []).map((p) => [p.label, (spec.measure === "spend" ? currency : "")
        + formatNumber(p.value)]);

  wrap.innerHTML = `
    <table>
      <tbody>
        ${rows.map(([label, value]) => `
          <tr><th scope="row">${escape(label)}</th><td>${escape(value)}</td></tr>`).join("")}
      </tbody>
    </table>`;
  return wrap;
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function emptyNote(text) {
  const node = document.createElement("p");
  node.className = "chart-empty";
  node.textContent = text;
  return node;
}

/** Round the axis top up to something a human would choose. */
function niceCeiling(max) {
  if (max <= 0) return 0;
  const magnitude = 10 ** Math.floor(Math.log10(max));
  return Math.ceil(max / magnitude) * magnitude;
}

/** Thin x labels so they never collide on a narrow chart. */
function shouldLabel(index, total) {
  if (total <= 8) return true;
  const step = Math.ceil(total / 6);
  return index % step === 0 || index === total - 1;
}

function formatNumber(value) {
  const number = Number(value) || 0;
  return Number.isInteger(number)
    ? number.toLocaleString()
    : number.toLocaleString(undefined, { minimumFractionDigits: 2,
                                         maximumFractionDigits: 2 });
}

function compact(value) {
  const number = Number(value) || 0;
  if (number >= 100000) return `${(number / 100000).toFixed(1)}L`;   // lakh
  if (number >= 1000) return `${(number / 1000).toFixed(1)}k`;
  return Number.isInteger(number) ? String(number) : number.toFixed(0);
}

function truncate(text, max) {
  const value = String(text ?? "");
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

function escape(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

window.renderCharts = renderCharts;
