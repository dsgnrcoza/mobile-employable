(function () {
  "use strict";

  // Same live checklist as signup.js, matching auth.validate_password()'s
  // rules exactly -- duplicated rather than shared since each page's
  // password field has its own id/markup and this is only a few lines.
  var passwordInput = document.getElementById("new_password");
  var reqItems = document.querySelectorAll("#password-reqs li");
  var RULES = {
    length: function (pw) { return pw.length >= 8; },
    upper: function (pw) { return /[A-Z]/.test(pw); },
    lower: function (pw) { return /[a-z]/.test(pw); },
    number: function (pw) { return /[0-9]/.test(pw); },
    special: function (pw) { return /[^A-Za-z0-9]/.test(pw); },
  };
  if (passwordInput && reqItems.length) {
    passwordInput.addEventListener("input", function () {
      var pw = passwordInput.value;
      reqItems.forEach(function (li) {
        var rule = RULES[li.dataset.rule];
        li.classList.toggle("is-met", !!(rule && rule(pw)));
      });
    });
  }
})();
