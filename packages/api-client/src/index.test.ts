import test, { describe, mock } from "node:test";
import assert from "node:assert/strict";
import { ControlApiClientV2, ApiClientError } from "./index.ts";

describe("ControlApiClientV2 Unit Tests", () => {
  test("ApiClientError exposes status and helper getters", () => {
    const err401 = new ApiClientError(401, "Unauthorized", { detail: "expired" }, "req-1");
    assert.equal(err401.status, 401);
    assert.equal(err401.isUnauthorized, true);
    assert.equal(err401.isForbidden, false);
    assert.equal(err401.requestId, "req-1");

    const err403 = new ApiClientError(403, "Forbidden");
    assert.equal(err403.isForbidden, true);

    const err404 = new ApiClientError(404, "Not Found");
    assert.equal(err404.isNotFound, true);

    const err409 = new ApiClientError(409, "Conflict");
    assert.equal(err409.isConflict, true);
  });

  test("request sets credentials same-origin and cache no-store by default", async () => {
    let capturedInit: RequestInit | undefined;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
      capturedInit = init;
      return new Response(JSON.stringify([{ agent_id: "ag-1", slug: "agent-1", display_name: "Agent 1", lifecycle: "DRAFT" }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch;

    try {
      const client = new ControlApiClientV2({ baseUrl: "http://localhost:8000" });
      const agents = await client.listAgents("ws-1");
      assert.equal(agents.length, 1);
      assert.equal(capturedInit?.credentials, "same-origin");
      assert.equal(capturedInit?.cache, "no-store");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("single-flight refresh: concurrent 401 requests trigger only 1 refresh call and retry", async () => {
    let refreshCalls = 0;
    let agentCalls = 0;
    let approvalCalls = 0;
    let refreshed = false;

    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL | Request, _init?: RequestInit) => {
      const urlStr = String(url);
      if (urlStr.includes("/v1/auth/refresh")) {
        refreshCalls++;
        refreshed = true;
        return new Response(JSON.stringify({ status: "refreshed" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (urlStr.includes("/agents")) {
        agentCalls++;
        if (!refreshed) {
          return new Response(JSON.stringify({ detail: "Token expired" }), { status: 401 });
        }
        return new Response(JSON.stringify([{ agent_id: "ag-1", slug: "agent-1", display_name: "Agent 1", lifecycle: "DRAFT" }]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (urlStr.includes("/approvals")) {
        approvalCalls++;
        if (!refreshed) {
          return new Response(JSON.stringify({ detail: "Token expired" }), { status: 401 });
        }
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("Not found", { status: 404 });
    }) as typeof fetch;

    try {
      let unauthorizedCalled = 0;
      const client = new ControlApiClientV2({
        baseUrl: "http://localhost:8000",
        onUnauthorized: () => {
          unauthorizedCalled++;
        },
      });

      // Fire concurrent requests
      const [agents, approvals] = await Promise.all([
        client.listAgents("ws-1"),
        client.listApprovals("ws-1"),
      ]);

      assert.equal(agents.length, 1);
      assert.equal(approvals.length, 0);
      assert.equal(refreshCalls, 1, "Exactly one refresh call made for concurrent 401s");
      assert.equal(agentCalls, 2, "Agent request called once then retried");
      assert.equal(approvalCalls, 2, "Approval request called once then retried");
      assert.equal(unauthorizedCalled, 0, "onUnauthorized not called because refresh succeeded");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("failed refresh triggers onUnauthorized and rethrows original 401", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL | Request) => {
      const urlStr = String(url);
      if (urlStr.includes("/v1/auth/refresh")) {
        return new Response(JSON.stringify({ detail: "Absolute session expired" }), { status: 401 });
      }
      return new Response(JSON.stringify({ detail: "Unauthorized" }), { status: 401 });
    }) as typeof fetch;

    try {
      let unauthorizedCalled = 0;
      const client = new ControlApiClientV2({
        baseUrl: "http://localhost:8000",
        onUnauthorized: () => {
          unauthorizedCalled++;
        },
      });

      await assert.rejects(
        async () => {
          await client.listAgents("ws-1");
        },
        (err: unknown) => {
          assert.ok(err instanceof ApiClientError);
          assert.equal(err.status, 401);
          return true;
        }
      );

      assert.equal(unauthorizedCalled, 1, "onUnauthorized callback triggered on refresh failure");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("auth endpoints do not auto-refresh on 401", async () => {
    let refreshCalls = 0;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL | Request) => {
      const urlStr = String(url);
      if (urlStr.includes("/v1/auth/refresh")) {
        refreshCalls++;
        return new Response(JSON.stringify({ status: "refreshed" }), { status: 200 });
      }
      if (urlStr.includes("/v1/auth/login")) {
        return new Response(JSON.stringify({ detail: "Invalid credentials" }), { status: 401 });
      }
      return new Response("Not found", { status: 404 });
    }) as typeof fetch;

    try {
      const client = new ControlApiClientV2({ baseUrl: "http://localhost:8000" });
      await assert.rejects(
        async () => {
          await client.getLoginUrl();
        },
        (err: unknown) => {
          assert.ok(err instanceof ApiClientError);
          assert.equal(err.status, 401);
          return true;
        }
      );
      assert.equal(refreshCalls, 0, "No auto-refresh triggered for auth endpoints");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

