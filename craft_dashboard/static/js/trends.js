// Color palette for projects
const colors = [
  "#E95420", "#0E8420", "#0066CC", "#772953", "#AEA79F",
  "#333333", "#007AA6", "#C7162B", "#F99B11", "#38B44A",
  "#5E2750", "#77216F", "#335280",
];

// Fetch data
const response = await fetch("/stats/trends/all-data");
const { projects, order, snapshot } = await response.json();

// Store full data and filtered data
let allProjects = projects;
let filteredProjects = projects;

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
  issuesChart.data.labels = filteredProjects[firstProject].dates;
  
  const dataKey = getCurrentView() === "all" ? "open_issues" : "open_issues_external";
  
  issuesChart.data.datasets = selected.map((name) => {
    const rawData = filteredProjects[name][dataKey];
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
  medianAgeChart.data.labels = filteredProjects[firstProject].dates;
  
  const dataKey = "median_issue_age"; // We don't have separate external median age yet
  
  medianAgeChart.data.datasets = selected.map((name) => {
    const rawData = filteredProjects[name][dataKey];
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
  closedChart.data.labels = filteredProjects[firstProject].dates;
  
  const dataKey = getCurrentView() === "all" ? "closed_issues" : "closed_issues_external";
  
  closedChart.data.datasets = selected.map((name) => {
    const rawData = filteredProjects[name][dataKey].map(v => v * 7); // Scale to per-week
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
  const issueKey = getCurrentView() === "all" ? "open_issues" : "nm_open_issues";
  const prKey = getCurrentView() === "all" ? "open_prs" : "nm_open_prs";
  
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
  const issueAgeKey = getCurrentView() === "all" ? "median_issue_age" : "nm_median_issue_age";
  const prAgeKey = getCurrentView() === "all" ? "median_pr_age" : "nm_median_pr_age";
  
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
  const closedIssueKey = getCurrentView() === "all" ? "closed_issues_year" : "nm_closed_issues_year";
  const closedPrKey = getCurrentView() === "all" ? "closed_prs_year" : "nm_closed_prs_year";
  
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
// View helpers
// ============================================================================

function getCurrentView() {
  const maintainers = document.getElementById("view-maintainers")?.checked ?? true;
  const contributors = document.getElementById("view-contributors")?.checked ?? true;
  const bots = document.getElementById("view-bots")?.checked ?? true;
  // Only "contributors only" maps to external data; everything else uses all data
  if (contributors && !maintainers && !bots) return "external";
  return "all";
}

function onViewChange() {
  updateOpenIssuesChart();
  updateClosedChart();
  updateSnapshotCharts();
}

// ============================================================================
// Date filtering
// ============================================================================

function applyDateFilter() {
  const startStr = document.getElementById("date-start").value;
  const endStr = document.getElementById("date-end").value;
  
  if (!startStr || !endStr) {
    alert("Please select both start and end dates");
    return;
  }
  
  const startDate = new Date(startStr);
  const endDate = new Date(endStr);
  
  if (startDate > endDate) {
    alert("Start date must be before end date");
    return;
  }
  
  // Filter each project's dates and data arrays
  filteredProjects = {};
  for (const name in allProjects) {
    const project = allProjects[name];
    const dates = project.dates;
    
    // Find start and end indices
    let startIdx = dates.findIndex(d => new Date(d) >= startDate);
    let endIdx = dates.findLastIndex(d => new Date(d) <= endDate);
    
    if (startIdx === -1 || endIdx === -1 || startIdx > endIdx) {
      // No data in range, create empty project
      filteredProjects[name] = {
        dates: [],
        open_issues: [],
        open_issues_external: [],
        median_issue_age: [],
        closed_issues: [],
        closed_issues_external: [],
      };
    } else {
      // Slice dates and all data arrays
      filteredProjects[name] = {
        dates: dates.slice(startIdx, endIdx + 1),
      };
      
      // Slice all data keys
      for (const key in project) {
        if (key !== "dates" && Array.isArray(project[key])) {
          filteredProjects[name][key] = project[key].slice(startIdx, endIdx + 1);
        }
      }
    }
  }
  
  // Re-render all charts with filtered data
  updateOpenIssuesChart();
  updateMedianAgeChart();
  updateClosedChart();
  updateSnapshotCharts();
}

function resetDateFilter() {
  filteredProjects = allProjects;
  const today = new Date().toISOString().slice(0, 10);
  document.getElementById("date-start").value = "2021-01-01";
  document.getElementById("date-end").value = today;
  
  updateOpenIssuesChart();
  updateMedianAgeChart();
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
    checked: true, // Check all-projects by default for all charts
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

// Initialize author group checkboxes
["view-maintainers", "view-contributors", "view-bots"].forEach(id => {
  document.getElementById(id)?.addEventListener("change", onViewChange);
});

// Initialize date range inputs
const today = new Date().toISOString().slice(0, 10);
document.getElementById("date-start").value = "2021-01-01";
document.getElementById("date-end").value = today;

// Initial render
updateOpenIssuesChart();
updateMedianAgeChart();
updateClosedChart();
updateSnapshotCharts();
