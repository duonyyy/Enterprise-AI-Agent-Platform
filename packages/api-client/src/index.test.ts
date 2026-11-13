import test, { describe, mock } from "node:test";
import assert from "node:assert/strict";
import {
  ControlApiClientV2,
  ApiClientError,
  canActorAssignTargetRole,
  canActorManageTargetRole,
  canActorRevokeTargetRole,
} from "./index.ts";

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

  test("workspace endpoints (createWorkspace, listWorkspaces, getWorkspace) serialize and parse payloads", async () => {
    const calls: Array<{ url: string; method: string; body?: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      const urlStr = String(url);
      const method = init?.method || "GET";
      calls.push({ url: urlStr, method, body: init?.body ? String(init.body) : undefined });

      if (urlStr.endsWith("/v1/workspaces") && method === "POST") {
        return new Response(
          JSON.stringify({
            workspace_id: "00000000-0000-0000-0000-000000000010",
            tenant_id: "00000000-0000-0000-0000-000000000001",
            tenant_slug: "vinai",
            tenant_name: "VinAI Enterprise",
            workspace_slug: "core-platform",
            workspace_name: "Core Platform",
            status: "active",
            role: "owner",
            initial_owner_user_id: "00000000-0000-0000-0000-000000000099",
            audit_event_id: "00000000-0000-0000-0000-000000000088",
            created_at: "2026-11-12T00:00:00Z",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } }
        );
      }
      if (urlStr.endsWith("/v1/workspaces") && method === "GET") {
        return new Response(
          JSON.stringify({
            items: [
              {
                workspace_id: "00000000-0000-0000-0000-000000000010",
                tenant_id: "00000000-0000-0000-0000-000000000001",
                tenant_slug: "vinai",
                tenant_name: "VinAI Enterprise",
                workspace_slug: "core-platform",
                workspace_name: "Core Platform",
                status: "active",
                role: "owner",
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (urlStr.endsWith("/v1/workspaces/00000000-0000-0000-0000-000000000010") && method === "GET") {
        return new Response(
          JSON.stringify({
            workspace_id: "00000000-0000-0000-0000-000000000010",
            tenant_id: "00000000-0000-0000-0000-000000000001",
            tenant_slug: "vinai",
            tenant_name: "VinAI Enterprise",
            workspace_slug: "core-platform",
            workspace_name: "Core Platform",
            status: "active",
            role: "owner",
            initial_owner_user_id: null,
            audit_event_id: null,
            created_at: "2026-11-12T00:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response("Not found", { status: 404 });
    }) as typeof fetch;

    try {
      const client = new ControlApiClientV2({ baseUrl: "http://localhost:8000" });
      const created = await client.createWorkspace({
        name: "Core Platform",
        slug: "core-platform",
        tenant_id: "00000000-0000-0000-0000-000000000001",
      });
      assert.equal(created.workspace_id, "00000000-0000-0000-0000-000000000010");
      assert.equal(created.workspace_slug, "core-platform");
      assert.equal(created.role, "owner");

      const list = await client.listWorkspaces();
      assert.equal(list.items.length, 1);
      assert.equal(list.items[0].workspace_slug, "core-platform");
      assert.equal(list.items[0].status, "active");

      const detail = await client.getWorkspace("00000000-0000-0000-0000-000000000010");
      assert.equal(detail.workspace_slug, "core-platform");
      assert.equal(calls.length, 3);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("workspace invitations methods decode payloads and {code, message} error details", async () => {
    const calls: Array<{ url: string; method: string; body?: string }> = [];
    const originalFetch = globalThis.fetch;
    const baseInv = {
      invitation_id: "00000000-0000-0000-0000-000000000101",
      tenant_id: "00000000-0000-0000-0000-000000000001",
      workspace_id: "00000000-0000-0000-0000-000000000010",
      recipient_email_normalized: "invitee@example.com",
      recipient_issuer: "http://localhost:8080/realms/enterprise",
      role: "developer",
      status: "pending",
      generation: 1,
      delivery_state: "queued",
      delivery_error_code: null,
      created_by_user_id: "00000000-0000-0000-0000-000000000099",
      delivery_requested_by_user_id: "00000000-0000-0000-0000-000000000099",
      revoked_by_user_id: null,
      accepted_by_user_id: null,
      expires_at: "2026-11-15T00:00:00Z",
      created_at: "2026-11-12T00:00:00Z",
      updated_at: "2026-11-12T00:00:00Z",
      provider_accepted_at: null,
      revoked_at: null,
      accepted_at: null,
      audit_event_id: "00000000-0000-0000-0000-000000000201",
      outbox_event_id: "00000000-0000-0000-0000-000000000301",
    };

    globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      const urlStr = String(url);
      const method = init?.method || "GET";
      calls.push({ url: urlStr, method, body: typeof init?.body === "string" ? init.body : undefined });

      if (urlStr.endsWith("/invitations") && method === "POST") {
        return new Response(JSON.stringify(baseInv), { status: 201 });
      }
      if (urlStr.includes("/invitations?limit=10&cursor=cur-1") && method === "GET") {
        return new Response(JSON.stringify({ items: [baseInv], next_cursor: null }), { status: 200 });
      }
      if (urlStr.endsWith("/resend") && method === "POST") {
        return new Response(
          JSON.stringify({ detail: { code: "resend_cooldown_active", message: "Please wait" } }),
          { status: 429, headers: { "Content-Type": "application/json" } }
        );
      }
      if (urlStr.endsWith("/revoke") && method === "POST") {
        return new Response(JSON.stringify({ ...baseInv, status: "revoked", delivery_state: "cancelled" }), {
          status: 200,
        });
      }
      return new Response("Not found", { status: 404 });
    }) as typeof fetch;

    try {
      const client = new ControlApiClientV2({ baseUrl: "http://localhost:8000" });
      const created = await client.createWorkspaceInvitation("00000000-0000-0000-0000-000000000010", {
        email: "invitee@example.com",
        role: "developer",
      });
      assert.equal(created.generation, 1);
      assert.equal(created.delivery_state, "queued");

      const page = await client.listWorkspaceInvitations("00000000-0000-0000-0000-000000000010", {
        limit: 10,
        cursor: "cur-1",
      });
      assert.equal(page.items.length, 1);
      assert.equal(page.next_cursor, null);

      await assert.rejects(
        () =>
          client.resendWorkspaceInvitation(
            "00000000-0000-0000-0000-000000000010",
            "00000000-0000-0000-0000-000000000101"
          ),
        (err: unknown) => {
          assert.ok(err instanceof ApiClientError);
          assert.equal(err.isRateLimited, true);
          assert.equal(err.errorCode, "resend_cooldown_active");
          assert.equal(err.errorMessage, "Please wait");
          return true;
        }
      );

      const revoked = await client.revokeWorkspaceInvitation(
        "00000000-0000-0000-0000-000000000010",
        "00000000-0000-0000-0000-000000000101"
      );
      assert.equal(revoked.status, "revoked");
      assert.equal(calls.length, 4);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("invitation handoff, preview, accept and getLoginUrl options preserve security flags", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      const urlStr = String(url);
      calls.push({ url: urlStr, init });
      if (urlStr.endsWith("/handoff")) {
        return new Response(JSON.stringify({ expires_at: "2026-11-12T12:00:00Z" }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (urlStr.endsWith("/preview")) {
        return new Response(
          JSON.stringify({
            invitation_id: "11111111-1111-1111-1111-111111111111",
            tenant_id: "22222222-2222-2222-2222-222222222222",
            tenant_slug: "vinai",
            tenant_name: "VinAI",
            workspace_id: "33333333-3333-3333-3333-333333333333",
            workspace_slug: "alpha",
            workspace_name: "Alpha",
            role: "developer",
            status: "pending",
            expires_at: "2026-11-15T12:00:00Z",
            can_accept: true,
            acceptance_state: "ready",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (urlStr.endsWith("/accept")) {
        return new Response(
          JSON.stringify({
            invitation_id: "11111111-1111-1111-1111-111111111111",
            tenant_id: "22222222-2222-2222-2222-222222222222",
            workspace_id: "33333333-3333-3333-3333-333333333333",
            workspace_slug: "alpha",
            workspace_name: "Alpha",
            role: "developer",
            acceptance_state: "accepted",
            redirect_url: "/w/33333333-3333-3333-3333-333333333333/agents",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response(
        JSON.stringify({ authorization_url: "https://idp.example/auth", state: "st-1" }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    }) as typeof fetch;

    try {
      const client = new ControlApiClientV2({ baseUrl: "http://localhost:8000" });
      const handoff = await client.createInvitationHandoff(
        "11111111-1111-1111-1111-111111111111",
        { token: "secret-proof-token-value" }
      );
      assert.equal(handoff.expires_at, "2026-11-12T12:00:00Z");
      assert.equal(calls[0]?.init?.cache, "no-store");
      assert.equal(calls[0]?.init?.referrerPolicy, "no-referrer");
      assert.equal(calls[0]?.init?.credentials, "same-origin");

      await client.getLoginUrl("/invitations/11111111-1111-1111-1111-111111111111/accept", {
        invitationId: "11111111-1111-1111-1111-111111111111",
        reauth: true,
      });
      assert.ok(calls[1]?.url.includes("invitation_id=11111111-1111-1111-1111-111111111111"));
      assert.ok(calls[1]?.url.includes("reauth=true"));

      const preview = await client.previewInvitation("11111111-1111-1111-1111-111111111111");
      assert.equal(preview.can_accept, true);
      assert.equal(calls[2]?.init?.body, undefined);

      const accepted = await client.acceptInvitation("11111111-1111-1111-1111-111111111111");
      assert.equal(accepted.acceptance_state, "accepted");
      assert.equal(calls[3]?.init?.body, undefined);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("supports workspace membership endpoints, RBAC helpers, and promoted-owner already_accepted receipts", async () => {
    assert.equal(canActorManageTargetRole("owner", "owner"), true);
    assert.equal(canActorAssignTargetRole("owner", "admin", "owner"), true);
    assert.equal(canActorManageTargetRole("admin", "owner"), false);
    assert.equal(canActorManageTargetRole("admin", "admin"), false);
    assert.equal(canActorAssignTargetRole("admin", "developer", "admin"), false);
    assert.equal(canActorAssignTargetRole("admin", "developer", "owner"), false);
    assert.equal(canActorAssignTargetRole("admin", "developer", "operator"), true);
    assert.equal(canActorRevokeTargetRole("admin", "viewer"), true);
    assert.equal(canActorRevokeTargetRole("admin", "owner"), false);
    assert.equal(canActorManageTargetRole("developer", "viewer"), false);

    const originalFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      const urlStr = typeof url === "string" ? url : url instanceof URL ? url.toString() : url.url;
      calls.push({ url: urlStr, init });
      if (urlStr.includes("/memberships?")) {
        return new Response(
          JSON.stringify({
            items: [
              {
                membership_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                user_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                display_name: "Alice",
                role: "developer",
                status: "active",
                joined_at: "2026-11-12T00:00:00Z",
              },
            ],
            next_cursor: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (urlStr.endsWith("/role")) {
        return new Response(
          JSON.stringify({
            membership_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            user_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            display_name: "Alice",
            role: "owner",
            status: "active",
            joined_at: "2026-11-12T00:00:00Z",
            outcome: "updated",
            audit_event_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (urlStr.endsWith("/memberships/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/revoke")) {
        return new Response(
          JSON.stringify({
            membership_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            user_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            display_name: "Alice",
            role: "owner",
            status: "revoked",
            joined_at: "2026-11-12T00:00:00Z",
            outcome: "revoked",
            audit_event_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response(
        JSON.stringify({
          invitation_id: "11111111-1111-1111-1111-111111111111",
          tenant_id: "22222222-2222-2222-2222-222222222222",
          tenant_slug: "vinai",
          tenant_name: "VinAI",
          workspace_id: "33333333-3333-3333-3333-333333333333",
          workspace_slug: "alpha",
          workspace_name: "Alpha",
          role: "owner",
          status: "accepted",
          expires_at: "2026-11-15T12:00:00Z",
          can_accept: false,
          acceptance_state: "already_accepted",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    }) as typeof fetch;

    try {
      const client = new ControlApiClientV2({ baseUrl: "http://localhost:8000" });
      const list = await client.listWorkspaceMemberships("33333333-3333-3333-3333-333333333333", {
        limit: 25,
        status: "active",
      });
      assert.equal(list.items.length, 1);
      assert.ok(calls[0]?.url.includes("limit=25"));
      assert.ok(calls[0]?.url.includes("status=active"));
      assert.equal(calls[0]?.init?.cache, "no-store");

      const updated = await client.updateWorkspaceMembershipRole(
        "33333333-3333-3333-3333-333333333333",
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        { role: "owner", expected_role: "developer", expected_status: "active" }
      );
      assert.equal(updated.role, "owner");
      assert.equal(updated.outcome, "updated");
      assert.equal(calls[1]?.init?.method, "PATCH");

      const revoked = await client.revokeWorkspaceMembership(
        "33333333-3333-3333-3333-333333333333",
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        { expected_role: "owner", expected_status: "active" }
      );
      assert.equal(revoked.status, "revoked");
      assert.equal(revoked.outcome, "revoked");

      const promotedPreview = await client.previewInvitation("11111111-1111-1111-1111-111111111111");
      assert.equal(promotedPreview.role, "owner");
      assert.equal(promotedPreview.acceptance_state, "already_accepted");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

