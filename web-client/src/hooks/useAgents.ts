import { ownerFetch } from "../auth/api";
import { useCallback } from "react";

export interface ApiAgent {
  id: string;
  name: string;
  type: "openclaw" | "openclaw-direct";
  active: boolean;
  createdAt: string;
  builtin?: boolean;
}

export function useAgents(_baseUrl = "", _token = "") {
  const request = useCallback(async (path: string, init?: RequestInit): Promise<Response> => {
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
      } catch { /* use statusText */ }
      throw new Error(message);
    }
    return res;
  }, []);

  const listAgents = useCallback(async (): Promise<ApiAgent[]> => {
    const res = await request("/api/agents");
    return await res.json();
  }, [request]);

  const activateAgent = useCallback(async (id: string): Promise<void> => {
    await request(`/api/agents/${encodeURIComponent(id)}/activate`, { method: "POST" });
  }, [request]);

  return { listAgents, activateAgent };
}
