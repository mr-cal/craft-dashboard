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

    if (typeof container.refreshMultiselectDisplay === "function") {
      container.refreshMultiselectDisplay();
    }
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

      const paramValue = url.searchParams.get(field.name);
      if (paramValue !== null && paramValue !== "") {
        field.value = paramValue;
      }
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

  function resetFiltersNotInUrl(url) {
    // Fields absent from the URL must fall back to their defaults, otherwise
    // navigating back to an unfiltered view would leave stale values in the
    // controls even though the table no longer reflects them.
    filterBar.querySelectorAll("input[name], select[name]").forEach((field) => {
      if (field.type === "hidden" && field.id.endsWith("-hidden")) {
        return;
      }
      if (url.searchParams.get(field.name)) {
        return;
      }
      if (field.tagName === "SELECT") {
        const fallback = Array.from(field.options).find((opt) => opt.defaultSelected);
        field.value = fallback ? fallback.value : field.options[0]?.value ?? "";
      } else {
        field.value = field.defaultValue;
      }
    });
  }

  function restoreStateFromHistory() {
    const url = new URL(window.location.href);
    resetFiltersNotInUrl(url);
    applyFiltersFromUrl(url.href);
    updateIssuesExportLink(url.href);

    // One coordinated request rather than letting each control fire its own:
    // the table must end up matching the URL the user navigated to.
    if (typeof htmx === "undefined") {
      return;
    }
    const tableUrl = buildFilteredUrl(url.href, "/issues/table");
    htmx.ajax("GET", `${tableUrl.pathname}${tableUrl.search}`, {
      target: "#issue-table",
      swap: "outerHTML",
    });
  }

  window.updateIssuesExportLink = updateIssuesExportLink;

  applyFiltersFromUrl(window.location.href);
  updateIssuesExportLink();

  document.body.addEventListener("htmx:afterSettle", syncBrowserUrl);
  window.addEventListener("popstate", restoreStateFromHistory);
})();
