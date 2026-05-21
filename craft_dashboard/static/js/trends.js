// Color palette for projects
const colors = [
  "#E95420", "#0E8420", "#0066CC", "#772953", "#AEA79F",
  "#333333", "#007AA6", "#C7162B", "#F99B11", "#38B44A",
  "#5E2750", "#77216F", "#335280",
];

// Fetch data
const response = await fetch("/stats/trends/all-data");
const { projects, order, snapshot } = await response.json();

// View state: "all" or "external"
let currentView = "all";

// ============================================================================
// Utility functions
// ============================================================================

function rollingAverage(data, windowSize) {
  const result = [];
  for (let i = 0; i < data.length; i++) {
    const start = Math.max(0, i - windowSize + 1);
    const window = data.slice(start, i + 1);
    const avg = window.reduce((sum, val) => sum + val, 0) / window.length;
    result.push(avg);
  }
  return result;
}

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
      plugins: { 
        legend: { display: false }, 
        tooltip: { mode: "index", intersect: false } 
      },
      scales: {
        x: { display: true, title: { display: true, text: "Date" } },
        y: { 
          display: true, 
          beginAtZero: true, 
          title: { display: true, text: yLabel }, 
          ticks: { precision: 0 } 
        },
      },
      interaction: { mode: "nearest", axis: "x", intersect: false },
    },
  });
}

function createBarChart(canvasId, xLabel) {
  return new Chart(document.getElementById(canvasId), {
    type: "bar",
    data: { labels: [], datasets: [] },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { 
        legend: { display: true, position: "bottom" },
        tooltip: { mode: "index", intersect: false } 
      },
      scales: {
        x: { 
          display: true, 
          beginAtZero: true, 
          title: { display: true, text: xLabel },
          ticks: { precision: 0 } 
        },
        y: { display: true },
      },
    },
  });
}

// ============================================================================
// Line chart updates
// ============================================================================

function updateOpenIssuesChart() {
  const selected = order.filter(name => {
    const cb = document.getElementById(`open-issues-${name}`);
    return cb?.checked;
  });
  
  // Check if all-projects is selected
  const allProjectsCb = document.getElementById("open-issues-all-projects");
  if (allProjectsCb?.checked) {
    selected.push("all-projects");
  }
  
  if (!selected.length) {
    issuesChart.data.labels = [];
    issuesChart.data.datasets = [];
    issuesChart.update();
    return;
  }
  
  const firstProject = selected[0];
  issuesChart.data.labels = projects[firstProject].dates;
  
  const dataKey = currentView === "all" ? "open_issues" : "open_issues_external";
  
  issuesChart.data.datasets = selected.map((name) => {
    const rawData = projects[name][dataKey];
    const smoothedData = rollingAverage(rawData, 28); // 4-week rolling average
    const colorIdx = name === "all-projects" ? 0 : order.indexOf(name);
    const color = colors[colorIdx % colors.length];
    
    return {
      label: name,
      data: smoothedData,
      borderColor: color,
      backgroundColor: color + "20",
      borderWidth: 2,
      fill: false,
      tension: 0.1,
    };
  });
  
  issuesChart.update();
}

function updateMedianAgeChart() {
  const selected = order.filter(name => {
    const cb = document.getElementById(`median-age-${name}`);
    return cb?.checked;
  });
  
  const allProjectsCb = document.getElementById("median-age-all-projects");
  if (allProjectsCb?.checked) {
    selected.push("all-projects");
  }
  
  if (!selected.length) {
    medianAgeChart.data.labels = [];
    medianAgeChart.data.datasets = [];
    medianAgeChart.update();
    return;
  }
  
  const firstProject = selected[0];
  medianAgeChart.data.labels = projects[firstProject].dates;
  
  const dataKey = "median_issue_age"; // We don't have separate external median age yet
  
  medianAgeChart.data.datasets = selected.map((name) => {
    const rawData = projects[name][dataKey];
    const smoothedData = rollingAverage(rawData, 28); // 4-week rolling average
    const colorIdx = name === "all-projects" ? 0 : order.indexOf(name);
    const color = colors[colorIdx % colors.length];
    
    return {
      label: name,
      data: smoothedData,
      borderColor: color,
      backgroundColor: color + "20",
      borderWidth: 2,
      fill: false,
      tension: 0.1,
    };
  });
  
  medianAgeChart.update();
}

