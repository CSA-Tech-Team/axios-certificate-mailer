import assert from "node:assert/strict";
import { calculateFittedFontSize } from "../dist/fit-math.js";

function renderedWidth(widthAtBaseSize, baseSize, fittedSize) {
  return widthAtBaseSize * (fittedSize / baseSize);
}

const baseSize = 64;

// Short values should retain the Figma typography.
assert.equal(calculateFittedFontSize(baseSize, 805, 420), baseSize);

// Representative long participant and college names must remain inside their blanks.
const longNameWidth = 1580;
const fittedNameSize = calculateFittedFontSize(baseSize, 805, longNameWidth);
assert.ok(fittedNameSize < baseSize);
assert.ok(renderedWidth(longNameWidth, baseSize, fittedNameSize) <= 805);

const longCollegeWidth = 3180;
const fittedCollegeSize = calculateFittedFontSize(baseSize, 1097, longCollegeWidth);
assert.ok(fittedCollegeSize < baseSize);
assert.ok(renderedWidth(longCollegeWidth, baseSize, fittedCollegeSize) <= 1097);

// Even unusually extreme input is calculated to fit rather than spilling out.
const extremeWidth = 12000;
const extremeSize = calculateFittedFontSize(baseSize, 805, extremeWidth);
assert.ok(renderedWidth(extremeWidth, baseSize, extremeSize) <= 805);

console.log("Autofit calculations keep short, long, and extreme values inside their fields.");
