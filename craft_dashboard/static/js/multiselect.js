/**
 * Custom modern multi-select dropdown component.
 *
 * Replaces native <select multiple> with a modern dropdown that shows
 * checkboxes, clean summary labels, search filtering, and action buttons.
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
    const hiddenId = container.dataset.hidden;
    const hiddenInput = document.getElementById(hiddenId);

    // Ensure display elements exist
    let labelWrap = inputWrap.querySelector(".multiselect__label-wrap");
    if (!labelWrap) {
      labelWrap = document.createElement("div");
      labelWrap.className = "multiselect__label-wrap";
      if (placeholder) {
        inputWrap.insertBefore(labelWrap, placeholder);
        labelWrap.appendChild(placeholder);
      } else {
        inputWrap.appendChild(labelWrap);
      }
    }

    let displaySpan = labelWrap.querySelector(".multiselect__display");
    if (!displaySpan) {
      displaySpan = document.createElement("span");
      displaySpan.className = "multiselect__display";
      labelWrap.appendChild(displaySpan);
    }

    let badgeSpan = labelWrap.querySelector(".multiselect__badge");
    if (!badgeSpan) {
      badgeSpan = document.createElement("span");
      badgeSpan.className = "multiselect__badge";
      labelWrap.appendChild(badgeSpan);
    }

    let arrowSpan = inputWrap.querySelector(".multiselect__arrow");
    if (!arrowSpan) {
      arrowSpan = document.createElement("span");
      arrowSpan.className = "multiselect__arrow";
      arrowSpan.setAttribute("aria-hidden", "true");
      inputWrap.appendChild(arrowSpan);
    }

    const allOption = Array.from(options).find(
      (cb) => cb.value === "all-projects" || cb.value === "all"
    );

    // Add search box if more than 5 options
    if (options.length > 5 && !dropdown.querySelector(".multiselect__search-box")) {
      const searchBox = document.createElement("div");
      searchBox.className = "multiselect__search-box";
      const searchInput = document.createElement("input");
      searchInput.type = "search";
      searchInput.className = "multiselect__search-input";
      searchInput.placeholder = "Filter options...";
      searchBox.appendChild(searchInput);
      dropdown.insertBefore(searchBox, dropdown.firstChild);

      searchInput.addEventListener("input", function () {
        const term = searchInput.value.toLowerCase().trim();
        container.querySelectorAll(".multiselect__option").forEach((opt) => {
          const txt = opt.textContent.toLowerCase();
          opt.style.display = txt.includes(term) ? "" : "none";
        });
      });
    }

    function getSelected() {
      return Array.from(options)
        .filter((cb) => cb.checked)
        .map((cb) => ({
          value: cb.value,
          label: (cb.closest("label")?.querySelector("span")?.textContent || cb.closest("label")?.textContent || cb.value).trim(),
        }));
    }

    function updateDisplay() {
      const selected = getSelected();
      if (selected.length === 0) {
        if (placeholder) placeholder.style.display = "";
        displaySpan.textContent = "";
        badgeSpan.style.display = "none";
        container.classList.remove("has-selection");
      } else {
        if (placeholder) placeholder.style.display = "none";
        container.classList.add("has-selection");

        const nonAllOptions = Array.from(options).filter((cb) => cb !== allOption);
        const allSelected = nonAllOptions.length > 0 && selected.length === nonAllOptions.length;

        if (allOption && allOption.checked) {
          displaySpan.textContent = allOption.closest("label")?.textContent.trim() || "All projects";
          badgeSpan.style.display = "none";
        } else if (allSelected) {
          displaySpan.textContent = placeholder ? placeholder.textContent.trim() : "All";
          badgeSpan.style.display = "none";
        } else if (selected.length === 1) {
          displaySpan.textContent = selected[0].label;
          badgeSpan.style.display = "none";
        } else if (selected.length === 2) {
          displaySpan.textContent = `${selected[0].label}, ${selected[1].label}`;
          badgeSpan.style.display = "none";
        } else {
          displaySpan.textContent = `${selected[0].label}, +${selected.length - 1} more`;
          badgeSpan.textContent = String(selected.length);
          badgeSpan.style.display = "inline-flex";
        }
      }
    }

    function syncHidden() {
      const selectedValues = Array.from(options)
        .filter((cb) => cb.checked)
        .map((cb) => cb.value);
      if (hiddenInput) {
        hiddenInput.value = selectedValues.join(",");
        if (typeof htmx !== "undefined") {
          htmx.trigger(hiddenInput, "change");
        }
        hiddenInput.dispatchEvent(new CustomEvent("change", { bubbles: true, detail: { fromMultiselect: true } }));
      }
    }

    // Toggle dropdown on click
    inputWrap.addEventListener("click", function (e) {
      const isOpen = !dropdown.classList.contains("u-hide");
      closeAll();
      if (!isOpen) {
        dropdown.classList.remove("u-hide");
        container.classList.add("is-open");
        inputWrap.setAttribute("aria-expanded", "true");
        const searchInput = dropdown.querySelector(".multiselect__search-input");
        if (searchInput) {
          setTimeout(() => searchInput.focus(), 50);
        }
      }
    });

    // Handle checkbox changes and mutual exclusion with allOption
    options.forEach((cb) => {
      cb.addEventListener("change", function () {
        if (allOption) {
          if (cb === allOption && cb.checked) {
            options.forEach((other) => {
              if (other !== allOption) other.checked = false;
            });
          } else if (cb !== allOption && cb.checked) {
            allOption.checked = false;
          } else if (!cb.checked) {
            const anyChecked = Array.from(options).some((other) => other.checked);
            if (!anyChecked) {
              allOption.checked = true;
            }
          }
        }
        updateDisplay();
        syncHidden();
      });
    });

    // Two-way sync from hidden input changes
    if (hiddenInput) {
      hiddenInput.addEventListener("change", function (e) {
        if (e.detail && e.detail.fromMultiselect) return;
        const vals = (hiddenInput.value || "").split(",").map((s) => s.trim()).filter(Boolean);
        options.forEach((cb) => {
          cb.checked = vals.includes(cb.value);
        });
        updateDisplay();
      });
    }

    // Initialize state
    updateDisplay();

    // Exposed so code that sets the checkboxes directly (e.g. restoring
    // filter state on browser back/forward) can refresh the summary label
    // without dispatching a `change` event, which htmx would turn into an
    // extra table request per control.
    container.refreshMultiselectDisplay = updateDisplay;
  }

  function closeAll() {
    document.querySelectorAll(".multiselect").forEach((ms) => {
      const dropdown = ms.querySelector(".multiselect__dropdown");
      if (dropdown) dropdown.classList.add("u-hide");
      ms.classList.remove("is-open");
      const inputWrap = ms.querySelector(".multiselect__input-wrap");
      if (inputWrap) inputWrap.setAttribute("aria-expanded", "false");
    });
  }

  document.addEventListener("click", function (e) {
    if (!e.target.closest(".multiselect")) {
      closeAll();
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      closeAll();
    }
  });
})();