function updateClosedChart() {
  const selected = order.filter(name => {
    const cb = document.getElementById(`closed-${name}`);
    return cb?.checked;
  });
  
  const allProjectsCb = document.getElementById("closed-all-projects");
  if (allProjectsCb?.checked) {
    selected.push("all-projects");
  }
  
  if (!selected.length) {
    closedChart.data.labels = [];
    closedChart.data.datasets = [];
    closedChart.update();
    return;
  }
  
  const firstProject = selected[0];
  closedChart.data.labels = projects[firstProject].dates;
  
  const dataKey = currentView === "all" ? "closed_issues" : "closed_issues_external";
  
  closedChart.data.datasets = selected.map((name) => {
    const rawData = projects[name][dataKey].map(v => v * 7); // Scale to per-week
    const smoothedData = rollingAverage(rawData, 30); // 30-day rolling average
    const colorIdx = name === "all-projects" ? 0 : order.indexOf(name);
    const color = colors[colorIdx % colors.length];
    
    return {
      label: name,
      data: smoothedData,
      borderColor: color,
      backgroundColor: color + "20",
      borderWidth: 2,
      fill: false,
      tension: 0.1,
    };
  });
  
  closedChart.update();
}

// ============================================================================
// Bar chart updates
// ============================================================================

function updateSnapshotCharts() {
  const selected = order.filter(name => {
    const cb = document.getElementById(`snapshot-${name}`);
    return cb?.checked;
  });
  
  const allProjectsCb = document.getElementById("snapshot-all-projects");
  if (allProjectsCb?.checked) {
    selected.unshift("all-projects"); // Put all-projects first
  }
  
  if (!selected.length) {
    snapshotOpenChart.data.labels = [];
    snapshotOpenChart.data.datasets = [];
    snapshotAgeChart.data.labels = [];
    snapshotAgeChart.data.datasets = [];
    snapshotClosedChart.data.labels = [];
    snapshotClosedChart.data.datasets = [];
    
    snapshotOpenChart.update();
    snapshotAgeChart.update();
    snapshotClosedChart.update();
    return;
  }
  
  const labels = selected;
  
  // Open Issues/PRs Chart
  const issueKey = currentView === "all" ? "open_issues" : "nm_open_issues";
  const prKey = currentView === "all" ? "open_prs" : "nm_open_prs";
  
  snapshotOpenChart.data.labels = labels;
  snapshotOpenChart.data.datasets = [
    {
      label: "Issues",
      data: selected.map(name => snapshot[name][issueKey]),
      backgroundColor: "#0066CC",
      borderColor: "#0066CC",
      borderWidth: 1,
    },
    {
      label: "PRs",
      data: selected.map(name => snapshot[name][prKey]),
      backgroundColor: "#E95420",
      borderColor: "#E95420",
      borderWidth: 1,
    },
  ];
  
  // Median Age Chart
  const issueAgeKey = currentView === "all" ? "median_issue_age" : "nm_median_issue_age";
  const prAgeKey = currentView === "all" ? "median_pr_age" : "nm_median_pr_age";
  
  snapshotAgeChart.data.labels = labels;
  snapshotAgeChart.data.datasets = [
    {
      label: "Issue Age",
      data: selected.map(name => snapshot[name][issueAgeKey]),
      backgroundColor: "#0066CC",
      borderColor: "#0066CC",
      borderWidth: 1,
    },
    {
      label: "PR Age",
      data: selected.map(name => snapshot[name][prAgeKey]),
      backgroundColor: "#E95420",
      borderColor: "#E95420",
      borderWidth: 1,
    },
  ];
  
  // Closed Last Year Chart
  const closedIssueKey = currentView === "all" ? "closed_issues_year" : "nm_closed_issues_year";
  const closedPrKey = currentView === "all" ? "closed_prs_year" : "nm_closed_prs_year";
  
  snapshotClosedChart.data.labels = labels;
  snapshotClosedChart.data.datasets = [
    {
      label: "Issues",
      data: selected.map(name => snapshot[name][closedIssueKey]),
      backgroundColor: "#0066CC",
      borderColor: "#0066CC",
      borderWidth: 1,
    },
    {
      label: "PRs",
      data: selected.map(name => snapshot[name][closedPrKey]),
      backgroundColor: "#E95420",
      borderColor: "#E95420",
      borderWidth: 1,
    },
  ];
  
  // Adjust chart height based on number of bars
  const height = Math.max(200, selected.length * 60);
  snapshotOpenChart.canvas.parentElement.style.height = height + "px";
  snapshotAgeChart.canvas.parentElement.style.height = height + "px";
  snapshotClosedChart.canvas.parentElement.style.height = height + "px";
  
  snapshotOpenChart.update();
  snapshotAgeChart.update();
  snapshotClosedChart.update();
}

