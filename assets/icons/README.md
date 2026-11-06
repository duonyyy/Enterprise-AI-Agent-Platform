# Enterprise Agent Platform — Phase 3 Icon System

**29 editable SVG icons + 145 transparent PNG exports + 4 QA previews**  
**Design:** Concept 03 Portal Monogram / Mineral & Copper  
**Version:** 1.0.0 · **Source:** 29 original SVGs included in the supplied Phase 3 ZIP

## Folder structure

```text
enterprise-agent-platform-icons/
├── svg/
│   ├── navigation/        13 files
│   └── status-actions/    16 files
├── png/
│   ├── 512px/             29 PNGs
│   ├── 128px/             29 PNGs
│   ├── 64px/              29 PNGs
│   ├── 24px/              29 PNGs
│   └── 16px/              29 PNGs
├── previews/
│   ├── all-icons.png
│   ├── light-background.png
│   ├── dark-background.png
│   └── 16px-test.png
├── MANIFEST.json
├── QA_REPORT.csv
├── QA_REPORT.md
└── README.md
```

## Required SVG specifications

- `24 × 24` artwork, `viewBox="0 0 24 24"`
- `stroke-width="1.5"`, round line caps and joins
- `fill="none"`, `stroke="currentColor"` (icon details may override `fill` as already present in the original source)
- True vector shapes; no images or textual labels embedded
- Original SVGs **copied byte-for-byte unchanged** from the provided ZIP
- PNGs have transparent backgrounds with fixed Deep Teal `#0F766E` strokes

## Integration (React / Next.js)

The SVG can be served through the app's public directory:

```tsx
// Move SVG files to public/icons/navigation/... and public/icons/status-actions/...
<img src="/icons/navigation/nav-agents.svg" width={24} height={24} alt="Agents" />
```

**Important:** SVG loaded as an external `<img>` does **not** always inherit CSS `color` through `currentColor`. To dynamically theme icons, use an inline SVG or an SVGR component:

```tsx
// Example with SVGR support enabled and the original 24x24 SVG imported
import NavAgentsIcon from '@/assets/icons/navigation/nav-agents.svg';

export function SidebarLinkIcon() {
  return <NavAgentsIcon width={20} height={20} className="text-teal-700 dark:text-slate-100" aria-hidden />;
}
```

`SVGR` configuration is **not** bundled here: it must be configured in your build system. PNG files are rasterized Deep Teal and therefore do not automatically theme on dark backgrounds. Prefer inline SVGs.

```css
:root { --eap-icon-color: #0F766E; }
[data-theme="dark"] { --eap-icon-color: #EDF8F6; }
.eap-icon { color: var(--eap-icon-color); width: 24px; height: 24px; }
```

## Preview images

- `all-icons.png` — complete 29-icon visual index
- `light-background.png` — icon rendering on `#FAFAF8`
- `dark-background.png` — icon rendering on `#18252A` using light themed `currentColor`
- `16px-test.png` — genuine 16×16 raster previews and a nearest-neighbor pixel zoom for inspection

## Quality assurance and fidelity

All SVGs, PNGs, and non-clipping alpha bounds were validated automatically, see `QA_REPORT.md` and `QA_REPORT.csv`. Some small details at 16px still deserve human recognition testing. The supplied original SVG archive is the authoritative source when the two raster preview images contain discrepancies, so no new geometry was invented.

## License / provenance

These files were supplied by the requester as original design assets. No third-party icon pack is embedded. Redistributing or commercial licensing should follow the owner's rights to the supplied originals.
