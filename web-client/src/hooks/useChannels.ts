import { ownerFetch } from "../auth/api";
import { useCallback } from "react";

export interface ApiChannel {
  id: string;
  name: string;
  type: "openclaw" | "openclaw-direct";
  active: boolean;
  createdAt: string;
  builtin?: boolean;
}

export function useChannels(_baseUrl = "", _token = "") {
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

  const listChannels = useCallback(async (): Promise<ApiChannel[]> => {
    const res = await request("/api/channels");
    return await res.json();
  }, [request]);

  const activateChannel = useCallback(async (id: string): Promise<void> => {
    await request(`/api/channels/${encodeURIComponent(id)}/activate`, { method: "POST" });
  }, [request]);

  return { listChannels, activateChannel };
}
