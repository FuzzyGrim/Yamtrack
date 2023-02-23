document.addEventListener('DOMContentLoaded', function() {

  // Get all the import forms
  var forms = document.querySelectorAll(".grid-item");

  // Add event listeners to each form
  forms.forEach(function(form) {
    form.addEventListener("submit", function () {

      var spinner = document.createElement("div");
      spinner.className = "spinner-border spinner-border-sm";
      spinner.setAttribute("role", "status");

      var span = document.createElement("span");
      span.className = "visually-hidden";
      span.textContent = "Loading...";

      spinner.appendChild(span);
      var submitBtn = form.querySelector(".btn-submit");

      // Set the innerHTML of the submit button to the spinner element
      submitBtn.innerHTML = "";
      submitBtn.appendChild(spinner);
    });
  });
});