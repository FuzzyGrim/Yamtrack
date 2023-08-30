let openModalButtons = document.querySelectorAll(".open-modal-button");

openModalButtons.forEach(function (button) {
  button.addEventListener("click", function () {
    // get the data attribute from the clicked element
    let type = this.getAttribute("data-type");
    let id = this.getAttribute("data-id");
    let url = "/modal_data?media_type=" + type + "&media_id=" + id;
    let form_id = type + "_" + id;

    if (this.hasAttribute("data-season-number")) {
      url += "&season_number=" + this.getAttribute("data-season-number"); 
      form_id += "_" + this.getAttribute("data-season-number");
    }

    // Check if the form has already been loaded
    let formElement = document.querySelector(`#modal-${form_id} .modal-body form`);
    if (formElement !== null) {
      return; // Exit the function if the form has already been loaded
    }
  
    let xhr = new XMLHttpRequest();
    xhr.onload = function () {
      if (xhr.status === 200) {
        // parse the JSON response
        let form_data = JSON.parse(xhr.responseText);

        // Add the form to the modal
        document.querySelector(`#modal-${form_id} .modal-body`).innerHTML = form_data.html;

        // Add delete button if the media is already being tracked
        if (form_data.allow_delete) {
          console.log("Adding delete button");
          let form = document.querySelector(`#modal-${form_id} .modal-footer`);
          let deleteBtn = document.createElement("button");
          deleteBtn.className = "btn btn-danger";
          deleteBtn.type = "submit";
          deleteBtn.name = "delete";
          deleteBtn.innerHTML = "Delete";
          deleteBtn.setAttribute("form", `form-${form_id}`);
          form.appendChild(deleteBtn);
        }
      } 
      else {
        console.error("Request failed. Returned status of " + xhr.status);
      }
    };
    xhr.open("GET", url);
    xhr.send();
  });
});
