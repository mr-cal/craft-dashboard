/**
 * Client-side table sorting functionality
 * Handles sorting by column headers with visual indicators
 */

document.addEventListener('DOMContentLoaded', function() {
  // Find all sortable tables
  const sortableTables = document.querySelectorAll('table[data-sortable]');
  
  sortableTables.forEach(table => {
    const headers = table.querySelectorAll('thead th');
    
    headers.forEach((header, index) => {
      header.style.cursor = 'pointer';
      header.addEventListener('click', function() {
        sortTable(table, index);
      });
    });
  });
});

function sortTable(table, columnIndex) {
  const tbody = table.querySelector('tbody');
  if (!tbody) return;
  const rows = Array.from(tbody.querySelectorAll('tr'));
  
  // Get the current sort state from the table
  let currentSortColumn = table.dataset.sortColumn;
  let currentSortDirection = table.dataset.sortDirection || 'asc';
  
  // Check if clicking the same column
  if (currentSortColumn === String(columnIndex)) {
    // Toggle direction
    currentSortDirection = currentSortDirection === 'asc' ? 'desc' : 'asc';
  } else {
    // New column, start with ascending
    currentSortDirection = 'asc';
  }
  
  // Store the sort state
  table.dataset.sortColumn = String(columnIndex);
  table.dataset.sortDirection = currentSortDirection;
  
  // Sort rows
  rows.sort((a, b) => {
    const aCell = a.cells[columnIndex];
    const bCell = b.cells[columnIndex];
    
    let aValue = aCell.textContent.trim();
    let bValue = bCell.textContent.trim();
    
    // Try to parse as numbers for numeric columns
    const aNum = parseFloat(aValue);
    const bNum = parseFloat(bValue);
    
    let comparison = 0;
    if (!isNaN(aNum) && !isNaN(bNum)) {
      // Numeric comparison
      comparison = aNum - bNum;
    } else {
      // String comparison
      comparison = aValue.localeCompare(bValue);
    }
    
    // Apply direction
    return currentSortDirection === 'asc' ? comparison : -comparison;
  });
  
  // Update tbody with sorted rows
  rows.forEach(row => tbody.appendChild(row));
  
  // Update arrow indicators
  updateSortIndicators(table, columnIndex, currentSortDirection);
}

function updateSortIndicators(table, sortedColumn, direction) {
  const headers = table.querySelectorAll('thead th');
  
  headers.forEach((header, index) => {
    // Remove existing arrows
    const arrow = header.querySelector('.sort-arrow');
    if (arrow) {
      arrow.remove();
    }
    
    // Add arrow to sorted column
    if (index === sortedColumn) {
      const arrowSpan = document.createElement('span');
      arrowSpan.className = 'sort-arrow';
      arrowSpan.textContent = direction === 'asc' ? ' ↑' : ' ↓';
      header.appendChild(arrowSpan);
    }
  });
}
