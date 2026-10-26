// lib/config.js

export const DEFAULT_CONFIG = Object.freeze({
  enabled: true,
  features: Object.freeze({
    calc: Object.freeze({
      enabled: true,
      numToolNames: Object.freeze(['num.py']),
    }),
    layout: Object.freeze({
      enabled: true,
      command: '',
    }),
    issues: Object.freeze({
      enabled: true,
      command: '',
    }),
    repro: Object.freeze({
      enabled: true,
      command: '',
    }),
    recovery: Object.freeze({
      enabled: false,
      backupCommand: '',
      restoreCommand: '',
    }),
    commands: Object.freeze({
      enabled: false,
      mode: 'warn',
      protectedPaths: Object.freeze([]),
    }),
    snapshot: Object.freeze({
      enabled: false,
    }),
    claims: Object.freeze({
      enabled: false,
    }),
    tests: Object.freeze({
      enabled: false,
    }),
    protect: Object.freeze({
      enabled: false,
    }),
    secrets: Object.freeze({
      enabled: false,
    }),
    verify: Object.freeze({
      enabled: false,
    }),
    plan: Object.freeze({
      enabled: false,
    }),
  }),
});

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function toBoolean(value, fallback) {
  if (typeof value === 'boolean') return value;
  return fallback;
}

function toString(value, fallback) {
  if (typeof value === 'string') return value;
  return fallback;
}

function toStringArray(value, fallback) {
  if (!Array.isArray(value)) return fallback.slice();
  const out = [];
  for (const item of value) {
    if (typeof item === 'string') out.push(item);
  }
  return out;
}

function cloneDefaultFeatures() {
  const out = {};
  for (const [key, val] of Object.entries(DEFAULT_CONFIG.features)) {
    out[key] = {};
    for (const [k, v] of Object.entries(val)) {
      if (Array.isArray(v)) {
        out[key][k] = v.slice();
      } else {
        out[key][k] = v;
      }
    }
  }
  return out;
}

export function normalizeConfig(raw = {}) {
  if (!isPlainObject(raw)) {
    return {
      enabled: DEFAULT_CONFIG.enabled,
      features: cloneDefaultFeatures(),
    };
  }

  const result = {
    enabled: toBoolean(raw.enabled, DEFAULT_CONFIG.enabled),
    features: cloneDefaultFeatures(),
  };

  const rawFeatures = isPlainObject(raw.features) ? raw.features : {};

  for (const [featureName, featureDefaults] of Object.entries(DEFAULT_CONFIG.features)) {
    if (!(featureName in rawFeatures)) continue;

    const rawFeature = rawFeatures[featureName];
    if (!isPlainObject(rawFeature)) continue;

    const target = result.features[featureName];

    for (const [key, defaultValue] of Object.entries(featureDefaults)) {
      if (!(key in rawFeature)) continue;

      const rawValue = rawFeature[key];

      if (key === 'enabled') {
        target[key] = toBoolean(rawValue, defaultValue);
      } else if (key === 'command' || key === 'backupCommand' || key === 'restoreCommand') {
        target[key] = toString(rawValue, defaultValue);
      } else if (key === 'numToolNames' || key === 'protectedPaths') {
        target[key] = toStringArray(rawValue, defaultValue);
      } else if (key === 'mode') {
        const allowed = ['warn', 'ask', 'deny'];
        if (typeof rawValue === 'string' && allowed.includes(rawValue)) {
          target[key] = rawValue;
        } else {
          target[key] = defaultValue;
        }
      }
    }
  }

  return result;
}
