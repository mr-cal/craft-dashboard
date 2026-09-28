import {
  CHART_COLORS,
  createChartRegistry,
  rollingAverage,
  rollingAverageNullable,
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
const { createLineChart } = registry;

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
// Project selection helper
// ============================================================================

function getSelectedProjects() {
  const hiddenInput = document.getElementById("trend-projects-hidden");
  if (!hiddenInput || !hiddenInput.value) {
    return ["all-projects"];
  }
  return hiddenInput.value.split(",").map(s => s.trim()).filter(Boolean);
}

// ============================================================================
// Line chart updates
// ============================================================================

function updateOpenIssuesChart() {
  const selected = getSelectedProjects();
  
  if (!selected.length) {
    issuesChart.data.labels = [];
    issuesChart.data.datasets = [];
    issuesChart.update();
    return;
  }
  
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
  const selected = getSelectedProjects();
  
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
  const selected = getSelectedProjects();
  
  if (!selected.length) {
    closedChart.data.labels = [];
    closedChart.data.datasets = [];
    closedChart.update();
    return;
  }
  
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
// Snapshot table & summary updates
// ============================================================================

function updateSnapshotTable() {
  const selected = getSelectedProjects();
  const showAll = selected.includes("all-projects");
  const visibleProjects = order.filter(name => showAll || selected.includes(name));

  const view = getCurrentView();
  const types = getSelectedTypes();

  const issueKey = view === "bots" ? "bots_open_issues"
    : view === "external" ? "nm_open_issues"
    : view === "internal" ? "internal_open_issues"
    : "open_issues";
  const prKey = view === "bots" ? "bots_open_prs"
    : view === "external" ? "nm_open_prs"
    : view === "internal" ? "internal_open_prs"
    : "open_prs";
  const closedIssueKey = view === "external" ? "nm_closed_issues_year"
    : view === "bots" ? "bots_closed_issues_year"
    : view === "internal" ? "internal_closed_issues_year"
    : "closed_issues_year";
  const closedPrKey = view === "external" ? "nm_closed_prs_year"
    : view === "bots" ? "bots_closed_prs_year"
    : view === "internal" ? "internal_closed_prs_year"
    : "closed_prs_year";
  const issueAgeKey = view === "external" ? "nm_median_issue_age"
    : view === "internal" ? "median_issue_age_internal"
    : view === "bots" ? "median_issue_age_bots"
    : "median_issue_age";
  const prAgeKey = view === "external" ? "nm_median_pr_age"
    : view === "internal" ? "median_pr_age_internal"
    : view === "bots" ? "median_pr_age_bots"
    : "median_pr_age";

  let sumClosedIssues = 0;
  let sumClosedPrs = 0;
  let sumOpenIssues = 0;
  let sumOpenPrs = 0;
  let ageIssuesList = [];
  let agePrsList = [];

  visibleProjects.forEach(name => {
    const p = snapshot[name];
    if (!p) return;
    sumClosedIssues += (p[closedIssueKey] || 0);
    sumClosedPrs += (p[closedPrKey] || 0);
    sumOpenIssues += (p[issueKey] || 0);
    sumOpenPrs += (p[prKey] || 0);
    if (p[issueAgeKey] != null && p[issueAgeKey] > 0) ageIssuesList.push(p[issueAgeKey]);
    if (p[prAgeKey] != null && p[prAgeKey] > 0) agePrsList.push(p[prAgeKey]);
  });

  const totClosed = (types.issues ? sumClosedIssues : 0) + (types.prs ? sumClosedPrs : 0);
  const totOpen = (types.issues ? sumOpenIssues : 0) + (types.prs ? sumOpenPrs : 0);
  const avgIssueAge = ageIssuesList.length ? Math.round(ageIssuesList.reduce((a, b) => a + b, 0) / ageIssuesList.length) : null;
  const avgPrAge = agePrsList.length ? Math.round(agePrsList.reduce((a, b) => a + b, 0) / agePrsList.length) : null;

  const elClosedTot = document.getElementById("snap-closed-year-total");
  const elClosedBreak = document.getElementById("snap-closed-year-breakdown");
  const elOpenTot = document.getElementById("snap-open-total");
  const elOpenBreak = document.getElementById("snap-open-breakdown");
  const elMedAge = document.getElementById("snap-median-age");
  const elMedBreak = document.getElementById("snap-median-breakdown");

  if (elClosedTot) elClosedTot.textContent = totClosed.toLocaleString();
  if (elClosedBreak) elClosedBreak.textContent = `(${sumClosedIssues.toLocaleString()} issues, ${sumClosedPrs.toLocaleString()} PRs)`;
  if (elOpenTot) elOpenTot.textContent = totOpen.toLocaleString();
  if (elOpenBreak) elOpenBreak.textContent = `(${sumOpenIssues.toLocaleString()} issues, ${sumOpenPrs.toLocaleString()} PRs)`;
  if (elMedAge) {
    const parts = [];
    if (types.issues && avgIssueAge !== null) parts.push(`${avgIssueAge}d issues`);
    if (types.prs && avgPrAge !== null) parts.push(`${avgPrAge}d PRs`);
    elMedAge.textContent = parts.length ? parts.join(" / ") : "—";
  }
  if (elMedBreak) elMedBreak.textContent = `across ${visibleProjects.length} selected project${visibleProjects.length === 1 ? '' : 's'}`;

  const tbody = document.getElementById("snapshot-table-body");
  if (tbody) {
    tbody.innerHTML = visibleProjects.map(name => {
      const p = snapshot[name];
      if (!p) return "";
      const cIssues = p[closedIssueKey] || 0;
      const cPrs = p[closedPrKey] || 0;
      const cTotal = (types.issues ? cIssues : 0) + (types.prs ? cPrs : 0);
      const opIssues = p[issueKey] || 0;
      const opPrs = p[prKey] || 0;
      const ageIssue = p[issueAgeKey] != null && p[issueAgeKey] > 0 ? `${p[issueAgeKey]}d` : "—";
      const agePr = p[prAgeKey] != null && p[prAgeKey] > 0 ? `${p[prAgeKey]}d` : "—";

      return `
        <tr>
          <td><strong>${name}</strong></td>
          <td class="u-align--right" data-sort-value="${cTotal}">${cTotal.toLocaleString()}</td>
          <td class="u-align--right" data-sort-value="${types.issues ? opIssues : 0}">${types.issues ? opIssues.toLocaleString() : "—"}</td>
          <td class="u-align--right" data-sort-value="${types.prs ? opPrs : 0}">${types.prs ? opPrs.toLocaleString() : "—"}</td>
          <td class="u-align--right" data-sort-value="${types.issues && p[issueAgeKey] != null ? p[issueAgeKey] : -1}">${types.issues ? ageIssue : "—"}</td>
          <td class="u-align--right" data-sort-value="${types.prs && p[prAgeKey] != null ? p[prAgeKey] : -1}">${types.prs ? agePr : "—"}</td>
        </tr>
      `;
    }).join("");
  }
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
  updateSnapshotTable();
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
  updateSnapshotTable();
}

// ============================================================================
// Initialize charts
// ============================================================================

const issuesChart = createLineChart("issues-chart", "Open issues & PRs (4-week avg)");
const medianAgeChart = createLineChart("median-age-chart", "Median age (days, 4-week avg)");
const closedChart = createLineChart("closed-chart", "Closed per week (4-week avg)");

registry.watchTheme();

// Initialize author group multiselect change handler
const authorGroupsInput = document.querySelector('input[name="author-groups"]');
if (authorGroupsInput) {
  const observer = new MutationObserver(onViewChange);
  observer.observe(authorGroupsInput, { attributes: true, attributeFilter: ["value"] });
  authorGroupsInput.addEventListener("change", onViewChange);
}

// Initialize type filter change handler
const trendTypeInput = document.querySelector('input[name="trend-type"]');
if (trendTypeInput) {
  const observer = new MutationObserver(onViewChange);
  observer.observe(trendTypeInput, { attributes: true, attributeFilter: ["value"] });
  trendTypeInput.addEventListener("change", onViewChange);
}

// Initialize projects multiselect change handler
const trendProjectsInput = document.querySelector('input[name="trend-projects"]');
if (trendProjectsInput) {
  const observer = new MutationObserver(onViewChange);
  observer.observe(trendProjectsInput, { attributes: true, attributeFilter: ["value"] });
  trendProjectsInput.addEventListener("change", onViewChange);
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

// Initial render
updateSnapshotTable();

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
