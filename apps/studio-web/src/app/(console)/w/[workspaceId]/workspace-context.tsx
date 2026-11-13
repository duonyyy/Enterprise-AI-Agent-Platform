"use client";

import React, { createContext, useContext } from "react";
import type { CurrentUserResponse, WorkspaceMembershipInfo } from "@enterprise/api-client";

export interface WorkspaceSessionContextValue {
  currentUser: CurrentUserResponse;
  currentWorkspace: WorkspaceMembershipInfo;
  currentWorkspaceId: string;
  actorRole: string;
  requestSignal: AbortSignal;
  refreshWorkspaceAuth: () => Promise<void>;
}

const WorkspaceSessionContext = createContext<WorkspaceSessionContextValue | null>(null);

export const WorkspaceSessionProvider = WorkspaceSessionContext.Provider;

export function useWorkspaceSession(): WorkspaceSessionContextValue {
  const ctx = useContext(WorkspaceSessionContext);
  if (!ctx) {
    throw new Error("useWorkspaceSession must be used inside WorkspaceConsoleLayout");
  }
  return ctx;
}
