const colors = [
  "#E95420", "#0E8420", "#0066CC", "#772953", "#AEA79F",
  "#333333", "#007AA6", "#C7162B", "#F99B11", "#38B44A",
  "#5E2750", "#77216F", "#335280",
];

const response = await fetch("/stats/trends/all-data");
const { projects, order } = await response.json();

function createCheckboxItem(container, { id, label, checked, onChange, color }) {
  const labelEl = document.createElement("label");
  labelEl.style.cssText = "display:flex;align-items:center;gap:0.4rem;cursor:pointer;margin-bottom:0.3rem;";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.id = id;
  cb.checked = checked;
  cb.addEventListener("change", () => onChange(cb.checked));
  labelEl.appendChild(cb);
  if (color) {
    const swatch = document.createElement("span");
    swatch.style.cssText = `display:inline-block;width:12px;height:12px;flex-shrink:0;background:${color};border:1px solid #666;`;
    labelEl.appendChild(swatch);
  }
  labelEl.appendChild(document.createTextNode(label));
  container.appendChild(labelEl);
}

function createLineChart(canvasId, yLabel) {
  return new Chart(document.getElementById(canvasId), {
    type: "line",
    data: { labels: [], datasets: [] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      elements: { point: { radius: 0 } },
      plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false } },
      scales: {
        x: { display: true, title: { display: true, text: "Date" } },
        y: { display: true, beginAtZero: true, title: { display: true, text: yLabel }, ticks: { precision: 0 } },
      },
      interaction: { mode: "nearest", axis: "x", intersect: false },
    },
  });
}

function populateCheckboxes(containerId, prefix, onChange) {
  const container = document.getElementById(containerId);
  order.forEach((name, i) => {
    createCheckboxItem(container, {
      id: `${prefix}-${name}`,
      label: name,
      checked: i === 0,
      onChange: () => onChange(),
      color: colors[i % colors.length],
    });
  });
}

function updateChart(chart, checkboxPrefix, dataKey) {
  const selected = order.filter(name => {
    const cb = document.getElementById(`${checkboxPrefix}-${name}`);
    return cb?.checked;
  });
  if (!selected.length) {
    chart.data.labels = [];
    chart.data.datasets = [];
    chart.update();
    return;
  }
  chart.data.labels = projects[selected[0]].dates;
  chart.data.datasets = selected.map((name) => ({
    label: name,
    data: projects[name][dataKey],
    borderColor: colors[order.indexOf(name) % colors.length],
    backgroundColor: colors[order.indexOf(name) % colors.length] + "20",
    borderWidth: 2,
    fill: false,
    tension: 0.1,
  }));
  chart.update();
}

const issuesChart = createLineChart("issues-chart", "Open Issues");
const prsChart = createLineChart("prs-chart", "Open PRs");
const bugsChart = createLineChart("bugs-chart", "Open Bugs");

populateCheckboxes("issues-checkboxes", "iss", () => updateChart(issuesChart, "iss", "open_issues"));
populateCheckboxes("prs-checkboxes", "pr", () => updateChart(prsChart, "pr", "open_prs"));
populateCheckboxes("bugs-checkboxes", "bug", () => updateChart(bugsChart, "bug", "open_bugs"));

updateChart(issuesChart, "iss", "open_issues");
updateChart(prsChart, "pr", "open_prs");
updateChart(bugsChart, "bug", "open_bugs");
