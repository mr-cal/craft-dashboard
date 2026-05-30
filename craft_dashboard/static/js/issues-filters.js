(function () {
  const filterBar = document.getElementById("issue-filters");

  if (!filterBar) {
    return;
  }

  const shareableUrlRoot = filterBar.dataset.shareableUrlRoot || "/issues";
  const exportUrlRoot = filterBar.dataset.exportUrlRoot || "/issues/export";
  const exportLink = document.getElementById("export-json-link");

  function splitValue(value) {
    return (value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function syncMultiselect(name, value) {
    const container = filterBar.querySelector(`.multiselect[data-name="${name}"]`);
    if (!container) {
      return;
    }

    const selectedValues = new Set(splitValue(value));
    const hiddenInput = document.getElementById(container.dataset.hidden);
    if (hiddenInput) {
      hiddenInput.value = value;
    }

    container.querySelectorAll(".multiselect__option input").forEach((option) => {
      option.checked = selectedValues.has(option.value);
    });
  }

  function applyFiltersFromUrl(urlValue) {
    const url = new URL(urlValue, window.location.origin);

    filterBar.querySelectorAll(".multiselect").forEach((container) => {
      const hiddenInput = document.getElementById(container.dataset.hidden);
      const fallbackValue = hiddenInput ? hiddenInput.defaultValue : "";
      syncMultiselect(
        container.dataset.name,
        url.searchParams.get(container.dataset.name) ?? fallbackValue,
      );
    });

    filterBar.querySelectorAll("input[name], select[name]").forEach((field) => {
      if (field.type === "hidden" && field.id.endsWith("-hidden")) {
        return;
      }

      const fallbackValue = field.defaultValue || field.getAttribute("value") || "";
      field.value = url.searchParams.get(field.name) ?? fallbackValue;
    });
  }

  function buildFilteredUrl(sourceUrl, targetPath) {
    const currentUrl = sourceUrl
      ? new URL(sourceUrl, window.location.origin)
      : new URL(window.location.href);
    const filteredUrl = new URL(targetPath, window.location.origin);

    currentUrl.searchParams.forEach((value, key) => {
      if (value) {
        filteredUrl.searchParams.set(key, value);
      }
    });

    return filteredUrl;
  }

  function buildShareableUrl(sourceUrl) {
    return buildFilteredUrl(sourceUrl, shareableUrlRoot);
  }

  function updateIssuesExportLink(sourceUrl) {
    if (!exportLink) {
      return;
    }

    const exportUrl = buildFilteredUrl(sourceUrl, exportUrlRoot);
    exportLink.href = `${exportUrl.pathname}${exportUrl.search}`;
  }

  function syncBrowserUrl(event) {
    if (!event.detail?.target || event.detail.target.id !== "issue-table") {
      return;
    }

    const responseUrl = event.detail.xhr?.responseURL;
    const shareableUrl = buildShareableUrl(responseUrl);
    const nextUrl = `${shareableUrl.pathname}${shareableUrl.search}`;

    if (nextUrl !== `${window.location.pathname}${window.location.search}`) {
      window.history.pushState({}, "", nextUrl);
    }

    updateIssuesExportLink(responseUrl);
  }

  window.updateIssuesExportLink = updateIssuesExportLink;

  applyFiltersFromUrl(window.location.href);
  updateIssuesExportLink();

  document.body.addEventListener("htmx:afterSettle", syncBrowserUrl);
})();
