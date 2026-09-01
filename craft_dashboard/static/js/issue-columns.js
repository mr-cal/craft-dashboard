(function () {
  const storageKey = "visible_columns";
  const scoreColumns = [
    "staleness",
    "complexity",
    "support_request",
    "impact",
    "quick_win",
    "confidence",
  ];
  const defaultColumns = [
    "issue",
    "title",
    "author",
    "age",
    ...scoreColumns.filter((column) => ["staleness", "confidence"].includes(column)),
    "action",
    "summary",
  ];
  const hiddenInput = document.getElementById("columns-hidden");
  const scoresInput = document.getElementById("scores-hidden");

  if (!hiddenInput) {
    return;
  }

  function splitColumns(value) {
    return (value || "")
      .split(",")
      .map((column) => column.trim())
      .filter(Boolean);
  }

  function getInitialColumns() {
    const storedColumns = window.localStorage.getItem(storageKey);
    if (storedColumns === null) {
      return defaultColumns;
    }
    return splitColumns(storedColumns);
  }

  function setPickerState(columns) {
    const selectedColumns = new Set(columns);
    hiddenInput.value = columns.join(",");

    document
      .querySelectorAll('.multiselect[data-name="visible_columns"] .multiselect__option input')
      .forEach((option) => {
        option.checked = selectedColumns.has(option.value);
      });
  }

  function syncScoreColumns(visibleColumns) {
    if (!scoresInput) {
      return;
    }

    const nextScores = scoreColumns.filter((column) => visibleColumns.has(column));
    const nextValue = nextScores.join(",");
    if (scoresInput.value === nextValue) {
      return;
    }

    scoresInput.value = nextValue;
    if (typeof htmx !== "undefined") {
      htmx.trigger(scoresInput, "change");
    }
    scoresInput.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function applyVisibleColumns() {
    const visibleColumns = new Set(splitColumns(hiddenInput.value));

    document.querySelectorAll("#issue-table [data-col]").forEach((cell) => {
      cell.classList.toggle("col-hidden", !visibleColumns.has(cell.dataset.col));
    });

    window.localStorage.setItem(storageKey, hiddenInput.value);
    syncScoreColumns(visibleColumns);
  }

  setPickerState(getInitialColumns());
  applyVisibleColumns();

  hiddenInput.addEventListener("change", applyVisibleColumns);
  document.body.addEventListener("htmx:afterSettle", function (event) {
    if (event.detail?.target?.id === "issue-table") {
      applyVisibleColumns();
    }
  });
})();
