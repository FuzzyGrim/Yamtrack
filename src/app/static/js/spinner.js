document.addEventListener('DOMContentLoaded', function() {

  // Get all the import forms
  let forms = document.querySelectorAll(".grid-item");

  // Add event listeners to each form
  forms.forEach(function(form) {
    form.addEventListener("submit", function () {

      let spinner = document.createElement("div");
      spinner.className = "spinner-border spinner-border-sm";
      spinner.setAttribute("role", "status");

      let span = document.createElement("span");
      span.className = "visually-hidden";
      span.textContent = "Loading...";

      spinner.appendChild(span);
      let submitBtn = form.querySelector(".btn-submit");

      // Set the innerHTML of the submit button to the spinner element
      submitBtn.innerHTML = "";
      submitBtn.appendChild(spinner);
    });
  });
});