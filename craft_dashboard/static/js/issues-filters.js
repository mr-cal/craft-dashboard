(function () {
  const filterBar = document.getElementById("issue-filters");

  if (!filterBar) {
    return;
  }

  const shareableUrlRoot = filterBar.dataset.shareableUrlRoot || "/issues";

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

  function buildShareableUrl(sourceUrl) {
    const currentUrl = sourceUrl
      ? new URL(sourceUrl, window.location.origin)
      : new URL(window.location.href);
    const shareableUrl = new URL(shareableUrlRoot, window.location.origin);

    currentUrl.searchParams.forEach((value, key) => {
      if (value) {
        shareableUrl.searchParams.set(key, value);
      }
    });

    return shareableUrl;
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

    if (typeof window.updateIssuesExportLink === "function") {
      window.updateIssuesExportLink();
    }
  }

  applyFiltersFromUrl(window.location.href);

  if (typeof window.updateIssuesExportLink === "function") {
    window.updateIssuesExportLink();
  }

  document.body.addEventListener("htmx:afterSettle", syncBrowserUrl);
})();
