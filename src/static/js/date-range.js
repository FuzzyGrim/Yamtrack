function dateRangePicker() {
  return {
    isOpen: false,
    activeTab: "predefined",
    selectedRange: "Last 12 Months",
    startDate: new Date(new Date().setFullYear(new Date().getFullYear() - 1))
      .toISOString()
      .split("T")[0],
    endDate: new Date().toISOString().split("T")[0],
    customRangeLabel: "",

    predefinedRanges: [
      { name: "Today" },
      { name: "Yesterday" },
      { name: "This Week" },
      { name: "Last 7 Days" },
      { name: "This Month" },
      { name: "Last 30 Days" },
      { name: "Last 90 Days" },
      { name: "This Year" },
      { name: "Last 6 Months" },
      { name: "Last 12 Months" },
      { name: "All Time" },
    ],

    init() {
      // Initialize dates from URL parameters if they exist
      const urlParams = new URLSearchParams(window.location.search);
      const startDateParam = urlParams.get("start-date");
      const endDateParam = urlParams.get("end-date");

      if (startDateParam && endDateParam) {
        this.startDate = startDateParam;
        this.endDate = endDateParam;

        // Try to determine which predefined range matches these dates
        this.detectRangeFromDates();
      }
    },

    toggleDropdown() {
      this.isOpen = !this.isOpen;
    },

    selectPredefinedRange(rangeName) {
      this.selectedRange = rangeName;
      this.updateDatesFromRange(rangeName);
      this.isOpen = false;
      this.applyDateFilter();
    },

    updateDatesFromRange(rangeName) {
      const today = new Date();
      // Set time to start of day to avoid timezone issues
      today.setHours(0, 0, 0, 0);
      let start = new Date(today);
      let end = new Date(today);

      let shouldFormatDates = true;

      switch (rangeName) {
        case "Today":
          // Both start and end are today
          start = new Date(today);
          end = new Date(today);
          break;

        case "Yesterday":
          // Both start and end are yesterday
          start = new Date(today);
          start.setDate(start.getDate() - 1);
          end = new Date(start);
          break;

        case "This Week":
          // Start from Monday of current week
          const dayOfWeek = today.getDay(); // 0 is Sunday, 1 is Monday, etc.
          const diff = dayOfWeek === 0 ? 6 : dayOfWeek - 1; // Adjust to make Monday the first day
          start = new Date(today);
          start.setDate(start.getDate() - diff);
          end = new Date(today);
          break;

        case "Last 7 Days":
          start = new Date(today);
          start.setDate(start.getDate() - 6); // 6 days ago + today = 7 days
          end = new Date(today);
          break;

        case "This Month":
          // First day of current month to today
          start = new Date(today.getFullYear(), today.getMonth(), 1);
          end = new Date(today);
          break;

        case "Last 30 Days":
          start = new Date(today);
          start.setDate(start.getDate() - 29); // 29 days ago + today = 30 days
          end = new Date(today);
          break;

        case "Last 90 Days":
          start = new Date(today);
          start.setDate(start.getDate() - 89); // 89 days ago + today = 90 days
          end = new Date(today);
          break;

        case "This Year":
          // January 1st of current year to today
          start = new Date(today.getFullYear(), 0, 1);
          end = new Date(today);
          break;

        case "Last 6 Months":
          start = new Date(today);
          start.setMonth(start.getMonth() - 6);
          // If the day doesn't exist in the target month, it will roll over
          if (start.getDate() !== today.getDate()) {
            // If the days don't match, we rolled over to the next month
            // Set to the last day of the previous month
            start = new Date(start.getFullYear(), start.getMonth() + 1, 0);
          }
          end = new Date(today);
          break;

        case "Last 12 Months":
          start = new Date(today);
          start.setFullYear(start.getFullYear() - 1);
          // Handle the same day-of-month issue as with 6 months
          if (start.getDate() !== today.getDate()) {
            start = new Date(start.getFullYear(), start.getMonth() + 1, 0);
          }
          end = new Date(today);
          break;

        case "All Time":
          this.startDate = "all";
          this.endDate = "all";
          shouldFormatDates = false;
          break;
      }

      // Format dates as YYYY-MM-DD strings only if not "All Time"
      if (shouldFormatDates) {
        this.startDate = this.formatDateForInput(start);
        this.endDate = this.formatDateForInput(end);
      }
    },

    formatDateForInput(date) {
      // Format date as YYYY-MM-DD
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    },

    updateDateRange() {
      // Ensure end date is not before start date
      if (new Date(this.endDate) < new Date(this.startDate)) {
        this.endDate = this.startDate;
      }

      this.customRangeLabel = `${this.formatDisplayDate(
        this.startDate
      )} - ${this.formatDisplayDate(this.endDate)}`;
    },

    applyCustomRange() {
      this.selectedRange = this.customRangeLabel;
      this.isOpen = false;
      this.applyDateFilter();
    },

    applyDateFilter() {
      // Create URL with date parameters
      const url = new URL(window.location.href);
      url.searchParams.set("start-date", this.startDate);
      url.searchParams.set("end-date", this.endDate);

      // Navigate to the URL
      window.location.href = url.toString();
    },

    formatDisplayDate(dateString) {
      const date = new Date(dateString);
      // Get format from the script tag's data attribute
      const scriptTag = document.querySelector('script[data-date-format]');
      const format = scriptTag?.dataset.dateFormat || "Y-m-d";

      // Map Django date format to Intl.DateTimeFormat options
      const options = this.getDateFormatOptions(format);
      return date.toLocaleDateString(undefined, options);
    },

    getDateFormatOptions(djangoFormat) {
      // Map common Django format strings to Intl.DateTimeFormat options
      switch (djangoFormat) {
        case "d/m/Y": // European: 18/01/2026
        case "m/d/Y": // US: 01/18/2026
        case "Y-m-d": // ISO: 2026-01-18
          return { year: "numeric", month: "2-digit", day: "2-digit" };
        case "M j, Y": // Long: Jan 18, 2026
          return { year: "numeric", month: "short", day: "numeric" };
        default:
          return { year: "numeric", month: "short", day: "numeric" };
      }
    },

    detectRangeFromDates() {
      // Check for All Time (arbitrary start date)
      if (this.startDate === "all" && this.endDate === "all") {
        this.selectedRange = "All Time";
        return;
      }
      // Parse the current start and end dates
      const startDate = new Date(this.startDate);
      const endDate = new Date(this.endDate);

      // Get today's date with time set to 00:00:00
      const today = new Date();
      today.setHours(0, 0, 0, 0);

      // Helper function to check if two dates are the same day
      const isSameDay = (d1, d2) => {
        return (
          d1.getFullYear() === d2.getFullYear() &&
          d1.getMonth() === d2.getMonth() &&
          d1.getDate() === d2.getDate()
        );
      };

      // Check for Today
      if (isSameDay(startDate, today) && isSameDay(endDate, today)) {
        this.selectedRange = "Today";
        return;
      }

      // Check for Yesterday
      const yesterday = new Date(today);
      yesterday.setDate(yesterday.getDate() - 1);
      if (isSameDay(startDate, yesterday) && isSameDay(endDate, yesterday)) {
        this.selectedRange = "Yesterday";
        return;
      }

      // Check for This Week
      const thisWeekStart = new Date(today);
      const dayOfWeek = today.getDay();
      const diffToMonday = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
      thisWeekStart.setDate(today.getDate() - diffToMonday);
      if (isSameDay(startDate, thisWeekStart) && isSameDay(endDate, today)) {
        this.selectedRange = "This Week";
        return;
      }

      // Check for Last 7 Days
      const last7DaysStart = new Date(today);
      last7DaysStart.setDate(today.getDate() - 6);
      if (isSameDay(startDate, last7DaysStart) && isSameDay(endDate, today)) {
        this.selectedRange = "Last 7 Days";
        return;
      }

      // Check for This Month
      const thisMonthStart = new Date(today.getFullYear(), today.getMonth(), 1);
      if (isSameDay(startDate, thisMonthStart) && isSameDay(endDate, today)) {
        this.selectedRange = "This Month";
        return;
      }

      // Check for Last 30 Days
      const last30DaysStart = new Date(today);
      last30DaysStart.setDate(today.getDate() - 29);
      if (isSameDay(startDate, last30DaysStart) && isSameDay(endDate, today)) {
        this.selectedRange = "Last 30 Days";
        return;
      }

      // Check for Last 90 Days
      const last90DaysStart = new Date(today);
      last90DaysStart.setDate(today.getDate() - 89);
      if (isSameDay(startDate, last90DaysStart) && isSameDay(endDate, today)) {
        this.selectedRange = "Last 90 Days";
        return;
      }

      // Check for This Year
      const thisYearStart = new Date(today.getFullYear(), 0, 1);
      if (isSameDay(startDate, thisYearStart) && isSameDay(endDate, today)) {
        this.selectedRange = "This Year";
        return;
      }

      // Check for Last 6 Months
      // This is more complex due to varying month lengths
      const last6MonthsStart = new Date(today);
      last6MonthsStart.setMonth(today.getMonth() - 6);
      // Adjust for month length differences
      if (last6MonthsStart.getDate() !== today.getDate()) {
        // We rolled over to the next month, adjust to last day of previous month
        last6MonthsStart.setDate(0);
      }

      // Allow a 1-day difference for month-end variations
      const isWithinOneDay = (d1, d2) => {
        const diff = Math.abs(d1.getTime() - d2.getTime());
        return diff <= 86400000; // 24 hours in milliseconds
      };

      if (
        isWithinOneDay(startDate, last6MonthsStart) &&
        isSameDay(endDate, today)
      ) {
        this.selectedRange = "Last 6 Months";
        return;
      }

      // Check for Last 12 Months
      const last12MonthsStart = new Date(today);
      last12MonthsStart.setFullYear(today.getFullYear() - 1);
      // Adjust for month length differences and leap years
      if (last12MonthsStart.getDate() !== today.getDate()) {
        last12MonthsStart.setDate(0);
      }

      if (
        isWithinOneDay(startDate, last12MonthsStart) &&
        isSameDay(endDate, today)
      ) {
        this.selectedRange = "Last 12 Months";
        return;
      }

      // If no match found, use custom range format
      this.customRangeLabel = `${this.formatDisplayDate(
        this.startDate
      )} - ${this.formatDisplayDate(this.endDate)}`;
      this.selectedRange = this.customRangeLabel;
    },
  };
}
