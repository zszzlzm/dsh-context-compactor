import { normalizeConfig } from './lib/config.js';
import { createPreExecuteHandler } from './lib/host_preexecute.js';
import { detectCommands } from './lib/pure.js';
import { createAuditRecord } from './lib/audit.js';

export const name = '@local/dsh-work-hygiene';
export const inject = ['tools', 'systemPrompt'];

export function apply(ctx, config = {}) {
  const cfg = normalizeConfig(config);

  if (cfg.enabled !== true) {
    return;
  }

  if (ctx?.systemPrompt?.section) {
    ctx.systemPrompt.section({
      name: 'WORK_HYGIENE',
      order: 900,
      text: '需要计算时必须调用 num.py；需要恢复/备份时必须使用配置的脚本；不要手算或直接删保护文件。',
    });
  }

  const evaluators = [
    (op, c) =>
      op.kind === 'command' && c.features.commands.enabled
        ? detectCommands(op.command, c.features.commands)
        : [],
  ];

  const handler = createPreExecuteHandler({ config: cfg, evaluators });

  if (ctx?.effect && ctx?.on) {
    ctx.effect(() =>
      ctx.on('tools/pre-execute', async (exec, next) => {
        const r = await handler(exec, next);

        if (ctx.logger?.info) {
          const audit = createAuditRecord({
            operation: r.operation,
            decision: r.decision,
            downstream: r.downstream,
            reasons: r.audit,
            effects: r.effects,
          });
          ctx.logger.info(audit);
        }

        return r.decision;
      }),
    );
  }
}
