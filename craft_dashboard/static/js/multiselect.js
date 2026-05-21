/**
 * Custom multi-select dropdown component.
 *
 * Replaces native <select multiple> with a dropdown that shows checkboxes,
 * chips for selected items, and proper sizing. Modeled after the sd-tools
 * "Roots" filter pattern.
 *
 * Usage: add class="multiselect" to a container div with:
 *   data-hidden="<id of hidden input to sync>"
 *   Child .multiselect__input-wrap (clickable area)
 *   Child .multiselect__dropdown > .multiselect__options > label.multiselect__option
 */
(function () {
  document.querySelectorAll(".multiselect").forEach(initMultiselect);

  function initMultiselect(container) {
    const inputWrap = container.querySelector(".multiselect__input-wrap");
    const dropdown = container.querySelector(".multiselect__dropdown");
    const options = container.querySelectorAll(".multiselect__option input");
    const placeholder = container.querySelector(".multiselect__placeholder");
    const chipsContainer = container.querySelector(".multiselect__chips");
    const hiddenId = container.dataset.hidden;
    const hiddenInput = document.getElementById(hiddenId);

    function getSelected() {
      return Array.from(options)
        .filter((cb) => cb.checked)
        .map((cb) => cb.value);
    }

    function updateChips() {
      const selected = getSelected();
      chipsContainer.innerHTML = "";

      if (selected.length === 0) {
        placeholder.style.display = "";
        container.classList.remove("has-selection");
      } else {
        placeholder.style.display = "none";
        container.classList.add("has-selection");
        selected.forEach((val) => {
          const chip = document.createElement("span");
          chip.className = "multiselect__chip";
          chip.innerHTML =
            '<span class="multiselect__chip-text">' +
            val +
            "</span>" +
            '<button type="button" class="multiselect__chip-remove" data-value="' +
            val +
            '">&times;</button>';
          chipsContainer.appendChild(chip);
        });
      }
    }

    function syncHidden() {
      const selected = getSelected();
      hiddenInput.value = selected.join(",");
      htmx.trigger(hiddenInput, "change");
    }

    // Toggle dropdown on click
    inputWrap.addEventListener("click", function (e) {
      if (e.target.closest(".multiselect__chip-remove")) return;
      const isOpen = !dropdown.classList.contains("u-hide");
      closeAll();
      if (!isOpen) {
        dropdown.classList.remove("u-hide");
        container.classList.add("is-open");
      }
    });

    // Handle chip removal
    chipsContainer.addEventListener("click", function (e) {
      const removeBtn = e.target.closest(".multiselect__chip-remove");
      if (!removeBtn) return;
      const val = removeBtn.dataset.value;
      options.forEach((cb) => {
        if (cb.value === val) cb.checked = false;
      });
      updateChips();
      syncHidden();
    });

    // Handle checkbox changes
    options.forEach((cb) => {
      cb.addEventListener("change", function () {
        updateChips();
        syncHidden();
      });
    });

    // Initialize state
    updateChips();
  }

  // Close all dropdowns when clicking outside
  function closeAll() {
    document.querySelectorAll(".multiselect").forEach((ms) => {
      ms.querySelector(".multiselect__dropdown").classList.add("u-hide");
      ms.classList.remove("is-open");
    });
  }

  document.addEventListener("click", function (e) {
    if (!e.target.closest(".multiselect")) {
      closeAll();
    }
  });
})();
