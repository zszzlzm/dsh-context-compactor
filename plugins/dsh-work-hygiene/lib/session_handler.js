import { handleTurnEnd } from './session_turn.js';

export function createSessionHandler({ config, logger } = {}) {
  return function handle(session, event) {
    try {
      if (!event || event.type !== 'turn/end') {
        return null;
      }

      const result = handleTurnEnd(session, event, config);

      if (result === null || result === undefined) {
        return null;
      }

      if (result.decisions && result.decisions.length > 0) {
        if (logger && typeof logger.info === 'function') {
          const commands = result.commands
            ? result.commands.map((c) => ({ feature: c?.feature || 'unknown' }))
            : [];
          logger.info({
            turn: result.turn ?? event.turn ?? null,
            decisions: result.decisions,
            commands,
          });
        }
      }

      return result;
    } catch (err) {
      if (logger && typeof logger.warn === 'function') {
        logger.warn({ error: err instanceof Error ? err.message : String(err) });
      }
      return null;
    }
  };
}
