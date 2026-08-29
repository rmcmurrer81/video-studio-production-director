# Third-party notices

## PDF.js

This repository vendors the browser-facing `pdf.mjs` and `pdf.worker.mjs` distribution files from Mozilla PDF.js so attached PDF stories and screenplays can be converted to text locally in the user's browser.

- Project: Mozilla PDF.js
- Vendored distribution version: 6.2.108
- Upstream: https://github.com/mozilla/pdf.js
- License: Apache License 2.0
- Vendored license: `web/vendor/pdfjs/LICENSE`
- Vendored upstream readme: `web/vendor/pdfjs/UPSTREAM-README.md`
- `pdf.mjs` SHA-256: `487bde1bcf89e041f791173d0509a1dc18d0feb6655d78395e1611f9da0de17d`
- `pdf.worker.mjs` SHA-256: `1a7607f28cfbc63f0e4e0a41927c89f991e353e4f3fb4565ecfd621ac5975089`

PDF.js is used only for local text extraction. The contest application does not upload the PDF bytes, perform OCR, or pretend that a scanned/image-only PDF was read.
