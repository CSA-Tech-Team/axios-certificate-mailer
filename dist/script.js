import { calculateFittedFontSize } from "./fit-math.js";

(function () {
  "use strict";

  const nameInput = document.getElementById("certificate-name");
  const collegeInput = document.getElementById("certificate-college");
  const fields = [nameInput, collegeInput];
  const measurementCanvas = document.createElement("canvas");
  const measurementContext = measurementCanvas.getContext("2d");

  function fitInputText(input) {
    input.style.fontSize = "";
    input.dataset.fitScale = "1";

    if (!input.value || !measurementContext) return;

    const styles = window.getComputedStyle(input);
    const baseFontSize = Number.parseFloat(styles.fontSize);
    const padding =
      Number.parseFloat(styles.paddingLeft) + Number.parseFloat(styles.paddingRight);
    const availableWidth = Math.max(1, input.clientWidth - padding - 2);
    const letterSpacing = Number.parseFloat(styles.letterSpacing) || 0;

    measurementContext.font = [
      styles.fontStyle,
      styles.fontVariant,
      styles.fontWeight,
      `${baseFontSize}px`,
      styles.fontFamily,
    ].join(" ");

    const glyphWidth = measurementContext.measureText(input.value).width;
    const measuredTextWidth =
      glyphWidth + Math.max(0, input.value.length - 1) * letterSpacing;
    const fittedFontSize = calculateFittedFontSize(
      baseFontSize,
      availableWidth,
      measuredTextWidth,
    );

    input.style.fontSize = `${fittedFontSize}px`;
    input.dataset.fitScale = (fittedFontSize / baseFontSize).toFixed(4);
  }

  function fitAllFields() {
    fields.forEach(fitInputText);
  }

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
    fitAllFields();
    return getValues();
  }

  fields.forEach((field) => field.addEventListener("input", () => fitInputText(field)));
  window.addEventListener("resize", fitAllFields);

  if ("ResizeObserver" in window) {
    const resizeObserver = new ResizeObserver(fitAllFields);
    resizeObserver.observe(document.querySelector(".certificate"));
  }

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(fitAllFields);
  }

  window.Certificate = Object.freeze({ setValues, getValues, reset, refit: fitAllFields });
  window.fillCertificate = setValues;

  const params = new URLSearchParams(window.location.search);
  setValues({
    name: params.has("name") ? params.get("name") : undefined,
    college: params.has("college") ? params.get("college") : undefined,
  });

  fitAllFields();
})();
