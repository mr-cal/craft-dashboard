/**
 * Client-side table pagination functionality.
 * Automatically paginates tables with `data-paginate="<page_size>"` (default 20).
 */

document.addEventListener('DOMContentLoaded', function () {
  initTablePagination();
});

function initTablePagination() {
  const tables = document.querySelectorAll('table[data-paginate]');

  tables.forEach((table) => {
    const pageSize = parseInt(table.dataset.paginate, 10) || 20;
    const tbody = table.querySelector('tbody');
    if (!tbody) return;

    let currentPage = 1;

    function getRows() {
      return Array.from(tbody.querySelectorAll('tr'));
    }

    function render() {
      const rows = getRows();
      const totalRows = rows.length;
      if (totalRows <= pageSize) {
        rows.forEach((r) => {
          r.style.display = '';
        });
        const existingNav = table.nextElementSibling;
        if (existingNav && existingNav.classList.contains('table-pagination')) {
          existingNav.remove();
        }
        return;
      }

      const totalPages = Math.ceil(totalRows / pageSize);
      if (currentPage > totalPages) currentPage = totalPages;
      if (currentPage < 1) currentPage = 1;

      const startIndex = (currentPage - 1) * pageSize;
      const endIndex = Math.min(startIndex + pageSize, totalRows);

      rows.forEach((row, idx) => {
        row.style.display = idx >= startIndex && idx < endIndex ? '' : 'none';
      });

      // Update or create pagination controls
      let nav = table.nextElementSibling;
      if (!nav || !nav.classList.contains('table-pagination')) {
        nav = document.createElement('div');
        nav.className = 'table-pagination';
        table.parentNode.insertBefore(nav, table.nextSibling);
      }

      nav.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 0.75rem; padding: 0.25rem 0; flex-wrap: wrap; gap: 0.5rem;">
          <span class="p-text--small p-text--muted table-pagination__info">
            Showing ${startIndex + 1}–${endIndex} of ${totalRows} issues
          </span>
          <nav aria-label="Pagination" style="display: inline-flex; gap: 0.25rem; align-items: center;">
            <button type="button" class="p-button--neutral is-small btn-prev" ${currentPage === 1 ? 'disabled style="opacity:0.5;cursor:not-allowed;"' : ''}>&laquo; Previous</button>
            <span class="p-text--small" style="padding: 0 0.5rem;">Page <strong>${currentPage}</strong> of <strong>${totalPages}</strong></span>
            <button type="button" class="p-button--neutral is-small btn-next" ${currentPage === totalPages ? 'disabled style="opacity:0.5;cursor:not-allowed;"' : ''}>Next &raquo;</button>
          </nav>
        </div>
      `;

      nav.querySelector('.btn-prev')?.addEventListener('click', () => {
        if (currentPage > 1) {
          currentPage--;
          render();
        }
      });

      nav.querySelector('.btn-next')?.addEventListener('click', () => {
        if (currentPage < totalPages) {
          currentPage++;
          render();
        }
      });
    }

    render();

    // Re-render when table is sorted
    table.addEventListener('table-sorted', () => {
      render();
    });
  });
}
