/**
 * Wizard flow definition — DATA, not scattered literals.
 *
 * The generic 7-step wizard flow is declared here once. Specs iterate over
 * these declarations instead of hardcoding a step list in each test, so the
 * step order, count and optionality live in exactly one place.
 *
 * Adapted from the structure of `endo-onboarding.spec.mjs` (upstream
 * f095444) — same shape of env-parameterized walkthrough, but
 * operator-agnostic and driven by a declared step model.
 *
 * Contract this module encodes (the thing the UI must satisfy):
 *   - exactly 7 steps, in the declared order
 *   - each step exposes a role-based heading and a testid-stable container
 *   - Advanced (index 4) is the ONLY optional step and is skippable
 *   - gating is per-step: a step's Next control stays disabled until that
 *     step's requirements are met
 *   - the terminal Live step carries post-deploy evidence
 */

/** @typedef {{id: string, title: string, optional: boolean, testId: string}} WizardStep */

/**
 * Canonical generic 7-step flow, in declared order.
 * `optional: true` marks the single skippable step (Advanced).
 * @type {readonly WizardStep[]}
 */
export const WIZARD_STEPS = Object.freeze([
	Object.freeze({ id: 'discover', title: 'Discover', optional: false, testId: 'step-discover' }),
	Object.freeze({ id: 'connect', title: 'Connect', optional: false, testId: 'step-connect' }),
	Object.freeze({ id: 'uplink', title: 'Uplink', optional: false, testId: 'step-uplink' }),
	Object.freeze({ id: 'payouts', title: 'Payouts', optional: false, testId: 'step-payouts' }),
	Object.freeze({ id: 'advanced', title: 'Advanced', optional: true, testId: 'step-advanced' }),
	Object.freeze({ id: 'deploy', title: 'Deploy', optional: false, testId: 'step-deploy' }),
	Object.freeze({ id: 'live', title: 'Live', optional: false, testId: 'step-live' }),
]);

/** Declared step count — the single source of truth for "7". */
export const STEP_COUNT = WIZARD_STEPS.length;

/** Declared order of step ids, for cheap equality assertions. */
export const STEP_ORDER = Object.freeze(WIZARD_STEPS.map((s) => s.id));

/** Declared order of step titles, for cheap equality assertions. */
export const STEP_TITLES = Object.freeze(WIZARD_STEPS.map((s) => s.title));

/** The only optional step, or null if the definition declares none. */
export const OPTIONAL_STEP = WIZARD_STEPS.find((s) => s.optional) || null;

/** The terminal step (must exist and must not be optional). */
export const TERMINAL_STEP = WIZARD_STEPS[WIZARD_STEPS.length - 1];

/**
 * Per-step requirements, as data. `nextRequires` lists the logical field ids
 * that must be satisfied before that step's forward control may enable.
 * These are logical names (not selectors) so the spec stays about behaviour.
 * @type {Readonly<Record<string, readonly string[]>>}
 */
export const STEP_REQUIREMENTS = Object.freeze({
	discover: Object.freeze([]),
	connect: Object.freeze(['router']),
	uplink: Object.freeze(['router', 'uplinkMode']),
	payouts: Object.freeze(['lightningAddress']),
	advanced: Object.freeze([]),
	deploy: Object.freeze(['router', 'lightningAddress']),
	live: Object.freeze([]),
});

/** Post-deploy evidence the terminal Live step must present. */
export const LIVE_EVIDENCE = Object.freeze([
	'routerReachable',
	'tollgateApiServing',
	'captivePortalServing',
]);

/**
 * Playwright testid selector for a step container.
 * @param {WizardStep} step
 * @returns {string}
 */
export function stepSelector(step) {
	return `[data-testid="${step.testId}"]`;
}

/**
 * Build a Playwright title filter for a single step, so each step case is
 * independently runnable (`--grep`). Kept here so the exact filter strings are
 * generated from the same data the assertions use.
 * @param {WizardStep} step
 * @returns {string}
 */
export function stepTitleFilter(step) {
	return `step ${step.title}`;
}
