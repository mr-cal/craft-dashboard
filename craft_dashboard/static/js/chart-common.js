// Shared Chart.js helpers used by both the Stats/Trends page (trends.js)
// and the Engagement/Forums page (engagement.js), to avoid duplicating
// theme handling, checkbox rendering, and rolling-average math.

export const CHART_COLORS = {
  palette: [
    "#E95420", "#0E8420", "#0066CC", "#772953", "#AEA79F",
    "#333333", "#007AA6", "#C7162B", "#F99B11", "#38B44A",
    "#5E2750", "#77216F", "#335280",
  ],
  issues: "#0066CC",
  prs: "#E95420",
};

export function getThemeColors(rootElement) {
  const themeName = rootElement.dataset.theme;
  const isDark = document.documentElement.classList.contains("is-dark-theme") || themeName === "dark";
  const textColor = isDark ? "#f3f3f3" : "#111";
  const gridColor = isDark ? "#4b5563" : "#e5e5e5";
  return { isDark, textColor, gridColor };
}

// Creates a small "chart registry" object bound to a single page's
// document.documentElement, holding the list of registered charts and
// providing theme-aware helpers that operate on them.
export function createChartRegistry(rootElement) {
  const registeredCharts = [];

  function applyChartDefaults() {
    const { textColor, gridColor } = getThemeColors(rootElement);
    Chart.defaults.color = textColor;
    Chart.defaults.borderColor = gridColor;
    return { textColor, gridColor };
  }

  function applyScaleTheme(scales, themeColors) {
    Object.values(scales || {}).forEach((scale) => {
      if (!scale) {
        return;
      }
      scale.grid = { ...(scale.grid || {}), color: themeColors.gridColor };
      scale.border = { ...(scale.border || {}), color: themeColors.gridColor };
      scale.ticks = { ...(scale.ticks || {}), color: themeColors.textColor };
      if (scale.title) {
        scale.title = { ...scale.title, color: themeColors.textColor };
      }
    });
  }

  function applyChartTheme(chart) {
    const themeColors = applyChartDefaults();
    applyScaleTheme(chart.options.scales, themeColors);

    if (chart.options.plugins?.legend?.labels) {
      chart.options.plugins.legend.labels.color = themeColors.textColor;
    }

    if (chart.options.plugins?.tooltip) {
      chart.options.plugins.tooltip.titleColor = themeColors.textColor;
      chart.options.plugins.tooltip.bodyColor = themeColors.textColor;
      chart.options.plugins.tooltip.borderColor = themeColors.gridColor;
      chart.options.plugins.tooltip.backgroundColor = themeColors.isDark ? "#111827" : "#ffffff";
    }
  }

  function registerChart(chart) {
    applyChartTheme(chart);
    registeredCharts.push(chart);
    return chart;
  }

  function createLineChart(canvasId, yLabel, xLabel = "Date") {
    const themeColors = applyChartDefaults();
    return registerChart(new Chart(document.getElementById(canvasId), {
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
            titleColor: themeColors.textColor,
            bodyColor: themeColors.textColor,
            borderColor: themeColors.gridColor,
            backgroundColor: themeColors.isDark ? "#111827" : "#ffffff",
            callbacks: {
              label: function (context) {
                const val = context.parsed.y;
                const rounded = val === null ? null : Math.round(val);
                return `${context.dataset.label}: ${rounded}`;
              },
            },
          },
        },
        scales: {
          x: {
            display: true,
            title: { display: true, text: xLabel, color: themeColors.textColor },
            ticks: { color: themeColors.textColor },
            grid: { color: themeColors.gridColor },
            border: { color: themeColors.gridColor },
          },
          y: {
            display: true,
            beginAtZero: true,
            title: { display: true, text: yLabel, color: themeColors.textColor },
            ticks: { precision: 0, color: themeColors.textColor },
            grid: { color: themeColors.gridColor },
            border: { color: themeColors.gridColor },
          },
        },
        interaction: { mode: "nearest", axis: "x", intersect: false },
      },
    }));
  }

  function createBarChart(canvasId, xLabel) {
    const ctx = document.getElementById(canvasId);
    const themeColors = applyChartDefaults();

    // Wrap canvas in a positioned div (like starcraft-stats)
    const wrapper = document.createElement("div");
    wrapper.style.position = "relative";
    wrapper.style.height = `${CHART_BASE_HEIGHT_PX}px`;
    ctx.parentNode.insertBefore(wrapper, ctx);
    wrapper.appendChild(ctx);

    const chart = registerChart(new Chart(ctx, {
      type: "bar",
      data: { labels: [], datasets: [] },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: true,
            position: "bottom",
            labels: { color: themeColors.textColor },
          },
          tooltip: {
            mode: "index",
            intersect: false,
            titleColor: themeColors.textColor,
            bodyColor: themeColors.textColor,
            borderColor: themeColors.gridColor,
            backgroundColor: themeColors.isDark ? "#111827" : "#ffffff",
          },
        },
        scales: {
          x: {
            display: true,
            beginAtZero: true,
            title: { display: true, text: xLabel, color: themeColors.textColor },
            ticks: { precision: 0, color: themeColors.textColor },
            grid: { color: themeColors.gridColor },
            border: { color: themeColors.gridColor },
          },
          y: {
            display: true,
            ticks: { color: themeColors.textColor },
            grid: { color: themeColors.gridColor },
            border: { color: themeColors.gridColor },
          },
        },
      },
    }));

    return { chart, wrapper };
  }

  function watchTheme() {
    const themeObserver = new MutationObserver((mutations) => {
      if (!mutations.some((m) => m.attributeName === "class" || m.attributeName === "data-theme")) {
        return;
      }
      registeredCharts.forEach((chart) => {
        applyChartTheme(chart);
        chart.update("none");
      });
    });
    themeObserver.observe(rootElement, { attributes: true, attributeFilter: ["class", "data-theme"] });
    return themeObserver;
  }

  function setTooltipsEnabled(enabled) {
    registeredCharts.forEach((chart) => {
      chart.options.plugins.tooltip.enabled = enabled;
      chart.update("none");
    });
  }

  function wireTooltipToggle(checkboxId) {
    const checkbox = document.getElementById(checkboxId);
    if (!checkbox) {
      return;
    }
    checkbox.addEventListener("change", (e) => {
      setTooltipsEnabled(!e.target.checked);
    });
  }

  return {
    registeredCharts,
    applyChartDefaults,
    applyScaleTheme,
    applyChartTheme,
    registerChart,
    createLineChart,
    createBarChart,
    watchTheme,
    setTooltipsEnabled,
    wireTooltipToggle,
  };
}

