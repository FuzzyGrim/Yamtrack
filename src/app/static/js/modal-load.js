var openModalButtons = document.querySelectorAll(".open-modal-button");

openModalButtons.forEach(function (button) {
  button.addEventListener("click", function () {
    // get the data attribute from the clicked element
    var type = this.getAttribute("data-type");
    var id = this.getAttribute("data-id");

    if (this.hasAttribute("data-season-number")) {
      var url =
        "/edit/?media_type=" +
        type +
        "&media_id=" +
        id +
        "&season_number=" +
        this.getAttribute("data-season-number");
      var form_id =
        type + "_" + id + "_" + this.getAttribute("data-season-number");
    } else {
      var url = "/edit/?media_type=" + type + "&media_id=" + id;
      var form_id = type + "_" + id;
    }
    var xhr = new XMLHttpRequest();
    xhr.onload = function () {
      if (xhr.status === 200) {
        // parse the JSON response
        var media = JSON.parse(xhr.responseText);

        if (Object.keys(media).length !== 0) {
          document.querySelector(`#form-${form_id} input[name='score']`).value =
            media.score;
          document.querySelector(
            `#form-${form_id} select[name='status']`
          ).value = media.status;
          document.querySelector(
            `#form-${form_id} input[name='progress']`
          ).value = media.progress;
          document.querySelector(`#form-${form_id} input[name='start']`).value =
            media.start_date;
          document.querySelector(`#form-${form_id} input[name='end']`).value =
            media.end_date;
          
          // Add delete button if it doesn't exist
          if (
            document.querySelector(
              `#modal-${form_id} .modal-footer .btn-danger`
            ) === null
          ) {
            var form = document.querySelector(
              `#modal-${form_id} .modal-footer form`
            );
            var deleteBtn = document.createElement("button");
            deleteBtn.className = "btn btn-danger";
            deleteBtn.type = "submit";
            deleteBtn.name = "delete";
            deleteBtn.innerHTML = "Delete";
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
