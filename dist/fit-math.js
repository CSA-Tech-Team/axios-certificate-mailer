/**
 * Calculate a font size that keeps one line of text inside a fixed width.
 * The one-pixel floor only affects pathological inputs; normal long names
 * remain comfortably readable.
 */
export function calculateFittedFontSize(
  baseFontSize,
  availableWidth,
  measuredTextWidth,
  safetyFactor = 0.96,
) {
  if (![baseFontSize, availableWidth, measuredTextWidth].every(Number.isFinite)) {
    throw new TypeError("Font size and widths must be finite numbers.");
  }

  if (baseFontSize <= 0 || availableWidth <= 0 || measuredTextWidth <= 0) {
    return Math.max(1, baseFontSize);
  }

  const scale = Math.min(1, (availableWidth * safetyFactor) / measuredTextWidth);
  return Math.max(1, baseFontSize * scale);
}
