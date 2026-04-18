import path from 'node:path';
import fs from 'node:fs/promises';
import { ensureDirectory, nowIso, readJson, writeJson, makeRunId } from './io.mjs';

function sanitizeLaneName(laneName) {
  return laneName.trim().replace(/[^a-zA-Z0-9._-]+/g, '-');
}

export function registryRoot(repoRoot) {
  return path.join(repoRoot, '.hermes', 'codex-lanes');
}

export function laneDir(repoRoot, laneName) {
  return path.join(registryRoot(repoRoot), sanitizeLaneName(laneName));
}

export function laneMetaPath(repoRoot, laneName) {
  return path.join(laneDir(repoRoot, laneName), 'lane.json');
}

export async function ensureLane(repoRoot, laneName, metadata = {}) {
  const root = registryRoot(repoRoot);
  const dir = laneDir(repoRoot, laneName);
  const metaPath = laneMetaPath(repoRoot, laneName);
  await ensureDirectory(root);
  await ensureDirectory(path.join(dir, 'runs'));

  let lane = null;
  try {
    lane = await readJson(metaPath);
  } catch (error) {
    if (error.code !== 'ENOENT') {
      throw error;
    }
  }

  if (!lane) {
    const createdAt = nowIso();
    lane = {
      lane_name: sanitizeLaneName(laneName),
      original_lane_name: laneName,
      created_at: createdAt,
      updated_at: createdAt,
      thread_id: null,
      last_run_id: null,
      hermes_refs: {},
      metadata: {},
    };
  }

  lane.updated_at = nowIso();
  lane.hermes_refs = { ...(lane.hermes_refs || {}), ...(metadata.hermes_refs || {}) };
  lane.metadata = { ...(lane.metadata || {}), ...(metadata.metadata || {}) };
  if (metadata.thread_id !== undefined) {
    lane.thread_id = metadata.thread_id;
  }
  if (metadata.last_run_id !== undefined) {
    lane.last_run_id = metadata.last_run_id;
  }
  if (metadata.last_run_status !== undefined) {
    lane.last_run_status = metadata.last_run_status;
  }
  if (metadata.last_run_summary_path !== undefined) {
    lane.last_run_summary_path = metadata.last_run_summary_path;
  }
  if (metadata.last_run_final_path !== undefined) {
    lane.last_run_final_path = metadata.last_run_final_path;
  }

  await writeJson(metaPath, lane);
  return lane;
}

export async function loadLane(repoRoot, laneName) {
  return readJson(laneMetaPath(repoRoot, laneName));
}

export async function createRunArtifacts(repoRoot, laneName, explicitRoot = null) {
  const runId = makeRunId();
  const baseDir = explicitRoot || path.join(laneDir(repoRoot, laneName), 'runs', runId);
  await ensureDirectory(baseDir);
  return {
    runId,
    runDir: baseDir,
    requestPath: path.join(baseDir, 'request.json'),
    promptPath: path.join(baseDir, 'prompt.txt'),
    eventLogPath: path.join(baseDir, 'events.jsonl'),
    summaryPath: path.join(baseDir, 'summary.json'),
    finalPath: path.join(baseDir, 'final.txt'),
  };
}

export async function listLanes(repoRoot) {
  const root = registryRoot(repoRoot);
  await ensureDirectory(root);
  const entries = await fs.readdir(root, { withFileTypes: true });
  const lanes = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) {
      continue;
    }
    const metaPath = path.join(root, entry.name, 'lane.json');
    try {
      lanes.push(await readJson(metaPath));
    } catch (error) {
      if (error.code !== 'ENOENT') {
        throw error;
      }
    }
  }
  lanes.sort((left, right) => String(right.updated_at || '').localeCompare(String(left.updated_at || '')));
  return lanes;
}
