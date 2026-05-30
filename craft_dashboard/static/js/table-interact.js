/**
 * Table interaction: column resize + row expand/collapse.
 *
 * Column resize: drag handles on <th> elements.
 * Row expand: click a row to expand its summary cell.
 */
(function () {
  "use strict";

  // ── Column Resize ──

  function initColumnResize(table) {
    const headers = table.querySelectorAll("th");
    headers.forEach(function (th) {
      // Don't add duplicate handles
      if (th.querySelector(".col-resize-handle")) return;

      const handle = document.createElement("div");
      handle.className = "col-resize-handle";
      th.appendChild(handle);

      let startX, startWidth;

      handle.addEventListener("mousedown", function (e) {
        e.preventDefault();
        e.stopPropagation();
        startX = e.pageX;
        startWidth = th.offsetWidth;
        handle.classList.add("resizing");

        function onMouseMove(e2) {
          const newWidth = startWidth + (e2.pageX - startX);
          if (newWidth >= 40) {
            th.style.width = newWidth + "px";
            // Update corresponding col element if present
            const idx = Array.from(th.parentNode.children).indexOf(th);
            const col = table.querySelector("colgroup col:nth-child(" + (idx + 1) + ")");
            if (col) col.style.width = newWidth + "px";
          }
        }

        function onMouseUp() {
          handle.classList.remove("resizing");
          document.removeEventListener("mousemove", onMouseMove);
          document.removeEventListener("mouseup", onMouseUp);
        }

        document.addEventListener("mousemove", onMouseMove);
        document.addEventListener("mouseup", onMouseUp);
      });
    });
  }

  // ── Row Expand/Collapse ──

  function initRowExpand(table) {
    table.addEventListener("click", function (e) {
      // Don't toggle if clicking a link or button
      if (e.target.closest("a, button, .col-resize-handle")) return;

      const row = e.target.closest("tbody tr");
      if (row) {
        row.classList.toggle("expanded");
      }
    });
  }

  // ── Init ──

  function init() {
    document.querySelectorAll('table[role="grid"]').forEach(function (table) {
      initColumnResize(table);
      initRowExpand(table);
    });
  }

  // Run on load and after HTMX swaps
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // After HTMX swaps (outerHTML), the target element is replaced so we must
  // search the document for the new table rather than looking inside the
  // (now-detached) swap target.
  document.body.addEventListener("htmx:afterSettle", function () {
    document.querySelectorAll('table[role="grid"]').forEach(function (table) {
      // Skip tables that are already initialized
      if (table.dataset.interactInit) return;
      table.dataset.interactInit = "1";
      initColumnResize(table);
      initRowExpand(table);
    });
  });
})();
