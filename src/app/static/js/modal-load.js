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
        var media = JSON.parse(xhr.responseText);

        if (Object.keys(media).length !== 0) {
          document.getElementById("score-" + type + "_" + id).value = media.score;
          document.getElementById("status-" + type + "_" + id).value = media.status;
          document.getElementById("progress-" + type + "_" + id).value = media.progress;
          document.getElementById("start-" + type + "_" + id).value = media.start_date;
          document.getElementById("end-" + type + "_" + id).value = media.end_date;

          // Add delete button if it doesn't exist
          if (
            document.querySelectorAll(
              "#modal-footer-" + type + "_" + id + " .btn-danger"
            ).length == 0
          ) {
            var deleteBtn = document.createElement("button");
            deleteBtn.className = "btn btn-danger";
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
