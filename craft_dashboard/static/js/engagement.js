// Engagement page: one Chart.js line graph per Discourse forum, with a flat
// per-category checkbox filter ("all categories" + individual categories)
// mirroring the checkbox/chart patterns in trends.js.

try {
  const CHART_COLORS = {
    palette: [
      "#E95420", "#0E8420", "#0066CC", "#772953", "#AEA79F",
      "#333333", "#007AA6", "#C7162B", "#F99B11", "#38B44A",
      "#5E2750", "#77216F", "#335280",
    ],
  };

  const rootElement = document.documentElement;
  const registeredCharts = [];

  function getThemeColors() {
    const themeName = rootElement.dataset.theme;
    const isDark = document.documentElement.classList.contains("is-dark-theme") || themeName === "dark";
    const textColor = isDark ? "#f3f3f3" : "#111";
    const gridColor = isDark ? "#4b5563" : "#e5e5e5";
    return { isDark, textColor, gridColor };
  }

  function applyChartDefaults() {
    const { textColor, gridColor } = getThemeColors();
    Chart.defaults.color = textColor;
    Chart.defaults.borderColor = gridColor;
    return { textColor, gridColor };
  }

  function applyScaleTheme(scales, themeColors) {
    Object.values(scales || {}).forEach((scale) => {
      if (!scale) return;
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
          },
        },
        scales: {
          x: {
            display: true,
            title: { display: true, text: "Month", color: themeColors.textColor },
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

  const forums = window.ENGAGEMENT_FORUMS || [];
  const forumData = {}; // name -> { months, all, categories }

  async function loadForum(forum) {
    const response = await fetch(`/engagement/forums/data?forum=${encodeURIComponent(forum.name)}`);
    if (!response.ok) {
      // No data yet (e.g. backfill hasn't run) — leave the chart empty
      // rather than failing the whole page.
      forumData[forum.name] = { months: [], all: [], categories: {} };
      return;
    }
    forumData[forum.name] = await response.json();
  }

  function updateForumChart(forum, chart) {
    const data = forumData[forum.name];
    const checkboxContainer = document.getElementById(`engagement-${forum.name}-checkboxes`);
    const selectedCategories = forum.categories.filter((category) => {
      const cb = document.getElementById(`engagement-${forum.name}-category-${category}`);
      return cb?.checked;
    });
    const allCategoriesCb = document.getElementById(`engagement-${forum.name}-all-categories`);

    chart.data.labels = data.months;
    const datasets = [];
    if (allCategoriesCb?.checked) {
      datasets.push({
        label: "all categories",
        data: data.all,
        borderColor: CHART_COLORS.palette[0],
        backgroundColor: CHART_COLORS.palette[0] + "20",
        borderWidth: 2,
        fill: false,
        tension: 0.1,
      });
    }
    selectedCategories.forEach((category, i) => {
      const color = CHART_COLORS.palette[(i + 1) % CHART_COLORS.palette.length];
      datasets.push({
        label: category,
        data: data.categories[category] || data.months.map(() => 0),
        borderColor: color,
        backgroundColor: color + "20",
        borderWidth: 2,
        fill: false,
        tension: 0.1,
      });
    });
    chart.data.datasets = datasets;
    chart.update();
    void checkboxContainer; // referenced for clarity; no direct manipulation needed here
  }

  function populateForumCheckboxes(forum, chart) {
    const container = document.getElementById(`engagement-${forum.name}-checkboxes`);
    const defaultCategories = new Set(
      (container.dataset.defaultCategories || "")
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean)
    );

    createCheckboxItem(container, {
      id: `engagement-${forum.name}-all-categories`,
      label: "all categories",
      checked: true,
      onChange: () => updateForumChart(forum, chart),
      color: CHART_COLORS.palette[0],
    });

    forum.categories.forEach((category, i) => {
      createCheckboxItem(container, {
        id: `engagement-${forum.name}-category-${category}`,
        label: category,
        checked: defaultCategories.has(category),
        onChange: () => updateForumChart(forum, chart),
        color: CHART_COLORS.palette[(i + 1) % CHART_COLORS.palette.length],
      });
    });
  }

  await Promise.all(forums.map(loadForum));

  forums.forEach((forum) => {
    const chart = createLineChart(`engagement-${forum.name}-chart`, "Posts per month");
    populateForumCheckboxes(forum, chart);
    updateForumChart(forum, chart);
  });

  const themeObserver = new MutationObserver(() => {
    registeredCharts.forEach((chart) => {
      applyChartTheme(chart);
      chart.update("none");
    });
  });
  themeObserver.observe(rootElement, { attributes: true, attributeFilter: ["class", "data-theme"] });

  document.getElementById("engagement-loading").style.display = "none";
} catch (error) {
  console.error("Failed to load forum engagement data:", error);
  const loading = document.getElementById("engagement-loading");
  if (loading) loading.style.display = "none";
  document.querySelectorAll("canvas").forEach((c) => {
    const ctx = c.getContext("2d");
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Failed to load data", c.width / 2, c.height / 2);
  });
}
