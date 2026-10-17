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

    // One definition of each predefined range, used both to apply one and to
    // recognise one. Keeping two copies let them drift: they disagreed on the
    // month-end adjustment, so picking "Last 6 Months" on the 31st produced a
    // range the detector then failed to name.
    rangeBounds(rangeName, today) {
      const start = new Date(today);
      const end = new Date(today);

      switch (rangeName) {
        case "Today":
          return { start, end };

        case "Yesterday":
          start.setDate(start.getDate() - 1);
          return { start, end: new Date(start) };

        case "This Week": {
          // Back to Monday; getDay() is 0 on Sunday.
          const dayOfWeek = today.getDay();
          start.setDate(start.getDate() - (dayOfWeek === 0 ? 6 : dayOfWeek - 1));
          return { start, end };
        }

        case "Last 7 Days":
          start.setDate(start.getDate() - 6); // 6 days ago + today = 7 days
          return { start, end };

        case "This Month":
          return {
            start: new Date(today.getFullYear(), today.getMonth(), 1),
            end,
          };

        case "Last 30 Days":
          start.setDate(start.getDate() - 29); // 29 days ago + today = 30 days
          return { start, end };

        case "Last 90 Days":
          start.setDate(start.getDate() - 89); // 89 days ago + today = 90 days
          return { start, end };

        case "This Year":
          return { start: new Date(today.getFullYear(), 0, 1), end };

        case "Last 6 Months":
          return { start: this.monthsBefore(today, 6), end };

        case "Last 12 Months":
          return { start: this.monthsBefore(today, 12), end };

        case "All Time":
          return null;

        default:
          return { start, end };
      }
    },

    // Stepping back whole months can overshoot: six months before 31 August is
    // 31 February, which rolls forward into March. Clamp to the last day of
    // the month actually intended.
    monthsBefore(today, months) {
      const start = new Date(today);
      start.setMonth(start.getMonth() - months);
      if (start.getDate() !== today.getDate()) {
        start.setDate(0);
      }
      return start;
    },

    isSameDay(first, second) {
      return (
        first.getFullYear() === second.getFullYear() &&
        first.getMonth() === second.getMonth() &&
        first.getDate() === second.getDate()
      );
    },

    updateDatesFromRange(rangeName) {
      const today = new Date();
      // Set time to start of day to avoid timezone issues
      today.setHours(0, 0, 0, 0);

      const bounds = this.rangeBounds(rangeName, today);
      if (!bounds) {
        this.startDate = "all";
        this.endDate = "all";
        return;
      }

      this.startDate = this.formatDateForInput(bounds.start);
      this.endDate = this.formatDateForInput(bounds.end);
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
      if (
        this.parseInputDate(this.endDate) < this.parseInputDate(this.startDate)
      ) {
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
      // The chosen days start and end on this browser's clock, so say which.
      // If the zone cannot be resolved the parameter is left off entirely, so
      // the server applies its own documented fallback rather than us pinning
      // a second copy of that default here.
      let zone;
      try {
        zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      } catch (error) {
        console.warn("Could not resolve the browser timezone:", error);
      }
      if (zone) {
        url.searchParams.set("tz", zone);
      }

      // Navigate to the URL
      window.location.href = url.toString();
    },

    formatDisplayDate(dateString) {
      const date = this.parseInputDate(dateString);
      const format = this.getDateFormat();

      return this.formatDateByDjangoFormat(date, format);
    },

    getDateFormat() {
      const scriptTag = document.querySelector('script[data-date-format]');
      const selectedFormat = scriptTag?.dataset.dateFormat;
      const dateFormats = this.getDateFormatValues();

      if (
        selectedFormat &&
        (!dateFormats.length || dateFormats.includes(selectedFormat))
      ) {
        return selectedFormat;
      }

      return dateFormats[0] || "Y-m-d";
    },

    getDateFormatValues() {
      const formatsElement = document.getElementById("date_format_values");

      if (!formatsElement?.textContent) {
        return [];
      }

      try {
        const dateFormats = JSON.parse(formatsElement.textContent);
        return Array.isArray(dateFormats) ? dateFormats : [];
      } catch {
        return [];
      }
    },

    parseInputDate(dateString) {
      const [year, month, day] = dateString.split("-").map(Number);
      return new Date(year, month - 1, day);
    },

    formatDateByDjangoFormat(date, djangoFormat) {
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      const shortMonth = date.toLocaleString(undefined, { month: "short" });
      const longMonth = date.toLocaleString(undefined, { month: "long" });
      const shortWeekday = date.toLocaleString(undefined, { weekday: "short" });
      const longWeekday = date.toLocaleString(undefined, { weekday: "long" });
      const ordinalSuffix = this.getOrdinalSuffix(date.getDate());

      const formatters = {
        d: () => day,
        D: () => shortWeekday,
        F: () => longMonth,
        j: () => String(date.getDate()),
        l: () => longWeekday,
        m: () => month,
        M: () => shortMonth,
        n: () => String(date.getMonth() + 1),
        S: () => ordinalSuffix,
        y: () => String(year).slice(-2),
        Y: () => String(year),
      };

      let formattedDate = "";
      let isEscaped = false;

      for (const character of djangoFormat) {
        if (isEscaped) {
          formattedDate += character;
          isEscaped = false;
        } else if (character === "\\") {
          isEscaped = true;
        } else {
          formattedDate += formatters[character]?.() ?? character;
        }
      }

      return formattedDate;
    },

    getOrdinalSuffix(day) {
      if (day >= 11 && day <= 13) {
        return "th";
      }

      switch (day % 10) {
        case 1:
          return "st";
        case 2:
          return "nd";
        case 3:
          return "rd";
        default:
          return "th";
      }
    },

    detectRangeFromDates() {
      if (this.startDate === "all" && this.endDate === "all") {
        this.selectedRange = "All Time";
        return;
      }

      const startDate = this.parseInputDate(this.startDate);
      const endDate = this.parseInputDate(this.endDate);
      const today = new Date();
      today.setHours(0, 0, 0, 0);

      // The ranges are checked in the order they are offered, so the first
      // name that reproduces these dates is the one shown.
      const match = this.predefinedRanges.find(({ name }) => {
        const bounds = this.rangeBounds(name, today);
        return (
          bounds &&
          this.isSameDay(startDate, bounds.start) &&
          this.isSameDay(endDate, bounds.end)
        );
      });

      if (match) {
        this.selectedRange = match.name;
        return;
      }

      // Nothing matched, so label it by the dates themselves.
      this.customRangeLabel = `${this.formatDisplayDate(
        this.startDate
      )} - ${this.formatDisplayDate(this.endDate)}`;
      this.selectedRange = this.customRangeLabel;
    },
  };
}
