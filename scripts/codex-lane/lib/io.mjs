import fs from 'node:fs/promises';
import path from 'node:path';

export async function ensureDirectory(dirPath) {
  await fs.mkdir(dirPath, { recursive: true });
}

export async function readJson(filePath) {
  const raw = await fs.readFile(filePath, 'utf8');
  return JSON.parse(raw);
}

export async function writeJson(filePath, value) {
  await ensureDirectory(path.dirname(filePath));
  await fs.writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

export async function writeText(filePath, value) {
  await ensureDirectory(path.dirname(filePath));
  await fs.writeFile(filePath, value, 'utf8');
}

export async function appendJsonl(filePath, value) {
  await ensureDirectory(path.dirname(filePath));
  await fs.appendFile(filePath, `${JSON.stringify(value)}\n`, 'utf8');
}

export function nowIso() {
  return new Date().toISOString();
}

export function makeRunId(date = new Date()) {
  const pad = (value) => String(value).padStart(2, '0');
  return [
    date.getUTCFullYear(),
    '-',
    pad(date.getUTCMonth() + 1),
    '-',
    pad(date.getUTCDate()),
    '_',
    pad(date.getUTCHours()),
    pad(date.getUTCMinutes()),
    pad(date.getUTCSeconds()),
  ].join('');
}
