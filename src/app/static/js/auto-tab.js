document.addEventListener("DOMContentLoaded", function () {
  // get hash from url
  var hash = location.hash.replace(/^#\//, "");
  if (hash) {
    var button = document.querySelector(
      '#v-pills-tab button[data-bs-target="#' + hash + '"]'
    );
    var tab = new bootstrap.Tab(button);
    tab.show();

    var media = hash.split("-")[0];
    document.querySelector("#" + media + "-tab").classList.add("highlight");
  } else {
    var defaultTabButton = document.querySelector(
      '#v-pills-tab button[data-bs-target="#tv"]'
    );
    var defaultTab = new bootstrap.Tab(defaultTabButton);
    defaultTab.show();
  }

  document
    .querySelectorAll("#v-pills-tab button:not(.dropdown-toggle)")
    .forEach(function (button) {
      button.addEventListener("click", function (event) {
        event.preventDefault();
        // change hash after click for page-reload
        var target = button.dataset.bsTarget.replace(/^#/, "");
        window.location.hash = "#/" + target;

        // Remove the "highlight" class from all tab link elements
        document
          .querySelectorAll("#v-pills-tab button")
          .forEach(function (button) {
            button.classList.remove("highlight");
          });

        // highlight media type if hash includes to status
        var media = target.split("-")[0];
        document.querySelector("#" + media + "-tab").classList.add("highlight");
      });
    });
});
