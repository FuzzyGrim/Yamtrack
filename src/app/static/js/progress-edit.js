var progressButtons = document.querySelectorAll(".progress-btn");

progressButtons.forEach(function (button) {
  button.addEventListener("click", function () {
    // get the data attribute from the clicked element
    var type = this.getAttribute("data-type");
    var id = this.getAttribute("data-id");
    var operation = this.getAttribute("data-operation");
    var div_id = type + "_" + id;

    if (this.hasAttribute("data-season-number")) {
      div_id += "_" + this.getAttribute("data-season-number");
    }

    var xhr = new XMLHttpRequest();
    xhr.onload = function () {
      if (xhr.status === 200) {
        const response = JSON.parse(xhr.responseText);
        const progress = response.progress;

        // update progress from div_id .progress_count
        document.querySelector(`#${div_id} .progress_count`).innerHTML = progress;
        const incrementButton = document.querySelector(`#${div_id} [data-operation="increment"]`);
        const decrementButton = document.querySelector(`#${div_id} [data-operation="decrement"]`);

        if (response.max) {
          incrementButton.disabled = true;
        }
        else {
          incrementButton.disabled = false;
        }

        if (response.min) {
          decrementButton.disabled = true;
        }
        else {
          decrementButton.disabled = false;
        }
        
      } 
      
      else {
        console.error("Request failed. Returned status of " + xhr.status);
      }
    };
    xhr.open("POST", "/progress_edit");
    const formData = new FormData();
    formData.append("media_type", type);
    formData.append("media_id", id);
    formData.append("operation", operation);
    if (this.hasAttribute("data-season-number")) {
      formData.append("season_number", this.getAttribute("data-season-number"));
    }
    formData.append("csrfmiddlewaretoken", csrftoken);
    xhr.send(formData);
  });
});
