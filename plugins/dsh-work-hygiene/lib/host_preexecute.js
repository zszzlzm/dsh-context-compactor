import { normalizeOperation } from './operation.js';
import { arbitrate } from './arbiter.js';

const KIND_RANK = {
  cancel: 4,
  deny: 3,
  ask: 2,
  allow: 1,
};

function safeKind(value) {
  if (value && typeof value === 'object' && typeof value.kind === 'string') {
    return value.kind;
  }
  return 'allow';
}

function safeReason(value) {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object' && typeof value.reason === 'string') return value.reason;
  return '';
}

function safeEffects(value) {
  if (!Array.isArray(value)) return [];
  return value.filter((e) => e !== null && typeof e === 'object');
}

function safeReasons(value) {
  if (!Array.isArray(value)) return [];
  return value.filter((r) => r !== null && typeof r === 'object');
}

function buildAudit(reasons) {
  const list = safeReasons(reasons);
  const audit = [];
  for (const r of list) {
    const level = typeof r.level === 'string' ? r.level : 'info';
    const rule = typeof r.rule === 'string' ? r.rule : 'unknown';
    const reason = typeof r.reason === 'string' ? r.reason : '';
    audit.push({
      module: 'host_preexecute',
      rule,
      level,
      reason,
    });
  }
  return audit;
}

function makeDecision(kind, reason) {
  if (kind === 'deny') {
    return { kind: 'deny', reason: reason || 'denied' };
  }
  if (kind === 'ask') {
    return { kind: 'ask', reason: reason || 'ask' };
  }
  if (kind === 'cancel') {
    return { kind: 'cancel' };
  }
  return { kind: 'allow' };
}

export function createPreExecuteHandler({ config, evaluators = [] } = {}) {
  return async function handle(exec, next = async () => ({ kind: 'allow' })) {
    let downstream;
    try {
      downstream = await next();
    } catch (err) {
      return {
        decision: { kind: 'deny', reason: 'downstream error' },
        effects: [],
        audit: [
          {
            module: 'host_preexecute',
            rule: 'downstream_error',
            level: 'error',
            reason: 'downstream error',
          },
        ],
        operation: null,
      };
    }

    let operation = null;
    try {
      operation = normalizeOperation(exec?.name, exec?.arguments);
    } catch {
      operation = null;
    }

    let result;
    try {
      result = arbitrate(operation, config, evaluators);
    } catch {
      result = {
        level: 'allow',
        effects: [],
        reasons: [],
      };
    }

    const downstreamKind = safeKind(downstream);
    const internalKind = typeof result.level === 'string' ? result.level : 'allow';

    // 下游 cancel 是终端
    if (downstreamKind === 'cancel') {
      return {
        decision: { kind: 'cancel' },
        effects: [],
        audit: buildAudit(result.reasons),
        operation,
      };
    }

    // 下游 deny 是终端（内部不能覆盖）
    if (downstreamKind === 'deny') {
      return {
        decision: { kind: 'deny', reason: safeReason(downstream) || 'denied' },
        effects: [],
        audit: buildAudit(result.reasons),
        operation,
      };
    }

    // 内部 deny 优先于下游 ask/allow
    if (internalKind === 'deny') {
      return {
        decision: { kind: 'deny', reason: safeReason(result) || 'denied' },
        effects: [],
        audit: buildAudit(result.reasons),
        operation,
      };
    }

    // 内部 cancel 视为 deny（内部不应产生 cancel，但安全起见）
    if (internalKind === 'cancel') {
      return {
        decision: { kind: 'cancel' },
        effects: [],
        audit: buildAudit(result.reasons),
        operation,
      };
    }

    // 内部 ask 优先于下游 allow
    if (internalKind === 'ask') {
      return {
        decision: { kind: 'ask', reason: safeReason(result) || 'ask' },
        effects: safeEffects(result.effects),
        audit: buildAudit(result.reasons),
        operation,
      };
    }

    // 下游 ask 优先于内部 allow
    if (downstreamKind === 'ask') {
      return {
        decision: { kind: 'ask', reason: safeReason(downstream) || 'ask' },
        effects: safeEffects(result.effects),
        audit: buildAudit(result.reasons),
        operation,
      };
    }

    // 内部 warn 映射为 allow 但写审计
    if (internalKind === 'warn') {
      return {
        decision: { kind: 'allow' },
        effects: safeEffects(result.effects),
        audit: buildAudit(result.reasons),
        operation,
      };
    }

    // 默认 allow
    return {
      decision: { kind: 'allow' },
      effects: safeEffects(result.effects),
      audit: buildAudit(result.reasons),
      operation,
    };
  };
}
