var openModalButtons = document.querySelectorAll(".open-modal-button");

openModalButtons.forEach(function (button) {
  button.addEventListener("click", function () {
    // get the data-url attribute from the clicked element
    var urlParts = this.getAttribute("data-url").split("_");
    var type = urlParts[0];
    var id = urlParts[1];

    var xhr = new XMLHttpRequest();
    var url = "/edit/?media_type=" + type + "&media_id=" + id;

    xhr.onload = function () {
      if (xhr.status === 200) {
        // parse the JSON response
        var data = JSON.parse(xhr.responseText);
        var output = data.title;
        if (data.year) {
          output += " (" + data.year + ")";
        }

        if (data.original_type) {
          // capitalize first letter
          output += " ["
                 + data.original_type.charAt(0).toUpperCase()
                 + data.original_type.slice(1)
                 + "]";
        } else if (data.media_type) {
          // capitalize first letter
          output += " ["
                 + data.media_type.charAt(0).toUpperCase()
                 + data.media_type.slice(1)
                 + "]";
        }

        // update the modal title and body with the response data
        document.getElementById("modal-title-" + type + "_" + id).innerHTML =
          output;
        document.getElementById("modal-body-" + type + "_" + id).innerHTML =
          data.html;

        if (data.seasons) {
          var select = document.getElementById(
            "season-select-" + type + "_" + id
          );

          new bootstrap.Tooltip(
            document
              .getElementById("season-descriptor-" + type + "_" + id)
              .querySelector('[data-bs-toggle="tooltip"]')
          );

          var score = document.getElementById("score-input-" + type + "_" + id);
          var status = document.getElementById(
            "season-status-" + type + "_" + id
          );
          var progress = document.getElementById(
            "progress-input-" + type + "_" + id
          );
          var start = document.getElementById("start-input-" + type + "_" + id);
          var end = document.getElementById("end-input-" + type + "_" + id);

          select.addEventListener("change", function () {
            var selectedValue = select.value;

            if (selectedValue == "general") {
              score.value = data.score;
              status.value = data.status;
              progress.value = data.progress;
              start.value = data.start_date;
              end.value = data.end_date;
            } else {
              let exists = false;

              // check if season exists in database
              for (
                let i = 0;
                i < data["media_seasons"].length && !exists;
                i++
              ) {
                let media_season = data["media_seasons"][i];
                if (media_season.number == selectedValue) {
                  score.value = media_season.score;
                  status.value = media_season.status;
                  progress.value = media_season.progress;
                  start.value = media_season.start_date;
                  end.value = media_season.end_date;
                  exists = true;
                }
              }

              if (!exists) {
                score.value = "";
                status.value = "Completed";
                progress.value = "";
                start.value = "";
                end.value = "";
              }
            }
          });
        }

        if (data.in_db) {
          // Add delete button if it doesn't exist
          if (
            document.querySelectorAll(
              "#modal-footer-" + type + "_" + id + " .delete-btn"
            ).length == 0
          ) {
            var deleteBtn = document.createElement("button");
            deleteBtn.className = "btn btn-danger delete-btn";
            deleteBtn.type = "submit";
            deleteBtn.name = "delete";
            deleteBtn.innerHTML = "Delete";
            var form = document.querySelector(
              "#modal-footer-" + type + "_" + id + " form"
            );
            form.appendChild(deleteBtn);
          }
        }
      } else {
        console.error("Request failed. Returned status of " + xhr.status);
      }
    };
    xhr.open("GET", url);
    xhr.send();
  });
});
