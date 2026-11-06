# Quality assurance report — Enterprise Agent Platform Icons

## Automated validation

- **29/29** source SVGs match the requested filenames. SHA-256 hashes in `MANIFEST.json` are computed from the provided ZIP, and the SVG files are copied **byte-for-byte unchanged**.
- **29/29** SVGs have `width="24"`, `height="24"`, `viewBox="0 0 24 24"`, `stroke="currentColor"`, `stroke-width="1.5"`, `stroke-linecap="round"`, `stroke-linejoin="round"`, and `fill="none"`.
- **29/29** SVGs are parsed as vector XML; no embedded image, text element, external script, foreignObject, or filter was found.
- **145/145** transparent PNG exports successfully rasterized at 512, 128, 64, 24, 16 px, each from the *same* original SVG for that icon. The stroke color in PNG exports is fixed to `#0F766E` while SVG remains `currentColor`.
- **145/145** PNGs have nonempty alpha and no nontransparent pixels at any image edge (no output-boundary clipping).
- The light-background preview uses `#FAFAF8`; the dark-background preview uses `#18252A`. SVG icons are shown in light ink (`#EDF8F6`) on dark because `currentColor` is intended to follow the app theme.

## 16px caveats & source-reference fidelity

- `16px-test.png` shows **native 16×16 output** next to a pixel-magnified diagnostic rendition. Magnification is not another icon design.
- Exact visual recognition at 16px is **not guaranteed by automated QA**. Pay closer attention to `nav-agents`, `nav-knowledge`, `nav-settings`, `nav-tools`, `nav-skills`: they use multiple fine or intersecting strokes. If UX review demands simplification, that would be a *new variant* requiring approval, not an unannounced modification of the originals.
- Some icons intentionally share silhouettes in the source: `status-published`, `run-succeeded`, `action-approve` use a circle and tick; `run-failed` and `action-reject` use a circle and cross. They remain separate files for semantic naming.
- The supplied screenshots are visual references and may not be perfectly congruent with the source vector files at pixel level. Per the request to **prioritize provided original SVGs**, each original SVG is retained without geometry modifications. For example, the ZIP original `nav-overview` uses an asymmetric four-tile grid; some reference previews show equal tiles. `run-waiting-approval` uses an exclamation mark in the supplied SVG and the main board, while the separate 16px reference preview resembles a clock. The source SVG is treated as canonical rather than guessing the visual geometry.
- No objective user-study / human 16px recognition test is claimed. Validate usage in the live sidebar and status views before declaring final UI acceptance.

Details for all 29 icons are in `QA_REPORT.csv` (SHA-256, bounding boxes, alpha pixel counts).
