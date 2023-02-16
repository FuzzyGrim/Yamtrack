document.getElementById("import-form").addEventListener("submit", function () {
  var div = document.createElement("div");
  div.className = "d-flex justify-content-center";

  var spinner = document.createElement("div");
  spinner.className = "spinner-border";
  spinner.setAttribute("role", "status");

  var span = document.createElement("span");
  span.className = "visually-hidden";
  span.textContent = "Loading...";

  spinner.appendChild(span);
  div.appendChild(spinner);
  document.querySelector("main").appendChild(div);
});
