try {
// Color palette for projects
const CHART_COLORS = {
  palette: [
    "#E95420", "#0E8420", "#0066CC", "#772953", "#AEA79F",
    "#333333", "#007AA6", "#C7162B", "#F99B11", "#38B44A",
    "#5E2750", "#77216F", "#335280",
  ],
  issues: "#0066CC",
  prs: "#E95420",
};

// Fetch data
const response = await fetch("/stats/trends/all-data");
if (!response.ok) throw new Error(`HTTP ${response.status}`);
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
    const window = data.slice(start, i + 1).filter(v => v !== null);
    if (window.length === 0) {
      result.push(null);
    } else {
      result.push(window.reduce((sum, val) => sum + val, 0) / window.length);
    }
  }
  return result;
}

// Build a sorted union of all selected projects' dates.
function getUnifiedDates(selected) {
  const dateSet = new Set();
  for (const name of selected) {
    for (const d of filteredProjects[name].dates) {
      dateSet.add(d);
    }
  }
  return Array.from(dateSet).sort();
}

// Align a project's data array to the unified date axis.
// Returns null for dates where the project has no data.
function alignData(name, dataKey, unifiedDates) {
  const projDates = filteredProjects[name].dates;
  const projData = filteredProjects[name][dataKey];
  if (!projData) return unifiedDates.map(() => null);
  const dateMap = new Map();
  for (let i = 0; i < projDates.length; i++) {
    dateMap.set(projDates[i], projData[i]);
  }
  return unifiedDates.map(d => dateMap.has(d) ? dateMap.get(d) : null);
}

