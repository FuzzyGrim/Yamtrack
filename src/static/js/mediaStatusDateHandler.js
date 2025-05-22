document.addEventListener("alpine:init", () => {
  Alpine.data("mediaForm", () => ({
    // Track which fields were auto-filled
    autoFilled: {
      start_date: false,
      end_date: false,
    },

    init() {
      const statusField = this.$el.querySelector('[name="status"]');
      const endDateField = this.$el.querySelector('[name="end_date"]');
      const startDateField = this.$el.querySelector('[name="start_date"]');
      const now = new Date(
        new Date().getTime() - new Date().getTimezoneOffset() * 60000
      )
        .toISOString()
        .slice(0, 16);

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
  }));
});
