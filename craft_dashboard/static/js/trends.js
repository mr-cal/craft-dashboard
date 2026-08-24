import {
  CHART_COLORS,
  createChartRegistry,
  createCheckboxItem,
  rollingAverage,
  rollingAverageNullable,
  chartHeight,
  wireDateRangeFilter,
} from "/static/js/chart-common.js";

try {
// Fetch data
const response = await fetch("/stats/trends/all-data");
if (!response.ok) throw new Error(`HTTP ${response.status}`);
const { projects, order, snapshot } = await response.json();

// Store full data and filtered data
let allProjects = projects;
let filteredProjects = projects;
const rootElement = document.documentElement;
const registry = createChartRegistry(rootElement);
const { createLineChart, createBarChart } = registry;

function getThemeColors() {
  return registry.applyChartDefaults();
}

// ============================================================================
// Utility functions
// ============================================================================

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
  
  const dataKey = getMedianAgeDataKey();
  if (!dataKey) {
    medianAgeChart.data.labels = [];
    medianAgeChart.data.datasets = [];
    medianAgeChart.update();
    return;
  }
  
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
  const types = getSelectedTypes();

  const issueKey = view === "bots" ? "bots_open_issues"
    : view === "external" ? "nm_open_issues"
    : view === "internal" ? "internal_open_issues"
    : "open_issues";
  const prKey = view === "bots" ? "bots_open_prs"
    : view === "external" ? "nm_open_prs"
    : view === "internal" ? "internal_open_prs"
    : "open_prs";
  
  snapshotOpenChart.data.labels = labels;
  const openDatasets = [];
  if (types.issues) {
    openDatasets.push({
      label: "Issues",
      data: selected.map(name => snapshot[name][issueKey]),
      backgroundColor: CHART_COLORS.issues,
      borderColor: CHART_COLORS.issues,
      borderWidth: 1,
    });
  }
  if (types.prs) {
    openDatasets.push({
      label: "PRs",
      data: selected.map(name => snapshot[name][prKey]),
      backgroundColor: CHART_COLORS.prs,
      borderColor: CHART_COLORS.prs,
      borderWidth: 1,
    });
  }
  snapshotOpenChart.data.datasets = openDatasets;
  
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
  const ageDatasets = [];
  if (types.issues) {
    ageDatasets.push({
      label: "Issue age",
      data: selected.map(name => snapshot[name][issueAgeKey]),
      backgroundColor: CHART_COLORS.issues,
      borderColor: CHART_COLORS.issues,
      borderWidth: 1,
    });
  }
  if (types.prs) {
    ageDatasets.push({
      label: "PR Age",
      data: selected.map(name => snapshot[name][prAgeKey]),
      backgroundColor: CHART_COLORS.prs,
      borderColor: CHART_COLORS.prs,
      borderWidth: 1,
    });
  }
  snapshotAgeChart.data.datasets = ageDatasets;
  
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
  const closedDatasets = [];
  if (types.issues) {
    closedDatasets.push({
      label: "Issues",
      data: selected.map(name => snapshot[name][closedIssueKey]),
      backgroundColor: CHART_COLORS.issues,
      borderColor: CHART_COLORS.issues,
      borderWidth: 1,
    });
  }
  if (types.prs) {
    closedDatasets.push({
      label: "PRs",
      data: selected.map(name => snapshot[name][closedPrKey]),
      backgroundColor: CHART_COLORS.prs,
      borderColor: CHART_COLORS.prs,
      borderWidth: 1,
    });
  }
  snapshotClosedChart.data.datasets = closedDatasets;
  
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

function getSelectedTypes() {
  const hiddenInput = document.querySelector('input[name="trend-type"]');
  const selected = hiddenInput ? hiddenInput.value.split(",").filter(Boolean) : ["issue", "pull_request"];
  return {
    issues: selected.includes("issue"),
    prs: selected.includes("pull_request"),
  };
}

function getDataKey(baseKey) {
  const view = getCurrentView();
  if (view === "none") return null;

  const types = getSelectedTypes();
  if (!types.issues && !types.prs) return null;

  // Determine type suffix: "" for both, "_issues" for issue only, "_prs" for PR only
  let typeSuffix = "";
  if (types.issues && !types.prs) typeSuffix = "_issues";
  else if (!types.issues && types.prs) typeSuffix = "_prs";

  // Determine view suffix
  let viewSuffix = "";
  if (view === "external") viewSuffix = "_external";
  else if (view === "internal") viewSuffix = "_internal";
  else if (view === "bots") viewSuffix = "_bots";

  return baseKey + typeSuffix + viewSuffix;
}

function getMedianAgeDataKey() {
  const view = getCurrentView();
  if (view === "none") return null;

  const types = getSelectedTypes();
  if (!types.issues && !types.prs) return null;

  // Median age keys have inconsistent naming: nm_ prefix for external,
  // and type goes in the middle (median_issue_age vs median_age).
  let typeInfix = "";
  if (types.issues && !types.prs) typeInfix = "_issue";
  else if (!types.issues && types.prs) typeInfix = "_pr";

  if (view === "external") return "nm_median" + typeInfix + "_age";
  if (view === "internal") return "median" + typeInfix + "_age_internal";
  if (view === "bots") return "median" + typeInfix + "_age_bots";
  return "median" + typeInfix + "_age";
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

function applyDateFilterForRange(startDate, endDate) {
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
        open_prs: [],
        open_issues_external: [],
        open_prs_external: [],
        open_issues_internal: [],
        open_prs_internal: [],
        open_issues_bots: [],
        open_prs_bots: [],
        open: [],
        open_external: [],
        open_internal: [],
        open_bots: [],
        median_issue_age: [],
        median_pr_age: [],
        median_issue_age_internal: [],
        median_pr_age_internal: [],
        nm_median_issue_age: [],
        nm_median_pr_age: [],
        median_issue_age_bots: [],
        median_pr_age_bots: [],
        median_age: [],
        median_age_internal: [],
        nm_median_age: [],
        median_age_bots: [],
        closed_issues: [],
        closed_prs: [],
        closed_issues_external: [],
        closed_prs_external: [],
        closed_issues_internal: [],
        closed_prs_internal: [],
        closed_issues_bots: [],
        closed_prs_bots: [],
        closed: [],
        closed_external: [],
        closed_internal: [],
        closed_bots: [],
        open_bugs: [],
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

const issuesChart = createLineChart("issues-chart", "Open issues & PRs (4-week avg)");
const medianAgeChart = createLineChart("median-age-chart", "Median age (days, 4-week avg)");
const closedChart = createLineChart("closed-chart", "Closed per week (4-week avg)");

registry.watchTheme();

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
const dateStartInput = document.getElementById("date-start");
dateStartInput.dataset.defaultStart = "2021-01-01";
wireDateRangeFilter({
  onApply: (startDate, endDate) => applyDateFilterForRange(startDate, endDate),
});

// Wire up tooltip toggle
registry.wireTooltipToggle("hide-tooltips");

// Apply default date filter on page load (2021 to today)
document.getElementById("btn-date-reset").click();

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
    ctx.fillStyle = getThemeColors().textColor;
    ctx.textAlign = "center";
    ctx.fillText("Failed to load data", c.width / 2, c.height / 2);
  });
}
