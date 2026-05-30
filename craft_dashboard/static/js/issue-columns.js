(function () {
  const storageKey = "visible_columns";
  const defaultColumns = ["issue", "title", "author", "age", "action", "summary"];
  const hiddenInput = document.getElementById("columns-hidden");

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

  function applyVisibleColumns() {
    const visibleColumns = new Set(splitColumns(hiddenInput.value));

    document.querySelectorAll("#issue-table [data-col]").forEach((cell) => {
      cell.classList.toggle("col-hidden", !visibleColumns.has(cell.dataset.col));
    });

    window.localStorage.setItem(storageKey, hiddenInput.value);
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
