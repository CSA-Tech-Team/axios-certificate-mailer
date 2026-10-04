# DTP certificate — Frame 47

Static HTML implementation of Figma Frame 47 with script-fillable participant and college fields.

## Autofill

Run this in the browser after the page loads:

```js
window.fillCertificate({
  name: "Aarav Kumar",
  college: "PSG College of Technology",
});
```

The same API is available as `window.Certificate.setValues(...)`. Current values can be read with `window.Certificate.getValues()` and cleared with `window.Certificate.reset()`.

The fields can also be populated through query parameters:

```text
?name=Aarav%20Kumar&college=PSG%20College%20of%20Technology
```

For DOM automation, the inputs are `#certificate-name` and `#certificate-college`.
