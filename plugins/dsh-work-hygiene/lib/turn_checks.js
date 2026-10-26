import { detectCalc } from './pure.js';

function safeString(v) {
  if (typeof v === 'string') return v;
  if (v == null) return '';
  try {
    return String(v);
  } catch {
    return '';
  }
}

function isPlainArray(v) {
  return Array.isArray(v);
}

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function extractTextFromContentBlock(block) {
  if (!isPlainObject(block)) return '';
  if (block.type === 'text' && typeof block.text === 'string') {
    return block.text;
  }
  return '';
}

function extractTextFromContent(content) {
  if (typeof content === 'string') return content;
  if (isPlainArray(content)) {
    const parts = [];
    for (const block of content) {
      const t = extractTextFromContentBlock(block);
      if (t) parts.push(t);
    }
    return parts.join('');
  }
  if (isPlainObject(content)) {
    if (typeof content.text === 'string') return content.text;
    if (isPlainArray(content.content)) {
      const parts = [];
      for (const block of content.content) {
        const t = extractTextFromContentBlock(block);
        if (t) parts.push(t);
      }
      return parts.join('');
    }
  }
  return '';
}

export function extractTurnText(events) {
  let userText = '';
  let assistantText = '';

  if (!isPlainArray(events)) {
    return { userText, assistantText };
  }

  for (const ev of events) {
    if (!isPlainObject(ev)) continue;
    const type = ev.type;
    const data = ev.data;

    if (type === 'user/message') {
      if (isPlainObject(data)) {
        if (typeof data.content === 'string') {
          userText += data.content;
        } else if (isPlainObject(data.message) && typeof data.message.content === 'string') {
          userText += data.message.content;
        } else {
          const t = extractTextFromContent(data.content);
          if (t) userText += t;
        }
      }
    } else if (type === 'assistant/message') {
      if (isPlainObject(data)) {
        if (isPlainObject(data.message)) {
          const t = extractTextFromContent(data.message.content);
          if (t) assistantText += t;
        }
        if (typeof data.content === 'string') {
          assistantText += data.content;
        } else {
          const t = extractTextFromContent(data.content);
          if (t) assistantText += t;
        }
      }
    }
  }

  return { userText, assistantText };
}

function parseArguments(args) {
  if (typeof args === 'string') {
    try {
      const parsed = JSON.parse(args);
      return isPlainObject(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }
  if (isPlainObject(args)) return args;
  return null;
}

export function hasNumToolCall(events, numToolNames = ['num.py']) {
  if (!isPlainArray(events)) return false;
  if (!isPlainArray(numToolNames)) numToolNames = ['num.py'];

  const names = numToolNames.filter((n) => typeof n === 'string' && n.length > 0);
  if (names.length === 0) return false;

  for (const ev of events) {
    if (!isPlainObject(ev)) continue;
    if (ev.type !== 'tool/call') continue;
    const data = ev.data;
    if (!isPlainObject(data)) continue;

    if (typeof data.name === 'string' && names.includes(data.name)) {
      return true;
    }

    const args = parseArguments(data.arguments);
    if (args && typeof args.command === 'string') {
      const cmd = args.command;
      for (const n of names) {
        if (cmd.includes(n)) {
          return true;
        }
      }
    }
  }

  return false;
}

export function checkTurn(events, config) {
  if (!isPlainObject(config)) return [];
  if (config.enabled === false) return [];

  const features = config.features;
  if (!isPlainObject(features)) return [];
  const calc = features.calc;
  if (!isPlainObject(calc) || calc.enabled !== true) return [];

  const { userText, assistantText } = extractTurnText(events);
  const text = userText + assistantText;
  const usedNumTool = hasNumToolCall(events);

  try {
    const decision = detectCalc(text, { usedNumTool });
    if (isPlainArray(decision)) return decision;
    if (decision == null) return [];
    return [decision];
  } catch {
    return [];
  }
}

export function planTurnCommands(config) {
  const result = [];
  if (!isPlainObject(config)) return result;

  const features = config.features;
  if (!isPlainObject(features)) return result;

  const order = ['layout', 'issues', 'repro'];
  for (const key of order) {
    const f = features[key];
    if (!isPlainObject(f)) continue;
    if (f.enabled !== true) continue;
    if (typeof f.command !== 'string' || f.command.length === 0) continue;
    result.push({ feature: key, command: f.command });
  }

  return result;
}
