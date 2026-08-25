// Engagement page: one Chart.js line graph per Discourse forum, with a flat
// per-category checkbox filter ("all categories" + individual categories),
// a rolling-average smoothed "new topics per day" metric, and shared
// date-range/tooltip-toggle controls (see chart-common.js).

import {
  CHART_COLORS,
  createChartRegistry,
  createCheckboxItem,
  rollingAverage,
  wireDateRangeFilter,
} from "/static/js/chart-common.js";

const ROLLING_WINDOW_DAYS = 30;
const DEFAULT_START_DATE = "2017-01-01";

try {
  const rootElement = document.documentElement;
  const registry = createChartRegistry(rootElement);
  const { createLineChart, registeredCharts } = registry;

  const forums = window.ENGAGEMENT_FORUMS || [];
  const forumData = {}; // name -> { days, all, categories }
  const forumCharts = {}; // name -> Chart

  async function loadForum(forum) {
    const response = await fetch(`/engagement/forums/data?forum=${encodeURIComponent(forum.name)}`);
    if (!response.ok) {
      // No data yet (e.g. backfill hasn't run) — leave the chart empty
      // rather than failing the whole page.
      forumData[forum.name] = { days: [], all: [], categories: {} };
      return;
    }
    forumData[forum.name] = await response.json();
  }

  // Slice a forum's day-bucketed data to [startDate, endDate], returning a
  // new data object shaped like the raw fetch response.
  function sliceForumData(data, startDate, endDate) {
    const days = data.days;
    let startIdx = days.findIndex((d) => new Date(d) >= startDate);
    let endIdx = days.findLastIndex((d) => new Date(d) <= endDate);
    if (startIdx === -1 || endIdx === -1 || startIdx > endIdx) {
      return { days: [], all: [], categories: {} };
    }
    const categories = {};
    for (const [category, series] of Object.entries(data.categories)) {
      categories[category] = series.slice(startIdx, endIdx + 1);
    }
    return {
      days: days.slice(startIdx, endIdx + 1),
      all: data.all.slice(startIdx, endIdx + 1),
      categories,
    };
  }

  let currentRange = null; // { startDate, endDate } | null (null = unfiltered)

  // Round a rolling-average value for display; whole-number "topics per
  // day" reads more cleanly than a bouncing decimal, and the underlying
  // average is still computed at full precision before rounding.
  function roundOrNull(value) {
    return value === null ? null : Math.round(value);
  }

  function updateForumChart(forum) {
    const chart = forumCharts[forum.name];
    const raw = forumData[forum.name];
    const data = currentRange ? sliceForumData(raw, currentRange.startDate, currentRange.endDate) : raw;

    const selectedCategories = forum.categories.filter((category) => {
      const cb = document.getElementById(`engagement-${forum.name}-category-${category}`);
      return cb?.checked;
    });
    const allCategoriesCb = document.getElementById(`engagement-${forum.name}-all-categories`);

    chart.data.labels = data.days;
    const datasets = [];
    if (allCategoriesCb?.checked) {
      datasets.push({
        label: "all categories",
        data: rollingAverage(data.all, ROLLING_WINDOW_DAYS).map(roundOrNull),
        borderColor: CHART_COLORS.palette[0],
        backgroundColor: CHART_COLORS.palette[0] + "20",
        borderWidth: 2,
        fill: false,
        tension: 0.1,
      });
    }
    selectedCategories.forEach((category, i) => {
      const color = CHART_COLORS.palette[(i + 1) % CHART_COLORS.palette.length];
      const series = data.categories[category] || data.days.map(() => 0);
      datasets.push({
        label: category,
        data: rollingAverage(series, ROLLING_WINDOW_DAYS).map(roundOrNull),
        borderColor: color,
        backgroundColor: color + "20",
        borderWidth: 2,
        fill: false,
        tension: 0.1,
      });
    });
    chart.data.datasets = datasets;
    chart.update();
  }

  function updateAllCharts() {
    forums.forEach((forum) => updateForumChart(forum));
  }

  function populateForumCheckboxes(forum) {
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
      onChange: () => updateForumChart(forum),
      color: CHART_COLORS.palette[0],
    });

    forum.categories.forEach((category, i) => {
      createCheckboxItem(container, {
        id: `engagement-${forum.name}-category-${category}`,
        label: category,
        checked: defaultCategories.has(category),
        onChange: () => updateForumChart(forum),
        color: CHART_COLORS.palette[(i + 1) % CHART_COLORS.palette.length],
      });
    });
  }

  await Promise.all(forums.map(loadForum));

  forums.forEach((forum) => {
    const chart = createLineChart(
      `engagement-${forum.name}-chart`,
      `New topics per day (${ROLLING_WINDOW_DAYS}-day avg)`,
      "Date"
    );
    forumCharts[forum.name] = chart;
    populateForumCheckboxes(forum);
    updateForumChart(forum);
  });

  registry.watchTheme();
  registry.wireTooltipToggle("hide-tooltips");

  // Cap each chart's rendered height to a fraction of the viewport height,
  // so forums with very large category lists (e.g. discourse forums, with
  // 100+ categories) don't stretch the chart column to match the tall
  // checkbox column.
  const MAX_CHART_VH = 70;
  document.querySelectorAll("[data-engagement-chart-wrapper]").forEach((wrapper) => {
    wrapper.style.maxHeight = `${MAX_CHART_VH}vh`;
  });

  const startInput = document.getElementById("date-start");
  if (startInput) {
    startInput.dataset.defaultStart = DEFAULT_START_DATE;
  }
  wireDateRangeFilter({
    onApply: (startDate, endDate) => {
      currentRange = { startDate, endDate };
      updateAllCharts();
    },
  });
  document.getElementById("btn-date-reset").click();

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
