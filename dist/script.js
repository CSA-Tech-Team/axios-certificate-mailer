(function () {
  "use strict";

  const nameInput = document.getElementById("certificate-name");
  const collegeInput = document.getElementById("certificate-college");

  function setInputValue(input, value) {
    if (value === undefined || value === null) return;
    input.value = String(value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setValues(values) {
    const next = values || {};
    setInputValue(nameInput, next.name);
    setInputValue(collegeInput, next.college);
    return getValues();
  }

  function getValues() {
    return {
      name: nameInput.value,
      college: collegeInput.value,
    };
  }

  function reset() {
    nameInput.value = "";
    collegeInput.value = "";
    return getValues();
  }

  window.Certificate = Object.freeze({ setValues, getValues, reset });
  window.fillCertificate = setValues;

  const params = new URLSearchParams(window.location.search);
  setValues({
    name: params.has("name") ? params.get("name") : undefined,
    college: params.has("college") ? params.get("college") : undefined,
  });
})();
