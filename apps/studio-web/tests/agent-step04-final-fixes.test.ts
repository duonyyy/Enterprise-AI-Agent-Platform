import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import {
  AgentRequestScope,
  AgentValidationScope,
  buildThreeWayMergedSpec,
  calculateThreeWayDraftDiff,
  clearPendingPublishIntent,
  extractEditsFromSpec,
  getPendingPublishIntent,
  mergeAgentDraftEdits,
  savePendingPublishIntent,
  setPendingPublishStorage,
} from "../src/lib/agent-draft-state.ts";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");

function readAgentComponent(fileName: string): string {
  return readFileSync(
    new URL(`../src/app/(console)/w/[workspaceId]/agents/${fileName}`, import.meta.url),
    "utf8"
  );
}

function loadHandlers(context: Record<string, unknown>, names: string[], fileName: string) {
  const source = readAgentComponent(fileName);
  const parsed = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const declarations = new Map<string, string>();
  function visit(node: any) {
    if (ts.isVariableStatement(node)) {
      for (const declaration of node.declarationList.declarations) {
        const name = declaration.name.getText(parsed);
        if (names.includes(name)) {
          declarations.set(name, node.getText(parsed));
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(parsed);
  assert.equal(declarations.size, names.length, `Expected handlers ${names.join(", ")} in ${fileName}`);
  const script = [...declarations.values()].join("\n") + `\nglobalThis.handlers = {${names.join(",")}};`;
  vm.createContext(context);
  vm.runInContext(
    ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText,
    context
  );
  return (context as any).handlers;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeSpec(instructions: string, modelRef = "00000000-0000-0000-0000-000000000001") {
  return {
    schemaVersion: "1.0",
    kind: "Agent",
    metadata: { name: "agent-a", displayName: "Agent A" },
    mode: "fixed_plan",
    modelRef,
    persona: { instructions },
    limits: { maxSteps: 10, maxWallTimeSeconds: 180, maxTotalTokens: 16000 },
  };
}

// ---------------------------------------------------------------------------
// RF04-01: Pending save must not overwrite newer edits; conflict workbench locks form
// ---------------------------------------------------------------------------

test("RF04-01: deferred PATCH response A preserves newer edit B and rebases baseSpec/revision", async () => {
  const patchDeferred = deferred<any>();
  const initialSpec = makeSpec("submitted-A");
  const state: Record<string, any> = {
    instructions: "submitted-A",
    modelRef: initialSpec.modelRef,
    mode: "fixed_plan",
    maxSteps: 10,
    maxWallTime: 180,
    maxTokens: 16000,
    baseSpec: makeSpec("base-0"),
    saving: false,
    hasEditsSinceValidation: false,
    conflictWorkbench: null,
    parentDraft: { agent_id: "agent-a", revision: 1, spec: makeSpec("base-0") },
    toasts: [] as Array<[string, string]>,
  };

  const context: Record<string, any> = {
    useCallback: (fn: unknown) => fn,
    workspaceId: "ws-1",
    isOpen: true,
    activeDraft: state.parentDraft,
    editorLifetime: { current: new AgentRequestScope() },
    saveScope: { current: new AgentRequestScope() },
    validationScope: { current: new AgentValidationScope() },
    baseSpecRef: { current: state.baseSpec },
    editGenerationRef: { current: 1 },
    conflictWorkbenchRef: { current: null },
    latestEditsRef: { current: extractEditsFromSpec(initialSpec) },
    baseSpec: state.baseSpec,
    instructions: state.instructions,
    modelRef: state.modelRef,
    mode: state.mode,
    maxSteps: state.maxSteps,
    maxWallTime: state.maxWallTime,
    maxTokens: state.maxTokens,
    defaultApiClient: {
      replaceAgentDraftSpec: () => patchDeferred.promise,
    },
    ApiClientError: class extends Error {
      status = 412;
    },
    extractEditsFromSpec,
    calculateThreeWayDraftDiff,
    buildThreeWayMergedSpec,
    mergeAgentDraftEdits,
    setIsSaving: (v: boolean) => (state.saving = v),
    setSaveError: (v: string | null) => (state.saveError = v),
    setBaseSpec: (v: any) => (state.baseSpec = v),
    setInstructions: (v: string) => (state.instructions = v),
    setModelRef: (v: string) => (state.modelRef = v),
    setMode: (v: string) => (state.mode = v),
    setMaxSteps: (v: number) => (state.maxSteps = v),
    setMaxWallTime: (v: number) => (state.maxWallTime = v),
    setMaxTokens: (v: number) => (state.maxTokens = v),
    setConflictWorkbench: (v: any) => (state.conflictWorkbench = v),
    invalidateValidation: () => {
      state.hasEditsSinceValidation = true;
    },
    setValidationResult: (v: any) => (state.validationResult = v),
    setHasEditsSinceValidation: (v: boolean) => (state.hasEditsSinceValidation = v),
    onDraftSaved: (draft: any) => {
      state.parentDraft = draft;
    },
    onShowToast: (msg: string, type: string) => state.toasts.push([msg, type]),
  };

  const handlers = loadHandlers(context, ["executeSave", "updateInstructions"], "agent-workshop.tsx");

  const pendingSave = handlers.executeSave(initialSpec, 1);
  assert.equal(state.saving, true);

  // User types B while PATCH A is still in flight
  handlers.updateInstructions("typed-B-after-click");
  assert.equal(state.instructions, "typed-B-after-click");

  // Server commits A as revision 2
  patchDeferred.resolve({ agent_id: "agent-a", revision: 2, spec: initialSpec });
  await pendingSave;

  // Newer edit B MUST survive, while baseSpec & parent revision advance to rev 2
  assert.equal(state.instructions, "typed-B-after-click");
  assert.equal(state.baseSpec.persona.instructions, "submitted-A");
  assert.equal(state.parentDraft.revision, 2);
  assert.equal(state.hasEditsSinceValidation, true);
  assert.equal(state.saving, false);
});

test("RF04-01: 412 conflict uses latest edits and locks updaters until conflict is resolved", async () => {
  const getDeferred = deferred<any>();
  const baseSpec = makeSpec("base-0");
  const submittedSpec = makeSpec("submitted-A");
  const serverSpec = makeSpec("server-rev-2");

  const state: Record<string, any> = {
    instructions: "submitted-A",
    conflictWorkbench: null,
    saving: false,
    parentDraft: { agent_id: "agent-a", revision: 1, spec: baseSpec },
  };

  class MockApiError extends Error {
    status: number;
    constructor(status: number) {
      super("conflict");
      this.status = status;
    }
  }

  const context: Record<string, any> = {
    useCallback: (fn: unknown) => fn,
    workspaceId: "ws-1",
    isOpen: true,
    activeDraft: state.parentDraft,
    editorLifetime: { current: new AgentRequestScope() },
    saveScope: { current: new AgentRequestScope() },
    validationScope: { current: new AgentValidationScope() },
    baseSpecRef: { current: baseSpec },
    editGenerationRef: { current: 1 },
    conflictWorkbenchRef: { current: null },
    latestEditsRef: { current: extractEditsFromSpec(submittedSpec) },
    baseSpec,
    instructions: "submitted-A",
    modelRef: submittedSpec.modelRef,
    mode: "fixed_plan",
    maxSteps: 10,
    maxWallTime: 180,
    maxTokens: 16000,
    conflictWorkbench: null,
    isSaving: false,
    defaultApiClient: {
      replaceAgentDraftSpec: async () => {
        throw new MockApiError(412);
      },
      getAgentDraft: () => getDeferred.promise,
    },
    ApiClientError: MockApiError,
    extractEditsFromSpec,
    calculateThreeWayDraftDiff,
    buildThreeWayMergedSpec,
    mergeAgentDraftEdits,
    setIsSaving: (v: boolean) => {
      state.saving = v;
      context.isSaving = v;
    },
    setSaveError: (v: string | null) => (state.saveError = v),
    setBaseSpec: (v: any) => (state.baseSpec = v),
    setInstructions: (v: string) => (state.instructions = v),
    setModelRef: () => {},
    setMode: () => {},
    setMaxSteps: () => {},
    setMaxWallTime: () => {},
    setMaxTokens: () => {},
    setConflictWorkbench: (v: any) => {
      state.conflictWorkbench = v;
      context.conflictWorkbench = v;
    },
    invalidateValidation: () => {},
    setValidationResult: () => {},
    setHasEditsSinceValidation: () => {},
    onDraftSaved: (draft: any) => {
      state.parentDraft = draft;
    },
    onShowToast: () => {},
  };

  const handlers = loadHandlers(
    context,
    ["executeSave", "updateInstructions", "handleAdoptServerDraft"],
    "agent-workshop.tsx"
  );

  const pending = handlers.executeSave(submittedSpec, 1);
  // Simulate edit arriving before GET refetch completes
  handlers.updateInstructions("latest-before-412-refetch");
  getDeferred.resolve({ agent_id: "agent-a", revision: 2, spec: serverSpec });
  await pending;

  assert.ok(state.conflictWorkbench !== null, "Conflict workbench must open on 412");
  const instructionsDiff = state.conflictWorkbench.diffResult.diffs.find(
    (d: any) => d.field === "instructions"
  );
  assert.equal(instructionsDiff.localValue, "latest-before-412-refetch");
  assert.equal(instructionsDiff.serverValue, "server-rev-2");

  // Once conflict workbench is active, form updaters must NOT mutate form behind snapshot
  handlers.updateInstructions("illegal-drift-during-conflict");
  assert.equal(state.instructions, "latest-before-412-refetch");

  // Adopting server draft clears conflict and unlocks editing
  handlers.handleAdoptServerDraft();
  assert.equal(state.conflictWorkbench, null);
  assert.equal(state.instructions, "server-rev-2");
  assert.equal(state.parentDraft.revision, 2);

  handlers.updateInstructions("post-resolve-edit");
  assert.equal(state.instructions, "post-resolve-edit");
});

test("RF04-01: workshop locks form inputs during save/conflict and hydrates only on scope change", () => {
  const source = readAgentComponent("agent-workshop.tsx");
  assert.ok(source.includes("const isFormLocked = isSaving || Boolean(conflictWorkbench);"));
  assert.ok(source.includes("hydratedScopeRef.current === nextScopeKey"));
  assert.ok(source.includes("disabled={isFormLocked}"));
});

// ---------------------------------------------------------------------------
// RF04-07: UI must not default or chip unresolvable model aliases
// ---------------------------------------------------------------------------

test("RF04-07: starter templates, create modal, and workshop never prefill unresolvable model aliases", () => {
  const forbiddenAliases = ["approved-general@2", "claude-3-5-sonnet", "gpt-4o", "local-llama-3"];

  const workshopSrc = readAgentComponent("agent-workshop.tsx");
  const createSrc = readAgentComponent("agent-create-modal.tsx");
  const listSrc = readAgentComponent("agent-list.tsx");

  for (const alias of forbiddenAliases) {
    assert.equal(workshopSrc.includes(alias), false, `workshop must not contain unresolvable alias ${alias}`);
    assert.equal(createSrc.includes(alias), false, `create modal must not contain unresolvable alias ${alias}`);
    assert.equal(listSrc.includes(alias), false, `starter templates must not contain unresolvable alias ${alias}`);
  }

  // Unsupported modes are explicitly disabled in both create modal and workshop
  assert.ok(createSrc.includes('<option value="planner_based" disabled>'));
  assert.ok(createSrc.includes('<option value="react" disabled>'));
  assert.ok(workshopSrc.includes('<option value="planner_based" disabled>'));
  assert.ok(workshopSrc.includes('<option value="react" disabled>'));
});

// ---------------------------------------------------------------------------
// RF04-08: Actor switch in same workspace resets state, aborts A, and refetches B
// ---------------------------------------------------------------------------

test("RF04-08: actor switch in same workspace aborts actor A fetch and completes actor B fetch", async () => {
  const actorADeferred = deferred<any>();
  const actorBDeferred = deferred<any>();
  const signals: AbortSignal[] = [];

  const state: Record<string, any> = {
    agents: [],
    viewState: "loading",
    refreshing: false,
    nextCursor: null,
    currentPage: 1,
    errorMessage: "",
  };

  const context: Record<string, any> = {
    useCallback: (fn: unknown) => fn,
    workspaceId: "ws-shared",
    actorId: "actor-a",
    router: { replace: () => {} },
    requestScope: { current: new AgentRequestScope() },
    listScope: { current: new AgentRequestScope() },
    defaultApiClient: {
      listAgents: (_ws: string, opts: { signal: AbortSignal }) => {
        signals.push(opts.signal);
        return signals.length === 1 ? actorADeferred.promise : actorBDeferred.promise;
      },
    },
    ApiClientError: class extends Error {},
    setIsRefreshing: (v: boolean) => (state.refreshing = v),
    setViewState: (v: string) => (state.viewState = v),
    setErrorMessage: (v: string) => (state.errorMessage = v),
    setErrorStatus: () => {},
    setRequestId: () => {},
    setAgents: (v: any[]) => (state.agents = v),
    setNextCursor: (v: string | null) => (state.nextCursor = v),
    setCurrentPage: (v: number) => (state.currentPage = v),
  };

  const handlers = loadHandlers(context, ["fetchAgents"], "page.tsx");

  // 1. Actor A starts loading list
  const pendingA = handlers.fetchAgents();
  assert.equal(state.refreshing, true);
  assert.equal(state.viewState, "loading");
  assert.equal(signals.length, 1);
  assert.equal(signals[0].aborted, false);

  // 2. Actor switches to Actor B in the same workspace (simulated layout effect reset + B fetch)
  context.actorId = "actor-b";
  context.requestScope.current.reset();
  context.listScope.current.reset();
  state.agents = [];
  state.viewState = "loading";
  assert.equal(signals[0].aborted, true, "Actor A in-flight list request must be aborted on actor change");

  const pendingB = handlers.fetchAgents();
  assert.equal(signals.length, 2);

  // 3. Actor A's late response resolves -> must NOT overwrite Actor B or clear Actor B's loading
  actorADeferred.resolve({ items: [{ agent_id: "a-only-agent" }], next_cursor: null });
  await pendingA;
  assert.equal(state.agents.length, 0);
  assert.equal(state.refreshing, true, "Actor A finally must not clear Actor B loading");

  // 4. Actor B's response resolves -> list shows Actor B's data cleanly
  actorBDeferred.resolve({ items: [{ agent_id: "b-agent" }], next_cursor: null });
  await pendingB;
  assert.equal(state.agents.length, 1);
  assert.equal(state.agents[0].agent_id, "b-agent");
  assert.equal(state.viewState, "ready");
  assert.equal(state.refreshing, false);

  // 5. Logout (empty actorId) must not start a new list fetch
  context.actorId = "";
  await handlers.fetchAgents();
  assert.equal(signals.length, 2, "Logged-out actor must not issue listAgents");
});

test("RF04-08: page binds reset, fetchAgents, initial load, and modal keys to (workspaceId, actorId) and keeps publish recovery actor-partitioned", () => {
  const pageSrc = readAgentComponent("page.tsx");
  assert.ok(pageSrc.includes("[workspaceId, actorId, dismissToast]"));
  assert.ok(pageSrc.includes("[workspaceId, actorId, router]"));
  assert.ok(pageSrc.includes("[fetchAgents, workspaceId, actorId]"));
  assert.ok(pageSrc.includes('key={`create-${workspaceId}:${actorId}`}'));
  assert.ok(pageSrc.includes('key={`workshop-${workspaceId}:${actorId}`}'));

  // Pending publish intents remain isolated between Actor A and Actor B in the same workspace
  setPendingPublishStorage([]);
  savePendingPublishIntent({
    actorId: "actor-a",
    workspaceId: "ws-shared",
    agentId: "agent-1",
    operationKey: "op-actor-a",
    expectedRevision: 3,
    status: "pending",
    createdAt: Date.now(),
  });
  assert.equal(getPendingPublishIntent("actor-b", "ws-shared", "agent-1"), null);
  clearPendingPublishIntent("actor-b", "ws-shared", "agent-1");
  assert.equal(getPendingPublishIntent("actor-a", "ws-shared", "agent-1")?.operationKey, "op-actor-a");
});
