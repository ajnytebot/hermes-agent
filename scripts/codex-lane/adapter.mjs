#!/usr/bin/env node
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { appendJsonl, nowIso, readJson, writeJson, writeText } from './lib/io.mjs';
import { createRunArtifacts, ensureLane, listLanes, loadLane } from './lib/lane_registry.mjs';
import { validateRequest } from './lib/schema.mjs';

function parseArgs(argv) {
  const [command, ...rest] = argv;
  let requestPath = null;
  for (let index = 0; index < rest.length; index += 1) {
    if (rest[index] === '--request') {
      requestPath = rest[index + 1] || null;
      index += 1;
    }
  }
  if (!command) {
    throw new Error('Missing adapter command.');
  }
  if (!requestPath) {
    throw new Error('Missing --request <path>.');
  }
  return { command, requestPath };
}

function toRepoRelative(repoRoot, targetPath) {
  return path.relative(repoRoot, targetPath) || '.';
}

async function loadCodexSdk() {
  const override = process.env.HERMES_CODEX_SDK_MODULE;
  if (override) {
    if (override.startsWith('.') || override.startsWith('/') || override.includes(path.sep)) {
      return import(pathToFileURL(path.resolve(override)).href);
    }
    return import(override);
  }
  return import('@openai/codex-sdk');
}

function clampThreadOptions(threadOptions, repoRoot) {
  return {
    workingDirectory: threadOptions.workingDirectory || repoRoot,
    additionalDirectories: Array.isArray(threadOptions.additionalDirectories) ? threadOptions.additionalDirectories : undefined,
    skipGitRepoCheck: threadOptions.skipGitRepoCheck !== false,
    approvalPolicy: threadOptions.approvalPolicy || 'never',
    sandboxMode: threadOptions.sandboxMode || 'workspace-write',
    webSearchMode: threadOptions.webSearchMode || 'disabled',
    modelReasoningEffort: threadOptions.modelReasoningEffort === 'minimal'
      ? 'low'
      : (threadOptions.modelReasoningEffort || undefined),
    config: Array.isArray(threadOptions.config) ? threadOptions.config : undefined,
    model: threadOptions.model || undefined,
  };
}

function buildItemSummary(item) {
  const type = item?.type || 'unknown';
  const summary = { type };
  if (type === 'command_execution') {
    summary.command = item.command ?? null;
    summary.exit_code = item.exit_code ?? null;
    summary.cwd = item.cwd ?? null;
    summary.status = item.status ?? null;
  } else if (type === 'file_change') {
    summary.path = item.path ?? item.file_path ?? null;
    summary.change_type = item.change_type ?? null;
  } else if (type === 'mcp_tool_call') {
    summary.tool_name = item.tool_name ?? item.name ?? null;
    summary.status = item.status ?? null;
  } else if (type === 'todo_list') {
    summary.items = item.items ?? null;
  } else if (type === 'agent_message') {
    summary.text = item.text ?? item.content ?? null;
  }
  return summary;
}

function extractFinalResponseFromItem(item) {
  if (!item || item.type !== 'agent_message') {
    return null;
  }
  if (typeof item.text === 'string' && item.text.trim()) {
    return item.text;
  }
  if (typeof item.content === 'string' && item.content.trim()) {
    return item.content;
  }
  if (Array.isArray(item.content)) {
    const text = item.content
      .map((part) => (typeof part?.text === 'string' ? part.text : ''))
      .join('')
      .trim();
    return text || null;
  }
  return null;
}

