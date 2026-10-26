import { checkTurn, planTurnCommands } from './turn_checks.js';

export function collectTurnEvents(session, turn) {
  let events;
  try {
    if (!session || typeof session.ownEvents !== 'function') {
      return [];
    }
    events = session.ownEvents();
  } catch {
    return [];
  }

  if (!Array.isArray(events)) {
    return [];
  }

  let startIdx = -1;
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (ev && ev.type === 'turn/start' && ev.data && ev.data.turn === turn) {
      startIdx = i;
      break;
    }
  }

  if (startIdx === -1) {
    return [];
  }

  let endIdx = -1;
  for (let i = startIdx + 1; i < events.length; i++) {
    const ev = events[i];
    if (ev && ev.type === 'turn/end') {
      endIdx = i;
      break;
    }
  }

  if (endIdx === -1) {
    return [];
  }

  return events.slice(startIdx, endIdx + 1);
}

export function handleTurnEnd(session, event, config) {
  try {
    if (!event || event.type !== 'turn/end') {
      return null;
    }

    const turn = event.data?.turn;
    const events = collectTurnEvents(session, turn);
    const decisions = checkTurn(events, config);
    const commands = planTurnCommands(config);

    return { turn, events, decisions, commands };
  } catch {
    return null;
  }
}
