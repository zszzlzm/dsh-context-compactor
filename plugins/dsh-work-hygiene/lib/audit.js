// lib/audit.js

const LEVEL_MAP = {
  deny: 'deny',
  ask: 'ask',
  warn: 'warn',
  allow: 'allow',
};

function safeString(value) {
  if (typeof value === 'string') return value;
  if (value == null) return '';
  try {
    return String(value);
  } catch {
    return '';
  }
}

function safeArray(value) {
  if (!Array.isArray(value)) return [];
  return value;
}

function truncate(str, max) {
  const s = safeString(str);
  if (s.length <= max) return s;
  return s.slice(0, max);
}

function normalizeLevel(kind) {
  const k = safeString(kind).toLowerCase();
  if (LEVEL_MAP[k]) return LEVEL_MAP[k];
  return 'allow';
}

export function createAuditRecord({ operation, decision, downstream, reasons, effects } = {}) {
  const op = operation && typeof operation === 'object' ? operation : {};
  const dec = decision && typeof decision === 'object' ? decision : {};
  const ds = downstream && typeof downstream === 'object' ? downstream : {};
  const effs = effects && typeof effects === 'object' ? effects : null;

  // level
  const level = normalizeLevel(dec.kind);

  // tool
  const tool = safeString(op.toolName);

  // operation kind
  const operationKind = safeString(op.kind) || 'unknown';

  // targets: max 5 strings
  let targets = [];
  const rawTargets = op.targets;
  if (Array.isArray(rawTargets)) {
    targets = rawTargets
      .map((t) => safeString(t))
      .filter((t) => t !== '')
      .slice(0, 5);
  }

  // effects: array of kind strings
  let effectKinds = [];
  if (Array.isArray(effs)) {
    effectKinds = effs
      .map((e) => (e && typeof e === 'object' ? safeString(e.kind) : safeString(e)))
      .filter((k) => k !== '');
  }

  // rules: from reasons array
  let rules = [];
  const rawReasons = reasons;
  if (Array.isArray(rawReasons)) {
    rules = rawReasons
      .filter((r) => r && typeof r === 'object')
      .map((r) => ({
        module: safeString(r.module),
        rule: safeString(r.rule),
        level: normalizeLevel(r.level),
        reason: truncate(safeString(r.reason).replace(/[\r\n]+/g, ' '), 300),
      }));
  }

  // downstream
  const downstreamKind = safeString(ds.kind) || 'allow';

  return {
    plugin: 'dsh-work-hygiene',
    level,
    tool,
    operation: operationKind,
    targets,
    effects: effectKinds,
    rules,
    downstream: downstreamKind,
  };
}

export function toAuditLine(record) {
  const rec = createAuditRecord(record);
  // Ensure no newlines in any string values
  const cleaned = JSON.parse(JSON.stringify(rec));

  function cleanStrings(obj) {
    if (obj === null || typeof obj !== 'object') {
      if (typeof obj === 'string') {
        return obj.replace(/[\r\n]+/g, ' ');
      }
      return obj;
    }
    if (Array.isArray(obj)) {
      return obj.map(cleanStrings);
    }
    const out = {};
    for (const key of Object.keys(obj)) {
      out[key] = cleanStrings(obj[key]);
    }
    return out;
  }

  const final = cleanStrings(cleaned);
  return JSON.stringify(final);
}
