(function() {
  document.addEventListener("alpine:init", () => {
    Alpine.data("activityCalendar", (config) =>
      window.floppyCalendarState({
        ...config,
        calendarMode: "activity",
      }),
    );

    Alpine.data("sessionHistory", (config) => {
      const calendar = window.floppyCalendarState({
        ...config,
        calendarMode: "activity",
      });
      return {
        ...calendar,
        suppressSync: false,
        observer: null,
        reducedMotion: false,

        init() {
          calendar.init.call(this);
          this.reducedMotion = window.matchMedia(
            "(prefers-reduced-motion: reduce)",
          ).matches;
          this.$nextTick(() => this.observeDays());
        },

        destroy() {
          this.observer?.disconnect();
        },

        calendarDayIsInteractive(cell) {
          const dateKey = this.dateKey(cell);
          return (
            this.calendarDayHasActivity(cell) &&
            (!this.hasSelectableMarkerSet ||
              this.selectableMarkerDays.has(dateKey))
          );
        },

        calendarDayIsSelected(cell) {
          return this.selectedDate === this.dateKey(cell);
        },

        calendarDayTabIndex(cell) {
          return this.calendarDayIsInteractive(cell) ? 0 : -1;
        },

        setMonthFromDate(dateValue) {
          const match = /^(\d{4})-(\d{2})-\d{2}$/.exec(dateValue || "");
          if (!match) {
            return;
          }
          const year = Number(match[1]);
          const month = Number(match[2]) - 1;
          if (year !== this.viewYear || month !== this.viewMonth) {
            this.viewYear = year;
            this.viewMonth = month;
          }
        },

        observeDays() {
          this.observer?.disconnect();
          const list = this.$refs.list;
          if (!list || typeof IntersectionObserver === "undefined") {
            return;
          }
          this.observer = new IntersectionObserver(
            (observed) => {
              if (this.suppressSync) {
                return;
              }
              const visible = observed
                .filter(
                  (entry) =>
                    entry.isIntersecting && entry.target.dataset.dayDate,
                )
                .sort(
                  (left, right) =>
                    left.target.getBoundingClientRect().top -
                    right.target.getBoundingClientRect().top,
                );
              const topDay = visible[0]?.target.dataset.dayDate;
              if (!topDay) {
                return;
              }
              this.selectedDate = topDay;
              this.setMonthFromDate(topDay);
              this.$el.querySelector('[data-date-time-picker-root]')?.dispatchEvent(
                new CustomEvent('activity-calendar-sync', { detail: topDay }),
              );
            },
            { root: list, rootMargin: "0px 0px -85% 0px" },
          );
          list.querySelectorAll("[data-day-date]").forEach((header) => {
            this.observer.observe(header);
          });
        },

        selectCalendarDay(cell) {
          if (!this.calendarDayIsInteractive(cell)) {
            return;
          }
          this.selectedDate = this.dateKey(cell);
          this.scrollToDay(this.selectedDate);
        },

        scrollToDay(dateValue) {
          const target = this.$refs.list?.querySelector(
            `[data-day-date="${dateValue}"]`,
          );
          if (!target) {
            return;
          }
          this.suppressSync = true;
          let fallback;
          const release = () => {
            this.suppressSync = false;
            if (fallback) {
              window.clearTimeout(fallback);
            }
          };
          target.addEventListener("scrollend", release, { once: true });
          fallback = window.setTimeout(release, 600);
          target.scrollIntoView({
            block: "start",
            behavior: this.reducedMotion ? "auto" : "smooth",
          });
        },
      };
    });
  });
})();
