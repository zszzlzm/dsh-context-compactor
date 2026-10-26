// lib/operation.js

const KIND_MAP = {
  read: 'file-read',
  read_image: 'file-read',
  write: 'file-write',
  edit: 'file-write',
  str_replace_editor: 'file-write',
  delete: 'file-delete',
  remove: 'file-delete',
  rm: 'file-delete',
  move: 'file-move',
  mv: 'file-move',
  pwsh: 'command',
  bash: 'command',
  shell: 'command',
  run_code: 'command',
};

function safeString(value) {
  if (typeof value === 'string') return value;
  return '';
}

function safeArray(value) {
  if (Array.isArray(value)) {
    return value.filter((v) => typeof v === 'string');
  }
  return [];
}

export function normalizeOperation(toolName, args = {}) {
  const safeToolName = safeString(toolName);
  const safeArgs = (args && typeof args === 'object' && !Array.isArray(args)) ? args : {};

  const kind = KIND_MAP[safeToolName] || 'unknown';

  let targets = [];
  if (kind === 'file-move') {
    const source = safeString(safeArgs.source);
    const destination = safeString(safeArgs.destination);
    targets = [source, destination];
  } else {
    const filePath = safeString(safeArgs.file_path);
    const path = safeString(safeArgs.path);
    const target = filePath || path;
    if (target) {
      targets = [target];
    }
  }

  const command = safeString(safeArgs.command);

  return {
    kind,
    toolName: safeToolName,
    targets,
    command,
  };
}

export function isDestructiveKind(kind) {
  return kind === 'file-write' || kind === 'file-delete' || kind === 'file-move';
}
