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

      // Progress unit toggle logic
      if (progressUnitField && progressField) {
        this.progress_unit = progressUnitField.value;
        const maxProgress = parseInt(this.$el.dataset.maxProgress) || 0;

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
            label.textContent = newUnit === 'percentage' ? 'Progress (%)' : `Progress (Pages)`;
          }
        };
      }

      // Get the current time in correct format based on input type
      const now = this.getCurrentDateTime(endDateField);

      // Initial load handling - only auto-fill for new forms
      // For existing records, respect the saved values (even if empty)
      if (
        isNewForm &&
        statusField &&
        statusField.value === "Completed" &&
        endDateField &&
        !endDateField.value
      ) {
        endDateField.value = now;
        this.autoFilled.end_date = true;
      } else if (
        isNewForm &&
        statusField &&
        statusField.value === "In progress" &&
        startDateField &&
        !startDateField.value
      ) {
        startDateField.value = now;
        this.autoFilled.start_date = true;
      }

      // Status change handler
      if (statusField) {
        statusField.addEventListener("change", (e) => {
          const status = e.target.value;

          // Clear previously auto-filled fields when status changes
          if (this.autoFilled.start_date && startDateField) {
            startDateField.value = "";
            this.autoFilled.start_date = false;
          }
          if (this.autoFilled.end_date && endDateField) {
            endDateField.value = "";
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
            endDateField.value = now;
            this.autoFilled.end_date = true;
          } else if (
            status === "In progress" &&
            startDateField &&
            !startDateField.value &&
            !isReturningToOriginalInProgress
          ) {
            startDateField.value = now;
            this.autoFilled.start_date = true;
          }
        });
      }
    },

    getCurrentDateTime(field) {
      const date = new Date();

      if (field.type === 'datetime-local') {
        return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
          .toISOString()
          .slice(0, 16);
      } else if (field.type === 'date') {
        return date.toISOString().slice(0, 10);
      }

      // Fallback to date format
      return date.toISOString().slice(0, 10);
    }
  }));
});

