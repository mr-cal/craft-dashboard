// Renders a screen-reader-accessible data table mirroring a Chart.js chart.
//
// A <canvas> exposes nothing but its aria-label, so the values a chart plots
// are unreachable without an equivalent table. The table is visually hidden
// but present in the accessibility tree, and is rebuilt whenever the chart's
// data changes (filters, date ranges, category selection).

// Chart.js plugin: keeps every registered chart's data table in sync.
// Registered once in chart-common.js so each page gets accessible charts
// without having to remember to call the renderer after every update.
export const chartDataTablePlugin = {
  id: "accessibleDataTable",
  afterUpdate(chart) {
    const canvas = chart.canvas;
    if (!canvas || !canvas.parentElement) return;
    const caption =
      canvas.getAttribute("aria-label") ||
      chart.options?.plugins?.title?.text ||
      "Chart data";
    renderChartDataTable(chart, canvas.parentElement, caption);
  },
};

export function renderChartDataTable(chart, wrapper, caption) {
  if (!chart || !wrapper) return;

  let table = wrapper.querySelector("table.chart-data-table");
  if (!table) {
    table = document.createElement("table");
    table.className = "chart-data-table u-visually-hidden";
    wrapper.appendChild(table);

    const canvas = wrapper.querySelector("canvas");
    if (canvas) {
      if (!table.id) {
        table.id = `${canvas.id || "chart"}-data-table`;
      }
      canvas.setAttribute("aria-describedby", table.id);
    }
  }

  const labels = chart.data?.labels ?? [];
  const datasets = chart.data?.datasets ?? [];

  const fragment = document.createDocumentFragment();

  const captionEl = document.createElement("caption");
  captionEl.textContent = caption;
  fragment.appendChild(captionEl);

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  headRow.appendChild(_cell("th", "", { scope: "col" }));
  datasets.forEach((dataset) => {
    headRow.appendChild(_cell("th", dataset.label ?? "", { scope: "col" }));
  });
  thead.appendChild(headRow);
  fragment.appendChild(thead);

  const tbody = document.createElement("tbody");
  labels.forEach((label, index) => {
    const row = document.createElement("tr");
    row.appendChild(_cell("th", String(label), { scope: "row" }));
    datasets.forEach((dataset) => {
      const value = dataset.data?.[index];
      row.appendChild(_cell("td", value === null || value === undefined ? "no data" : String(value)));
    });
    tbody.appendChild(row);
  });
  fragment.appendChild(tbody);

  table.replaceChildren(fragment);
}

function _cell(tag, text, attributes = {}) {
  const el = document.createElement(tag);
  el.textContent = text;
  Object.entries(attributes).forEach(([key, value]) => el.setAttribute(key, value));
  return el;
}