async function emitJson(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function isAsyncIterable(value) {
  return Boolean(value && typeof value[Symbol.asyncIterator] === 'function');
}

function resolveTurnEventStream(streamedTurn) {
  if (isAsyncIterable(streamedTurn)) {
    return streamedTurn;
  }
  if (isAsyncIterable(streamedTurn?.events)) {
    return streamedTurn.events;
  }
  throw new Error('Codex SDK runStreamed() did not return an async event stream.');
}

async function handleRunTurn(request) {
  const repoRoot = request.repo_root;
  const laneName = request.lane_name;
  const lane = await ensureLane(repoRoot, laneName, {
    hermes_refs: request.hermes_refs || {},
    metadata: {
      repo_root: repoRoot,
      working_directory: request.thread_options?.workingDirectory || repoRoot,
    },
  });

  const artifacts = await createRunArtifacts(repoRoot, laneName, request.artifacts_dir || null);
  const startedAt = nowIso();
  await writeJson(artifacts.requestPath, request);
  await writeText(artifacts.promptPath, request.prompt);

  const summary = {
    lane_name: lane.lane_name,
    run_id: artifacts.runId,
    status: 'started',
    started_at: startedAt,
    finished_at: null,
    thread_id: lane.thread_id,
    final_response: '',
    usage: null,
    item_types: [],
    commands: [],
    changed_files: [],
    failed_items: [],
    todo_list: null,
    artifacts: {
      run_dir: toRepoRelative(repoRoot, artifacts.runDir),
      prompt_path: toRepoRelative(repoRoot, artifacts.promptPath),
      event_log_path: toRepoRelative(repoRoot, artifacts.eventLogPath),
      summary_path: toRepoRelative(repoRoot, artifacts.summaryPath),
      final_path: toRepoRelative(repoRoot, artifacts.finalPath),
    },
  };

  const itemTypes = new Set();
  const changedFiles = [];
  const commands = [];
  const failedItems = [];
  let finalResponse = '';
  let usage = null;
  let todoList = null;
  let threadId = lane.thread_id || null;

  try {
    const { Codex } = await loadCodexSdk();
    const codex = new Codex();
    const threadOptions = clampThreadOptions(request.thread_options || {}, repoRoot);
    const thread = request.resume !== false && lane.thread_id
      ? codex.resumeThread(lane.thread_id, threadOptions)
      : codex.startThread(threadOptions);

    const streamedTurn = await thread.runStreamed(request.prompt, request.turn_options || {});
    const eventStream = resolveTurnEventStream(streamedTurn);
    for await (const event of eventStream) {
      await appendJsonl(artifacts.eventLogPath, event);
      if (event?.thread_id) {
        threadId = event.thread_id;
      }
      if (event?.type === 'thread.started' && event.thread_id) {
        threadId = event.thread_id;
      }
      if (event?.type === 'turn.completed' && event.usage) {
        usage = event.usage;
      }
      const item = event?.item;
      if (!item) {
        continue;
      }
      itemTypes.add(item.type || 'unknown');
      if (item.type === 'file_change') {
        const itemPath = item.path || item.file_path;
        if (itemPath) {
          changedFiles.push(itemPath);
        }
      }
      if (item.type === 'command_execution') {
        commands.push(buildItemSummary(item));
        if (item.status === 'failed' || item.exit_code) {
          failedItems.push(buildItemSummary(item));
        }
      } else if (item.type === 'mcp_tool_call' && item.status === 'failed') {
        failedItems.push(buildItemSummary(item));
      } else if (item.type === 'todo_list') {
        todoList = item.items || item;
      }
      const maybeFinal = extractFinalResponseFromItem(item);
      if (maybeFinal) {
        finalResponse = maybeFinal;
      }
    }

    summary.status = finalResponse ? 'completed' : 'failed';
    summary.thread_id = threadId;
    summary.final_response = finalResponse;
    summary.usage = usage;
    summary.item_types = Array.from(itemTypes);
    summary.commands = commands;
    summary.changed_files = changedFiles;
    summary.failed_items = failedItems;
    summary.todo_list = todoList;
  } catch (error) {
    summary.status = 'error';
    summary.error = error instanceof Error ? error.message : String(error);
  }

  summary.finished_at = nowIso();
  await writeJson(artifacts.summaryPath, summary);
  await writeText(artifacts.finalPath, summary.final_response || '');

  await ensureLane(repoRoot, laneName, {
    thread_id: summary.thread_id || lane.thread_id || null,
    last_run_id: artifacts.runId,
    last_run_status: summary.status,
    last_run_summary_path: summary.artifacts.summary_path,
    last_run_final_path: summary.artifacts.final_path,
    hermes_refs: request.hermes_refs || {},
    metadata: {
      repo_root: repoRoot,
      working_directory: request.thread_options?.workingDirectory || repoRoot,
      last_started_at: startedAt,
      last_finished_at: summary.finished_at,
    },
  });

  await emitJson(summary);
}

async function main() {
  try {
    const { command, requestPath } = parseArgs(process.argv.slice(2));
    const request = await readJson(requestPath);
    const resolvedCommand = validateRequest(request, command);

    if (resolvedCommand === 'list-lanes') {
      await emitJson({ lanes: await listLanes(request.repo_root) });
      return;
    }

    if (resolvedCommand === 'inspect-lane' || resolvedCommand === 'resume-lane' || resolvedCommand === 'start-lane') {
      const lane = await ensureLane(request.repo_root, request.lane_name, {
        hermes_refs: request.hermes_refs || {},
      });
      await emitJson({ lane });
      return;
    }

    if (resolvedCommand === 'run-turn') {
      await handleRunTurn(request);
      return;
    }

    throw new Error(`Unhandled command '${resolvedCommand}'.`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`${message}\n`);
    process.exitCode = 1;
  }
}

await main();