// ============================================================================
// View toggle
// ============================================================================

function updateView(view) {
  currentView = view;
  document.getElementById("view-all").checked = view === "all";
  document.getElementById("view-external").checked = view === "external";
  
  updateOpenIssuesChart();
  updateClosedChart();
  updateSnapshotCharts();
}

// ============================================================================
// Populate checkboxes
// ============================================================================

function populateLineChartCheckboxes(containerId, prefix, onChange) {
  const container = document.getElementById(containerId);
  
  // Add all-projects checkbox first
  createCheckboxItem(container, {
    id: `${prefix}-all-projects`,
    label: "all-projects",
    checked: prefix === "open-issues", // Check by default for first chart
    onChange: () => onChange(),
    color: colors[0],
  });
  
  // Add individual projects
  order.forEach((name, i) => {
    createCheckboxItem(container, {
      id: `${prefix}-${name}`,
      label: name,
      checked: false,
      onChange: () => onChange(),
      color: colors[i % colors.length],
    });
  });
}

function populateSnapshotCheckboxes() {
  const container = document.getElementById("snapshot-checkboxes");
  
  // Add all-projects checkbox first
  createCheckboxItem(container, {
    id: "snapshot-all-projects",
    label: "all-projects",
    checked: true, // Checked by default
    onChange: () => updateSnapshotCharts(),
    color: colors[0],
  });
  
  // Add individual projects
  order.forEach((name, i) => {
    createCheckboxItem(container, {
      id: `snapshot-${name}`,
      label: name,
      checked: false,
      onChange: () => updateSnapshotCharts(),
      color: colors[i % colors.length],
    });
  });
}

// ============================================================================
// Initialize charts
// ============================================================================

const issuesChart = createLineChart("issues-chart", "Open Issues (4-week avg)");
const medianAgeChart = createLineChart("median-age-chart", "Median Age (days, 4-week avg)");
const closedChart = createLineChart("closed-chart", "Issues Closed per Week (30-day avg)");

const snapshotOpenChart = createBarChart("snapshot-open-chart", "Count");
const snapshotAgeChart = createBarChart("snapshot-age-chart", "Days");
const snapshotClosedChart = createBarChart("snapshot-closed-chart", "Count");

// Populate checkboxes
populateLineChartCheckboxes("open-issues-checkboxes", "open-issues", updateOpenIssuesChart);
populateLineChartCheckboxes("median-age-checkboxes", "median-age", updateMedianAgeChart);
populateLineChartCheckboxes("closed-checkboxes", "closed", updateClosedChart);
populateSnapshotCheckboxes();

// View toggle listeners
document.getElementById("view-all").addEventListener("change", () => updateView("all"));
document.getElementById("view-external").addEventListener("change", () => updateView("external"));

// Initial render
updateOpenIssuesChart();
updateMedianAgeChart();
updateClosedChart();
updateSnapshotCharts();
