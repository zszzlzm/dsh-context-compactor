// lib/pure.js
// Pure functions for DSH Host plugin pre-execute policy checks.
// No IO, no network, no console, no external imports.

export const LEVELS = Object.freeze({ allow: 0, warn: 1, ask: 2, deny: 3 });

const LEVEL_ORDER = { allow: 0, warn: 1, ask: 2, deny: 3 };

function safeString(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  try { return String(v); } catch { return ''; }
}

function safeArray(v) {
  if (Array.isArray(v)) return v;
  return [];
}

function maxLevel(a, b) {
  const la = LEVEL_ORDER[a] ?? 0;
  const lb = LEVEL_ORDER[b] ?? 0;
  return la >= lb ? a : b;
}

export function mergeDecisions(decisions = []) {
  const list = safeArray(decisions);
  if (list.length === 0) {
    return { level: 'allow', reasons: [] };
  }

  let level = 'allow';
  const reasons = [];

  for (const d of list) {
    if (d == null) continue;
    const lvl = typeof d.level === 'string' ? d.level : 'allow';
    level = maxLevel(level, lvl);
    reasons.push(d);
  }

  return { level, reasons };
}

function stripCode(text) {
  let s = safeString(text);
  // Remove fenced code blocks (``` ... ```)
  s = s.replace(/```[\s\S]*?```/g, ' ');
  // Remove inline code (`...`)
  s = s.replace(/`[^`\n]*`/g, ' ');
  return s;
}

function isVersionLike(str) {
  // v1.2.3, 1.2.3, v1.2, etc.
  return /^v?\d+(\.\d+)+$/.test(str);
}

function isPortLike(str) {
  // 端口 11434, port 8080, etc.
  return /^\d{2,5}$/.test(str);
}

function isPathLike(str) {
  // src/_试作/a.py, /usr/local/bin, C:\Windows
  return /[/\\]/.test(str) || /\.(py|js|ts|sh|bat|cmd|exe|dll|so|dylib)$/i.test(str);
}

export function detectCalc(text, options = {}) {
  if (options && options.usedNumTool === true) {
    return [];
  }

  const clean = stripCode(text);
  if (!clean || clean.trim() === '') {
    return [];
  }

  const decisions = [];
  const seenRules = new Set();

  function addDecision(rule, reason, matches) {
    if (seenRules.has(rule)) return;
    seenRules.add(rule);
    decisions.push({
      module: 'calc',
      rule,
      level: 'warn',
      reason,
      matches: matches || []
    });
  }

  // 1. Four arithmetic operations: +, -, *, /, ×, ÷
  //    But exclude version numbers, ports, paths
  const arithPattern = /(\d+(?:\.\d+)?)\s*[+\-*/×÷]\s*(\d+(?:\.\d+)?)/g;
  let m;
  const arithMatches = [];
  while ((m = arithPattern.exec(clean)) !== null) {
    const full = m[0];
    const left = m[1];
    const right = m[2];
    // A decimal operand (4.8) is not a version number; only skip a full
    // v1.2.3-like token, which has no arithmetic operator anyway.
    if (isPathLike(full)) continue;
    arithMatches.push(full);
  }
  if (arithMatches.length > 0) {
    addDecision('arithmetic', 'Detected arithmetic operation', arithMatches);
  }

  // 2. Percentage: %
  const pctPattern = /(\d+(?:\.\d+)?)\s*%/g;
  const pctMatches = [];
  while ((m = pctPattern.exec(clean)) !== null) {
    pctMatches.push(m[0]);
  }
  if (pctMatches.length > 0) {
    addDecision('percentage', 'Detected percentage calculation', pctMatches);
  }

  // 3. Keywords: 平均, 合计, 差值, 倍, 比例, average, total, difference, ratio
  const keywordPatterns = [
    { rule: 'average', pattern: /平均|average/i, reason: 'Detected average calculation' },
    { rule: 'total', pattern: /合计|total|sum/i, reason: 'Detected total/sum calculation' },
    { rule: 'difference', pattern: /差值|difference/i, reason: 'Detected difference calculation' },
    { rule: 'multiple', pattern: /倍|multiple/i, reason: 'Detected multiple calculation' },
    { rule: 'ratio', pattern: /比例|ratio/i, reason: 'Detected ratio calculation' }
  ];

  for (const kp of keywordPatterns) {
    const re = new RegExp(kp.pattern, 'g');
    const kwMatches = [];
    while ((m = re.exec(clean)) !== null) {
      kwMatches.push(m[0]);
    }
    if (kwMatches.length > 0) {
      addDecision(kp.rule, kp.reason, kwMatches);
    }
  }

  // 4. Unit conversion: value + unit, or explicit conversion wording
  const unitPattern = /\d+(?:\.\d+)?\s*(?:GB|MB|TB|KB|GiB|MiB|kg|g|km|m|cm|mm|小时|分钟|秒|℃|°F)\b/gi;
  const unitMatches = clean.match(unitPattern) || [];
  if (unitMatches.length > 0) {
    addDecision('unit', 'Detected unit conversion', unitMatches);
  }

  // 5. Date/duration difference: time range or explicit duration wording
  const datePattern = /\d{1,2}:\d{2}\s*(?:到|至|~|-)\s*\d{1,2}:\d{2}|相差\s*\d+\s*(?:天|小时|分钟|秒)|日期差|时长差|date\s*diff|duration/i;
  const dateMatches = clean.match(datePattern) || [];
  if (dateMatches.length > 0) {
    addDecision('date-diff', 'Detected date/duration difference', dateMatches);
  }

  return decisions;
}

export function detectCommands(command, options = {}) {
  const cmd = safeString(command).trim();
  if (!cmd) {
    return [];
  }

  const protectedPaths = safeArray(options.protectedPaths);
  const mode = (options && typeof options.mode === 'string') ? options.mode : 'warn';
  const level = LEVEL_ORDER[mode] != null ? mode : 'warn';

  // Check for destructive command patterns
  const destructivePatterns = [
    { pattern: /rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?-?[a-zA-Z]*f[a-zA-Z]*\s+/, label: 'rm -rf' },
    { pattern: /rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)/, label: 'rm -r' },
    { pattern: /Remove-Item/i, label: 'Remove-Item' },
    { pattern: /del\s+\/[a-zA-Z]*f[a-zA-Z]*\s+/, label: 'del /f' },
    { pattern: /del\s+\/[a-zA-Z]*s[a-zA-Z]*\s+/, label: 'del /s' },
    { pattern: /del\s+\/[a-zA-Z]*q[a-zA-Z]*\s+/, label: 'del /q' },
    { pattern: /git\s+reset\s+--hard/i, label: 'git reset --hard' }
  ];

  const matches = [];
  for (const dp of destructivePatterns) {
    if (dp.pattern.test(cmd)) {
      matches.push(dp.label);
    }
  }

  // Check protected paths
  const pathMatches = [];
  for (const pp of protectedPaths) {
    const p = safeString(pp).trim();
    if (p && cmd.includes(p)) {
      pathMatches.push(p);
    }
  }

  // If no destructive pattern matched, return empty
  if (matches.length === 0) {
    return [];
  }

  // Destructive patterns are the only trigger; protectedPaths refine the reason/matches.
  if (matches.length === 0) {
    return [];
  }

  const allMatches = [...matches, ...pathMatches];
  const reason = `Destructive command detected: ${matches.join(', ')}` + 
    (pathMatches.length > 0 ? `; protected paths: ${pathMatches.join(', ')}` : '');

  return [{
    module: 'commands',
    rule: 'destructive-command',
    level,
    reason,
    matches: allMatches
  }];
}

function isDestructiveKind(kind) {
  const k = safeString(kind);
  return k === 'file-write' || k === 'file-delete' || k === 'file-move';
}

export function planSnapshot(op, config = {}) {
  if (op == null) return [];
  const kind = safeString(op.kind);
  const targets = safeArray(op.targets);
  
  if (!isDestructiveKind(kind)) return [];
  if (targets.length === 0) return [];
  
  const snapshotConfig = config && config.snapshot;
  if (!snapshotConfig || snapshotConfig.enabled !== true) return [];
  
  return [{
    kind: 'snapshot',
    target: targets[0],
    reason: `Snapshot before destructive operation: ${kind}`
  }];
}

export function planRecovery(op, config = {}) {
  if (op == null) return [];
  const kind = safeString(op.kind);
  const targets = safeArray(op.targets);
  
  if (!isDestructiveKind(kind)) return [];
  
  const recoveryConfig = config && config.recovery;
  if (!recoveryConfig || recoveryConfig.enabled !== true) return [];
  
  return [{
    kind: 'backup',
    command: recoveryConfig.backupCommand || null,
    targets: targets
  }];
}

export function evaluateCommandCheck(result, check = {}) {
  if (result == null) return [];
  const exitCode = result.exitCode;
  
  if (exitCode === 0) {
    return [];
  }
  
  const name = check && typeof check.name === 'string' ? check.name : 'command';
  const stdout = safeString(result.stdout);
  const stderr = safeString(result.stderr);
  
  let reason = `Command exited with code ${exitCode}`;
  if (stderr) {
    reason += `; stderr: ${stderr.substring(0, 200)}`;
  } else if (stdout) {
    reason += `; stdout: ${stdout.substring(0, 200)}`;
  }
  
  return [{
    module: 'checks',
    rule: name,
    level: 'warn',
    reason,
    matches: []
  }];
}

export { isDestructiveKind };
