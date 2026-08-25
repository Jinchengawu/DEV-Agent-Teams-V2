import type { GraphEditingWorkspace } from "./GraphEditingWorkspace";

export type GraphEditingHistory = {
  past: GraphEditingWorkspace[];
  present: GraphEditingWorkspace;
  future: GraphEditingWorkspace[];
};

const HISTORY_LIMIT = 50;

export function createGraphEditingHistory(workspace: GraphEditingWorkspace): GraphEditingHistory {
  return { past: [], present: workspace, future: [] };
}

export function commitGraphEditingWorkspace(history: GraphEditingHistory, next: GraphEditingWorkspace): GraphEditingHistory {
  if (next === history.present) return history;
  return { past: [...history.past, history.present].slice(-HISTORY_LIMIT), present: next, future: [] };
}

export function replaceGraphEditingWorkspace(history: GraphEditingHistory, next: GraphEditingWorkspace): GraphEditingHistory {
  return next === history.present ? history : { ...history, present: next };
}

export function commitTransientGraphEditingWorkspace(history: GraphEditingHistory, origin: GraphEditingWorkspace): GraphEditingHistory {
  if (origin === history.present) return history;
  return { past: [...history.past, origin].slice(-HISTORY_LIMIT), present: history.present, future: [] };
}

export function undoGraphEditingWorkspace(history: GraphEditingHistory): GraphEditingHistory {
  const previous = history.past.at(-1);
  if (!previous) return history;
  return { past: history.past.slice(0, -1), present: previous, future: [history.present, ...history.future].slice(0, HISTORY_LIMIT) };
}

export function redoGraphEditingWorkspace(history: GraphEditingHistory): GraphEditingHistory {
  const next = history.future[0];
  if (!next) return history;
  return { past: [...history.past, history.present].slice(-HISTORY_LIMIT), present: next, future: history.future.slice(1) };
}