export const BAR_HEIGHT_PX = 28;
export const CHART_BASE_HEIGHT_PX = 80;

export function chartHeight(numBars, numDatasets) {
  return Math.max(1, numBars) * Math.max(1, numDatasets) * BAR_HEIGHT_PX + CHART_BASE_HEIGHT_PX;
}

export function createCheckboxItem(container, { id, label, checked, onChange, color }) {
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

// Simple trailing rolling average over a fixed number of array positions.
// Treats null as excluded from the window, but 0 as included.
export function rollingAverage(data, windowSize) {
  const result = [];
  for (let i = 0; i < data.length; i++) {
    const start = Math.max(0, i - windowSize + 1);
    const window = data.slice(start, i + 1).filter((v) => v !== null);
    if (window.length === 0) {
      result.push(null);
    } else {
      result.push(window.reduce((sum, val) => sum + val, 0) / window.length);
    }
  }
  return result;
}

// Like rollingAverage but treats null/zero values as missing (skips them).
// Returns null when all values in the window are null/zero, so Chart.js can
// span across gaps with spanGaps: true instead of dropping to zero.
export function rollingAverageNullable(data, windowSize) {
  const result = [];
  for (let i = 0; i < data.length; i++) {
    const start = Math.max(0, i - windowSize + 1);
    const window = data.slice(start, i + 1).filter((v) => v !== null && v !== 0);
    if (window.length === 0) {
      result.push(null);
    } else {
      result.push(window.reduce((sum, val) => sum + val, 0) / window.length);
    }
  }
  return result;
}

// Wires up a "Date Range" filter group (matching stats/trends.html's
// #date-start/#date-end/#btn-date-apply/#btn-date-reset markup) to a
// caller-supplied onApply(startDate, endDate) callback, and returns a
// resetToDefault(defaultStartStr) helper.
export function wireDateRangeFilter({ onApply }) {
  const startInput = document.getElementById("date-start");
  const endInput = document.getElementById("date-end");
  const applyBtn = document.getElementById("btn-date-apply");
  const resetBtn = document.getElementById("btn-date-reset");

  function applyDateFilter() {
    const startStr = startInput.value;
    const endStr = endInput.value;

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

    onApply(startDate, endDate);
  }

  function resetDateFilter(defaultStartStr) {
    const today = new Date().toISOString().slice(0, 10);
    startInput.value = defaultStartStr;
    endInput.value = today;
    applyDateFilter();
  }

  applyBtn.addEventListener("click", applyDateFilter);
  resetBtn.addEventListener("click", () => resetDateFilter(startInput.dataset.defaultStart || "2021-01-01"));

  return { applyDateFilter, resetDateFilter };
}