// Like rollingAverage but treats null/zero values as missing (skips them).
// Returns null when all values in the window are null/zero, so Chart.js can
// span across gaps with spanGaps: true instead of dropping to zero.
function rollingAverageNullable(data, windowSize) {
  const result = [];
  for (let i = 0; i < data.length; i++) {
    const start = Math.max(0, i - windowSize + 1);
    const window = data.slice(start, i + 1).filter(v => v !== null && v !== 0);
    if (window.length === 0) {
      result.push(null);
    } else {
      result.push(window.reduce((sum, val) => sum + val, 0) / window.length);
    }
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
        tooltip: {
          mode: "index",
          intersect: false,
          callbacks: {
            label: function(context) {
              const val = context.parsed.y;
              const rounded = val === null ? null : Math.round(val);
              return `${context.dataset.label}: ${rounded}`;
            }
          }
        }
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
  const ctx = document.getElementById(canvasId);

  // Wrap canvas in a positioned div (like starcraft-stats)
  const wrapper = document.createElement("div");
  wrapper.style.position = "relative";
  wrapper.style.height = `${CHART_BASE_HEIGHT_PX}px`;
  ctx.parentNode.insertBefore(wrapper, ctx);
  wrapper.appendChild(ctx);

  const chart = new Chart(ctx, {
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

  return { chart, wrapper };
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
  const unifiedDates = getUnifiedDates(selected);
  issuesChart.data.labels = unifiedDates;
  
  const dataKey = getDataKey("open");
  if (!dataKey) {
    issuesChart.data.labels = [];
    issuesChart.data.datasets = [];
    issuesChart.update();
    return;
  }
  
  issuesChart.data.datasets = selected.map((name) => {
    const rawData = alignData(name, dataKey, unifiedDates)
                    || alignData(name, "open", unifiedDates);
    const smoothedData = rollingAverage(rawData, 28); // 4-week rolling average
    const colorIdx = name === "all-projects" ? 0 : order.indexOf(name);
    const color = CHART_COLORS.palette[colorIdx % CHART_COLORS.palette.length];
    
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
  
  const view = getCurrentView();
  if (view === "none") {
    medianAgeChart.data.labels = [];
    medianAgeChart.data.datasets = [];
    medianAgeChart.update();
    return;
  }
  
  const firstProject = selected[0];
  const unifiedDates = getUnifiedDates(selected);
  medianAgeChart.data.labels = unifiedDates;
  
  const dataKey = view === "internal" ? "median_age_internal"
                : view === "external" ? "nm_median_age"
                : view === "bots" ? "median_age_bots"
                : "median_age";
  
  medianAgeChart.data.datasets = selected.map((name) => {
    const rawData = alignData(name, dataKey, unifiedDates);
    const smoothedData = rollingAverageNullable(rawData, 28); // 4-week rolling average, skips zeros
    const colorIdx = name === "all-projects" ? 0 : order.indexOf(name);
    const color = CHART_COLORS.palette[colorIdx % CHART_COLORS.palette.length];
    
    return {
      label: name,
      data: smoothedData,
      borderColor: color,
      backgroundColor: color + "20",
      borderWidth: 2,
      fill: false,
      tension: 0.1,
      spanGaps: true,
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
  const unifiedDates = getUnifiedDates(selected);
  closedChart.data.labels = unifiedDates;
  
  const dataKey = getDataKey("closed");
  if (!dataKey) {
    closedChart.data.labels = [];
    closedChart.data.datasets = [];
    closedChart.update();
    return;
  }
  
  closedChart.data.datasets = selected.map((name) => {
    const rawData = (alignData(name, dataKey, unifiedDates)
                    || alignData(name, "closed", unifiedDates)).map(v => v !== null ? v * 7 : null); // Scale to per-week
    const smoothedData = rollingAverage(rawData, 28); // 4-week rolling average
    const colorIdx = name === "all-projects" ? 0 : order.indexOf(name);
    const color = CHART_COLORS.palette[colorIdx % CHART_COLORS.palette.length];
    
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
  const view = getCurrentView();
  
  if (view === "none") {
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
  
  // Open Issues/PRs Chart
  const issueKey = view === "bots" ? "bots_open_issues"
    : view === "external" ? "nm_open_issues"
    : view === "internal" ? "internal_open_issues"
    : "open_issues";
  const prKey = view === "bots" ? "bots_open_prs"
    : view === "external" ? "nm_open_prs"
    : view === "internal" ? "internal_open_prs"
    : "open_prs";
  
  snapshotOpenChart.data.labels = labels;
  snapshotOpenChart.data.datasets = [
    {
      label: "Issues",
      data: selected.map(name => snapshot[name][issueKey]),
      backgroundColor: CHART_COLORS.issues,
      borderColor: CHART_COLORS.issues,
      borderWidth: 1,
    },
    {
      label: "PRs",
      data: selected.map(name => snapshot[name][prKey]),
      backgroundColor: CHART_COLORS.prs,
      borderColor: CHART_COLORS.prs,
      borderWidth: 1,
    },
  ];
  
  // Median Age Chart
  const issueAgeKey = view === "external" ? "nm_median_issue_age"
    : view === "internal" ? "median_issue_age_internal"
    : view === "bots" ? "median_issue_age_bots"
    : "median_issue_age";
  const prAgeKey = view === "external" ? "nm_median_pr_age"
    : view === "internal" ? "median_pr_age_internal"
    : view === "bots" ? "median_pr_age_bots"
    : "median_pr_age";
  
  snapshotAgeChart.data.labels = labels;
  snapshotAgeChart.data.datasets = [
    {
      label: "Issue Age",
      data: selected.map(name => snapshot[name][issueAgeKey]),
      backgroundColor: CHART_COLORS.issues,
      borderColor: CHART_COLORS.issues,
      borderWidth: 1,
    },
    {
      label: "PR Age",
      data: selected.map(name => snapshot[name][prAgeKey]),
      backgroundColor: CHART_COLORS.prs,
      borderColor: CHART_COLORS.prs,
      borderWidth: 1,
    },
  ];
  
  // Closed Last Year Chart
  const closedIssueKey = view === "external" ? "nm_closed_issues_year"
    : view === "bots" ? "bots_closed_issues_year"
    : view === "internal" ? "internal_closed_issues_year"
    : "closed_issues_year";
  const closedPrKey = view === "external" ? "nm_closed_prs_year"
    : view === "bots" ? "bots_closed_prs_year"
    : view === "internal" ? "internal_closed_prs_year"
    : "closed_prs_year";
  
  snapshotClosedChart.data.labels = labels;
  snapshotClosedChart.data.datasets = [
    {
      label: "Issues",
      data: selected.map(name => snapshot[name][closedIssueKey]),
      backgroundColor: CHART_COLORS.issues,
      borderColor: CHART_COLORS.issues,
      borderWidth: 1,
    },
    {
      label: "PRs",
      data: selected.map(name => snapshot[name][closedPrKey]),
      backgroundColor: CHART_COLORS.prs,
      borderColor: CHART_COLORS.prs,
      borderWidth: 1,
    },
  ];
  
  // Adjust chart height based on number of bars (like starcraft-stats)
  const numDatasets = snapshotOpenChart.data.datasets.length;
  const height = chartHeight(selected.length, numDatasets);
  snapshotOpenWrap.style.height = height + "px";
  snapshotAgeWrap.style.height = height + "px";
  snapshotClosedWrap.style.height = height + "px";
  
  snapshotOpenChart.update();
  snapshotAgeChart.update();
  snapshotClosedChart.update();
}

// ============================================================================
// View helpers
// ============================================================================

function getCurrentView() {
  const hiddenInput = document.querySelector('input[name="author-groups"]');
  const selected = hiddenInput ? hiddenInput.value.split(",").filter(Boolean) : ["maintainers", "contributors", "bots"];
  const maintainers = selected.includes("maintainers");
  const contributors = selected.includes("contributors");
  const bots = selected.includes("bots");
  
  if (!maintainers && !contributors && !bots) return "none";
  if (maintainers && contributors && bots) return "all";
  if (maintainers && !contributors && !bots) return "internal";
  if (!maintainers && contributors && !bots) return "external";
  if (!maintainers && !contributors && bots) return "bots";
  // Mixed cases: approximate with closest available series
  if (!maintainers && contributors && bots) return "external";  // non-maintainer (contributors + bots)
  // maintainers + contributors or maintainers + bots → show all (best approximation)
  return "all";
}

function getDataKey(baseKey) {
  const view = getCurrentView();
  if (view === "none") return null;
  if (view === "external") return baseKey + "_external";
  if (view === "internal") return baseKey + "_internal";
  if (view === "bots") return baseKey + "_bots";
  return baseKey; // "all"
}

function onViewChange() {
  updateOpenIssuesChart();
  updateMedianAgeChart();
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
        open_issues_bots: [],
        open: [],
        open_external: [],
        open_internal: [],
        open_bots: [],
        median_issue_age: [],
        median_issue_age_internal: [],
        nm_median_issue_age: [],
        median_issue_age_bots: [],
        median_age: [],
        median_age_internal: [],
        nm_median_age: [],
        median_age_bots: [],
        closed_issues: [],
        closed_issues_external: [],
        closed_issues_bots: [],
        closed: [],
        closed_external: [],
        closed_internal: [],
        closed_bots: [],
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
  const today = new Date().toISOString().slice(0, 10);
  document.getElementById("date-start").value = "2021-01-01";
  document.getElementById("date-end").value = today;
  applyDateFilter();
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
    color: CHART_COLORS.palette[0],
  });
  
  // Add individual projects
  order.forEach((name, i) => {
    createCheckboxItem(container, {
      id: `${prefix}-${name}`,
      label: name,
      checked: false,
      onChange: () => onChange(),
      color: CHART_COLORS.palette[i % CHART_COLORS.palette.length],
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
    color: CHART_COLORS.palette[0],
  });
  
  // Add individual projects
  order.forEach((name, i) => {
    createCheckboxItem(container, {
      id: `snapshot-${name}`,
      label: name,
      checked: false,
      onChange: () => updateSnapshotCharts(),
      color: CHART_COLORS.palette[i % CHART_COLORS.palette.length],
    });
  });
}

// ============================================================================
// Initialize charts
// ============================================================================

const issuesChart = createLineChart("issues-chart", "Open Issues & PRs (4-week avg)");
const medianAgeChart = createLineChart("median-age-chart", "Median Age (days, 4-week avg)");
const closedChart = createLineChart("closed-chart", "Closed per Week (4-week avg)");

const BAR_HEIGHT_PX = 28;
const CHART_BASE_HEIGHT_PX = 80;

function chartHeight(numBars, numDatasets) {
  return Math.max(1, numBars) * Math.max(1, numDatasets) * BAR_HEIGHT_PX + CHART_BASE_HEIGHT_PX;
}

const { chart: snapshotOpenChart, wrapper: snapshotOpenWrap } = createBarChart("snapshot-open-chart", "Count");
const { chart: snapshotAgeChart, wrapper: snapshotAgeWrap } = createBarChart("snapshot-age-chart", "Days");
const { chart: snapshotClosedChart, wrapper: snapshotClosedWrap } = createBarChart("snapshot-closed-chart", "Count");

// Populate checkboxes
populateLineChartCheckboxes("open-issues-checkboxes", "open-issues", updateOpenIssuesChart);
populateLineChartCheckboxes("median-age-checkboxes", "median-age", updateMedianAgeChart);
populateLineChartCheckboxes("closed-checkboxes", "closed", updateClosedChart);
populateSnapshotCheckboxes();

// Initialize author group multiselect change handler
const authorGroupsInput = document.querySelector('input[name="author-groups"]');
if (authorGroupsInput) {
  const observer = new MutationObserver(onViewChange);
  observer.observe(authorGroupsInput, { attributes: true, attributeFilter: ["value"] });
  // Also listen for change events from multiselect.js
  authorGroupsInput.addEventListener("change", onViewChange);
}

// Initialize type filter change handler
const trendTypeInput = document.querySelector('input[name="trend-type"]');
if (trendTypeInput) {
  trendTypeInput.addEventListener("change", onViewChange);
}

// Initialize date range inputs and apply default filter
const today = new Date().toISOString().slice(0, 10);
document.getElementById("date-start").value = "2021-01-01";
document.getElementById("date-end").value = today;

// Wire up date filter buttons (module scope, so we use addEventListener)
document.getElementById("btn-date-apply").addEventListener("click", applyDateFilter);
document.getElementById("btn-date-reset").addEventListener("click", resetDateFilter);

// Apply default date filter on page load (2021 to today)
applyDateFilter();

// Initial render (applyDateFilter already calls these, but ensure snapshot charts are updated)
updateSnapshotCharts();

// Hide loading spinner
document.getElementById("trends-loading").style.display = "none";
} catch (error) {
  console.error("Failed to load trend data:", error);
  document.getElementById("trends-loading").style.display = "none";
  document.querySelectorAll("canvas").forEach(c => {
    const ctx = c.getContext("2d");
    ctx.font = "14px sans-serif";
    ctx.fillStyle = "#666";
    ctx.textAlign = "center";
    ctx.fillText("Failed to load data", c.width / 2, c.height / 2);
  });
}
