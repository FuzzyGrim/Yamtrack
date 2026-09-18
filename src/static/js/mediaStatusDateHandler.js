// Guard against re-registering when this script re-runs after an HTMX body swap.
if (!window.__mediaFormRegistered) {
  window.__mediaFormRegistered = true;
  document.addEventListener("alpine:init", () => {
  Alpine.data("mediaForm", () => ({
    autoFilled: {
      start_date: false,
      end_date: false,
    },
    // Track original values to detect intentionally empty dates
    original: {
      status: null,
      start_date: null,
      end_date: null,
    },

    init() {
      const statusField = this.$el.querySelector('[name="status"]');
      const endDateField = this.$el.querySelector('[name="end_date"]');
      const startDateField = this.$el.querySelector('[name="start_date"]');
      const instanceIdField = this.$el.querySelector('[name="instance_id"]');
      const progressField = this.$el.querySelector('[name="progress"]');
      const progressUnitField = this.$el.querySelector('[name="progress_unit"]');
      const mediaTypeField = this.$el.querySelector('[name="media_type"]');

      // Check if this is a new form (no instance_id) vs editing existing record
      const isNewForm = !instanceIdField || !instanceIdField.value;

      // Store original values for edit forms
      if (!isNewForm) {
        this.original.status = statusField?.value || null;
        this.original.start_date = startDateField?.value || null;
        this.original.end_date = endDateField?.value || null;
      }
      this.syncOptionalDateField(startDateField);
      this.syncOptionalDateField(endDateField);

      // Progress unit toggle logic
      if (progressUnitField && progressField) {
        this.progress_unit = progressUnitField.value;
        const maxProgress = parseInt(this.$el.dataset.maxProgress) || 0;
        const unitNamePlural = this.$el.dataset.progressUnitName || "";

        this.toggleProgressUnit = () => {
          const oldUnit = this.progress_unit;
          const newUnit = oldUnit === 'pages' ? 'percentage' : 'pages';
          const currentValue = parseInt(progressField.value) || 0;

          if (maxProgress > 0) {
            let newValue;
            if (newUnit === 'percentage') {
              // pages -> percentage
              newValue = Math.round((currentValue / maxProgress) * 100);
              progressField.max = 100;
            } else {
              // percentage -> pages
              newValue = Math.round((currentValue / 100) * maxProgress);
              progressField.max = maxProgress;
            }
            progressField.value = Number.isNaN(newValue) ? 0 : newValue;
          }

          this.progress_unit = newUnit;
          progressUnitField.value = newUnit;

          // Update label suffix via custom event or direct DOM manipulation
          const label = this.$el.querySelector(`label[for="${progressField.id}"]`);
          if (label) {
            label.textContent =
              newUnit === "percentage"
                ? "Progress (%)"
                : `Progress (${unitNamePlural})`;
          }
        };
      }

      // Initial load handling - only auto-fill for new forms
      // For existing records, respect the saved values (even if empty)
      if (
        isNewForm &&
        statusField &&
        statusField.value === "Completed" &&
        endDateField &&
        !endDateField.value
      ) {
        this.setDateFieldValue(endDateField, this.getCurrentDateTime(endDateField));
        this.autoFilled.end_date = true;
      } else if (
        isNewForm &&
        statusField &&
        statusField.value === "In progress" &&
        startDateField &&
        !startDateField.value
      ) {
        this.setDateFieldValue(
          startDateField,
          this.getCurrentDateTime(startDateField),
        );
        this.autoFilled.start_date = true;
      }

      // Status change handler
      if (statusField) {
        statusField.addEventListener("change", (e) => {
          const status = e.target.value;

          // Clear previously auto-filled fields when status changes
          if (this.autoFilled.start_date && startDateField) {
            this.setDateFieldValue(startDateField, "");
            this.autoFilled.start_date = false;
          }
          if (this.autoFilled.end_date && endDateField) {
            this.setDateFieldValue(endDateField, "");
            this.autoFilled.end_date = false;
          }

          // For edit forms: don't auto-fill if returning to original status
          // where the date was intentionally left empty
          const isReturningToOriginalCompleted =
            status === "Completed" &&
            this.original.status === "Completed" &&
            this.original.end_date === null;

          const isReturningToOriginalInProgress =
            status === "In progress" &&
            this.original.status === "In progress" &&
            this.original.start_date === null;

          // Set new dates based on new status
          if (
            status === "Completed" &&
            endDateField &&
            !endDateField.value &&
            !isReturningToOriginalCompleted
          ) {
            this.setDateFieldValue(
              endDateField,
              this.getCurrentDateTime(endDateField),
            );
            this.autoFilled.end_date = true;
          } else if (
            status === "In progress" &&
            startDateField &&
            !startDateField.value &&
            !isReturningToOriginalInProgress
          ) {
            this.setDateFieldValue(
              startDateField,
              this.getCurrentDateTime(startDateField),
            );
            this.autoFilled.start_date = true;
          }

          this.syncOptionalDateField(startDateField);
          this.syncOptionalDateField(endDateField);
        });
      }
    },

    setDateFieldValue(field, value) {
      if (!field) {
        return;
      }

      field.value = value;
      this.syncOptionalDateField(field);

      // WebKit can keep stale validity state after scripted datetime changes.
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("change", { bubbles: true }));
    },

    syncOptionalDateField(field) {
      if (!field) {
        return;
      }

      field.required = false;
      field.removeAttribute("required");
      field.setCustomValidity("");

      const value = field.value;
      field.defaultValue = "";
      field.value = value;
    },

    getCurrentDateTime(field) {
      const now = new Date();
      const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);

      if (field.type === "datetime-local") {
        return local.toISOString().slice(0, 16);
      }

      // "date" type and fallback both use the local date.
      return local.toISOString().slice(0, 10);
    },
  }));
  });
}
