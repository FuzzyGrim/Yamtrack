document.addEventListener("alpine:init", () => {
  Alpine.data("mediaForm", () => ({
    autoFilled: {
      start_date: false,
      end_date: false,
    },

    init() {
      const statusField = this.$el.querySelector('[name="status"]');
      const endDateField = this.$el.querySelector('[name="end_date"]');
      const startDateField = this.$el.querySelector('[name="start_date"]');

      // Get the current time in correct format based on input type
      const now = this.getCurrentDateTime(endDateField);

      // Initial load handling
      if (
        statusField &&
        statusField.value === "Completed" &&
        endDateField &&
        !endDateField.value
      ) {
        endDateField.value = now;
        this.autoFilled.end_date = true;
      } else if (
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

          // Set new dates based on new status
          if (status === "Completed" && endDateField && !endDateField.value) {
            endDateField.value = now;
            this.autoFilled.end_date = true;
          } else if (
            status === "In progress" &&
            startDateField &&
            !startDateField.value
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
