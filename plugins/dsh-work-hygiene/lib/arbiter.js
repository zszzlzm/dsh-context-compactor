import { mergeDecisions, planSnapshot, planRecovery } from './pure.js';
import { isDestructiveKind } from './operation.js';

export function arbitrate(operation, config, evaluators = []) {
  const decisions = [];

  if (Array.isArray(evaluators)) {
    for (const evaluator of evaluators) {
      if (typeof evaluator !== 'function') continue;
      try {
        const result = evaluator(operation, config);
        if (result == null) continue;
        if (Array.isArray(result)) {
          for (const d of result) {
            if (d != null) decisions.push(d);
          }
        } else {
          decisions.push(result);
        }
      } catch (error) {
        decisions.push({
          module: 'evaluator',
          rule: 'exception',
          level: 'deny',
          reason: error && error.message ? error.message : String(error),
          matches: [],
        });
      }
    }
  }

  const merged = mergeDecisions(decisions);
  const level = merged && merged.level ? merged.level : 'allow';
  const reasons = Array.isArray(merged && merged.reasons) ? merged.reasons.slice() : [];

  let effects = [];
  if (level !== 'deny') {
    const snapshotCfg = config && config.features && config.features.snapshot ? config.features.snapshot : {};
    const recoveryCfg = config && config.features && config.features.recovery ? config.features.recovery : {};
    const snapshotEffects = planSnapshot(operation, { snapshot: snapshotCfg });
    const recoveryEffects = planRecovery(operation, {
      recovery: {
        ...recoveryCfg,
        backupCommand: recoveryCfg.backupCommand,
      },
    });
    if (Array.isArray(snapshotEffects)) effects = effects.concat(snapshotEffects);
    if (Array.isArray(recoveryEffects)) effects = effects.concat(recoveryEffects);
  }

  return { level, reasons, effects };
}
