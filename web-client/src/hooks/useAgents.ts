import { ownerFetch } from "../auth/api";
import { useCallback } from "react";

export interface IntegrationMetadata {
  framework: "openclaw" | "support" | "langgraph" | "langchain";
  configuration: "enrolled" | "operator-managed" | "registered" | "builtin" | "local";
}

export interface ApiAgent {
  definition_id?: string;
  id: string;
  name: string;
  type: "openclaw" | "openclaw-direct" | "support" | "langgraph" | "langchain";
  active: boolean;
  createdAt: string;
  builtin?: boolean;
  avatar_url?: string;
  chat_only?: boolean;
  integration_metadata?: IntegrationMetadata;
}

export function useAgents(_baseUrl = "", _token = "") {
  const request = useCallback(
    async (path: string, init?: RequestInit): Promise<Response> => {
      const res = await ownerFetch(path, {
        ...init,
        headers: {
          ...init?.headers,
          "Content-Type": "application/json",
        },
      });
      if (!res.ok) {
        let message = res.statusText;
        try {
          const body = await res.json();
          if (body.error) message = body.error;
          else if (body.message) message = body.message;
        } catch {
          /* use statusText */
        }
        throw new Error(message);
      }
      return res;
    },
    [],
  );

  const listAgents = useCallback(
    async (signal?: AbortSignal): Promise<ApiAgent[]> => {
      const res = await request("/api/agents", { signal });
      const agents = await res.json();
      if (!Array.isArray(agents))
        throw new Error("Invalid agent inventory response");
      return agents;
    },
    [request],
  );

  const activateAgent = useCallback(
    async (id: string): Promise<void> => {
      await request(`/api/agents/${encodeURIComponent(id)}/activate`, {
        method: "POST",
      });
    },
    [request],
  );

  const renameAgent = useCallback(
    async (id: string, name: string): Promise<{ id: string; name: string }> => {
      const response = await request(`/api/agents/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      });
      return response.json();
    },
    [request],
  );

  const deleteAgent = useCallback(async (id:string): Promise<void> => {
    await request(`/api/agents/${encodeURIComponent(id)}`,{method:"DELETE"});
  },[request]);
  return { listAgents, activateAgent, renameAgent, deleteAgent };
}
