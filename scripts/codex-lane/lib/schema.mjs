const VALID_COMMANDS = new Set([
  'start-lane',
  'resume-lane',
  'run-turn',
  'inspect-lane',
  'list-lanes',
]);

const VALID_APPROVAL_POLICIES = new Set(['never', 'on-request', 'on-failure', 'untrusted']);
const VALID_SANDBOX_MODES = new Set(['read-only', 'workspace-write', 'danger-full-access']);
const VALID_REASONING_EFFORTS = new Set(['minimal', 'low', 'medium', 'high', 'xhigh']);
const VALID_WEB_SEARCH_MODES = new Set(['disabled', 'cached', 'live']);

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function assertAbsolutePath(value, fieldName) {
  assert(typeof value === 'string' && value.length > 0, `${fieldName} must be a non-empty string.`);
  assert(value.startsWith('/'), `${fieldName} must be an absolute path.`);
}

function validateThreadOptions(threadOptions, repoRoot) {
  assert(isPlainObject(threadOptions), 'thread_options must be an object.');
  const workingDirectory = threadOptions.workingDirectory ?? repoRoot;
  assertAbsolutePath(workingDirectory, 'thread_options.workingDirectory');

  if (threadOptions.approvalPolicy != null) {
    assert(VALID_APPROVAL_POLICIES.has(threadOptions.approvalPolicy), 'thread_options.approvalPolicy is invalid.');
  }
  if (threadOptions.sandboxMode != null) {
    assert(VALID_SANDBOX_MODES.has(threadOptions.sandboxMode), 'thread_options.sandboxMode is invalid.');
  }
  if (threadOptions.modelReasoningEffort != null) {
    assert(VALID_REASONING_EFFORTS.has(threadOptions.modelReasoningEffort), 'thread_options.modelReasoningEffort is invalid.');
  }
  if (threadOptions.webSearchMode != null) {
    assert(VALID_WEB_SEARCH_MODES.has(threadOptions.webSearchMode), 'thread_options.webSearchMode is invalid.');
  }
  if (threadOptions.additionalDirectories != null) {
    assert(Array.isArray(threadOptions.additionalDirectories), 'thread_options.additionalDirectories must be an array.');
    for (const [index, value] of threadOptions.additionalDirectories.entries()) {
      assertAbsolutePath(value, `thread_options.additionalDirectories[${index}]`);
    }
  }
}

export function validateCommand(command) {
  assert(VALID_COMMANDS.has(command), `Unsupported command '${command}'.`);
}

export function validateRequest(request, commandFromArgv) {
  assert(isPlainObject(request), 'Request payload must be a JSON object.');

  const command = request.command ?? commandFromArgv;
  validateCommand(command);

  assertAbsolutePath(request.repo_root, 'repo_root');
  assert(typeof request.lane_name === 'string' && request.lane_name.trim().length > 0, 'lane_name must be a non-empty string.');

  if (command === 'run-turn') {
    assert(typeof request.prompt === 'string' && request.prompt.length > 0, 'prompt must be a non-empty string for run-turn.');
    if (request.artifacts_dir != null) {
      assertAbsolutePath(request.artifacts_dir, 'artifacts_dir');
    }
    validateThreadOptions(request.thread_options ?? {}, request.repo_root);
    if (request.turn_options != null) {
      assert(isPlainObject(request.turn_options), 'turn_options must be an object when provided.');
    }
    if (request.hermes_refs != null) {
      assert(isPlainObject(request.hermes_refs), 'hermes_refs must be an object when provided.');
    }
  }

  return command;
}
