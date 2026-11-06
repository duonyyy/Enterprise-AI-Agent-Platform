/**
 * Enterprise AI Agent Platform — Brand & Asset Registry
 * Concept 03: Portal Monogram / Mineral & Copper
 * Sources: assets/brand/ & assets/icons/
 */

export const BRAND_COLORS = {
  canvas: '#FAFAF8',
  surface: '#FFFFFF',
  graphite: '#18252A',
  teal: '#0F766E',
  copper: '#C2410C',
  border: '#E2E7E4',
} as const;

export const BRAND_LOGOS = {
  // SVG Vector Logos
  horizontalLightSvg: '/brand/logo-horizontal-light.svg',
  horizontalDarkSvg: '/brand/logo-horizontal-dark.svg',
  symbolColorSvg: '/brand/logo-symbol-color.svg',
  symbolWhiteSvg: '/brand/logo-symbol-white.svg',
  symbolBlackSvg: '/brand/logo-symbol-black.svg',
  symbolRefToneSvg: '/brand/logo-symbol-reference-tone.svg',

  // Raster PNG Logos
  horizontalLightPng: '/brand/logo-horizontal-light.png',
  horizontalDarkPng: '/brand/logo-horizontal-dark.png',
  symbolColorPng: '/brand/logo-symbol-color.png',
  symbolWhitePng: '/brand/logo-symbol-white.png',
  symbolBlackPng: '/brand/logo-symbol-black.png',

  // Favicons & App Icons
  faviconIco: '/favicon.ico',
  favicon16: '/brand/favicon-16.png',
  favicon32: '/brand/favicon-32.png',
  favicon48: '/brand/favicon-48.png',
  favicon64: '/brand/favicon-64.png',
  favicon192: '/brand/favicon-192.png',
  favicon512: '/brand/favicon-512.png',
  appIcon192: '/brand/app-icon-192.png',
  appIcon512: '/brand/app-icon-512.png',
} as const;

export const NAV_ICONS = {
  agents: '/icons/navigation/nav-agents.svg',
  approvals: '/icons/navigation/nav-approvals.svg',
  business: '/icons/navigation/nav-business.svg',
  evaluations: '/icons/navigation/nav-evaluations.svg',
  knowledge: '/icons/navigation/nav-knowledge.svg',
  mcp: '/icons/navigation/nav-mcp.svg',
  overview: '/icons/navigation/nav-overview.svg',
  runs: '/icons/navigation/nav-runs.svg',
  settings: '/icons/navigation/nav-settings.svg',
  skills: '/icons/navigation/nav-skills.svg',
  tools: '/icons/navigation/nav-tools.svg',
  workflows: '/icons/navigation/nav-workflows.svg',
  workspace: '/icons/navigation/nav-workspace.svg',
} as const;

export const STATUS_ACTION_ICONS = {
  actionApprove: '/icons/status-actions/action-approve.svg',
  actionCompare: '/icons/status-actions/action-compare.svg',
  actionPublish: '/icons/status-actions/action-publish.svg',
  actionReject: '/icons/status-actions/action-reject.svg',
  actionRollback: '/icons/status-actions/action-rollback.svg',
  runCancelled: '/icons/status-actions/run-cancelled.svg',
  runFailed: '/icons/status-actions/run-failed.svg',
  runRunning: '/icons/status-actions/run-running.svg',
  runSucceeded: '/icons/status-actions/run-succeeded.svg',
  runWaitingApproval: '/icons/status-actions/run-waiting-approval.svg',
  safetyQuarantine: '/icons/status-actions/safety-quarantine.svg',
  safetyShield: '/icons/status-actions/safety-shield.svg',
  statusArchived: '/icons/status-actions/status-archived.svg',
  statusDraft: '/icons/status-actions/status-draft.svg',
  statusImmutable: '/icons/status-actions/status-immutable.svg',
  statusPublished: '/icons/status-actions/status-published.svg',
} as const;
