let progressButtons = document.querySelectorAll(".progress-btn");

progressButtons.forEach(function (button) {
  button.addEventListener("click", function () {
    // get the data attribute from the clicked element
    let type = this.getAttribute("data-type");
    let id = this.getAttribute("data-id");
    let operation = this.getAttribute("data-operation");
    let card_id = type + "_" + id;

    if (this.hasAttribute("data-season-number")) {
      card_id += "_" + this.getAttribute("data-season-number");
    }

    let xhr = new XMLHttpRequest();
    xhr.onload = function () {
      if (xhr.status === 200) {
        const response = JSON.parse(xhr.responseText);
        const progress = response.progress;

        // update progress from card_id .progress_count
        document.querySelector(`#${card_id} .progress_count`).innerHTML = progress;
        const increaseButton = document.querySelector(`#${card_id} [data-operation="increase"]`);
        const decreaseButton = document.querySelector(`#${card_id} [data-operation="decrease"]`);

        // disable both as it will be marked as completed
        if (response.max) {
          increaseButton.disabled = true;
          decreaseButton.disabled = true;
        }
        else if (response.min) {
          increaseButton.disabled = false;
          decreaseButton.disabled = true;
        }
        else {
          increaseButton.disabled = false;
          decreaseButton.disabled = false;
        }
        
      } 
      
      else {
        console.error("Request failed. Returned status of " + xhr.status);
      }
    };
    xhr.open("POST", "/progress_edit");
    const formData = new FormData();
    formData.append("media_id", id);
    formData.append("media_type", type);
    formData.append("operation", operation);
    if (this.hasAttribute("data-season-number")) {
      formData.append("season_number", this.getAttribute("data-season-number"));
    }
    formData.append("csrfmiddlewaretoken", csrftoken);
    xhr.send(formData);
  });
});
