/**
 * env-facts: structured environment facts with an as-of stamp.
 *
 * Why: the runtime-context snapshot lists environment facts as prose and carries
 * no timestamp. This plugin contributes one ordered context named "env:facts"
 * whose text is a machine-readable block:
 *
 *   As of 2026-10-25T12:34:56.789Z
 *   <env>
 *   cwd: "C:\\..."
 *   file_policy: danger-full-access
 *   approval_policy: never
 *   gui_url: http://127.0.0.1:3442
 *   session: 019...
 *   </env>
 *
 * Dedupe safety: the snapshot is only re-emitted when its text changes. A naive
 * timestamp would therefore re-emit on every model step. This plugin instead
 * caches per session keyed by the facts payload: the "As of" time is the moment
 * the facts last changed (or were first captured), so the text stays byte-identical
 * while the environment is stable and the snapshot dedupe keeps working.
 */
export const name = "env-facts";

export const inject = ["systemPrompt", "workingDirectory", "sandboxPolicy"];

/** session -> { key, text } ; derived cache, the session log stays the source of truth. */
const CACHE = new WeakMap();

export function apply(ctx) {
  const order = ctx.systemPrompt.getContextOrder("WORKING_DIRECTORY") - 10;
  ctx.effect(() => ctx.systemPrompt.context({
    name: "env:facts",
    order,
    required: true,
    interpolate: false,
    text: (context) => render(ctx, context)
  }));
}

function render(ctx, context) {
  const agent = context?.agent;
  const session = agent?.session;
  if (session === undefined) return "";
  const facts = collect(ctx, session);
  const key = JSON.stringify(facts);
  let cached = CACHE.get(session);
  if (cached === undefined || cached.key !== key) {
    cached = { key, text: format(facts, new Date().toISOString()) };
    CACHE.set(session, cached);
  }
  return cached.text;
}

function collect(ctx, session) {
  const facts = {};
  try {
    facts.cwd = JSON.stringify(ctx.workingDirectory.get(session));
  } catch {
    // optional fact: leave it out rather than failing the assembly
  }
  try {
    facts.file_policy = ctx.sandboxPolicy.resolve({ session }).mode;
  } catch {
    // optional fact
  }
  try {
    const approval = ctx.get("approval");
    const override = approval?.overrideOf(session);
    if (override !== undefined) facts.approval_policy = override;
  } catch {
    // optional fact
  }
  try {
    const webApp = ctx.get("webApp");
    const url = webApp?.getAdvertisedUrl();
    if (typeof url === "string" && url.length > 0) facts.gui_url = url;
  } catch {
    // optional fact
  }
  try {
    facts.session = String(session.id);
  } catch {
    // optional fact
  }
  return facts;
}

function format(facts, asOf) {
  const lines = Object.entries(facts).map(([key, value]) => `${key}: ${value}`);
  return `As of ${asOf}\n<env>\n${lines.join("\n")}\n</env>`;
}
