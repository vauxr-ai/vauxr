import { useCallback, useRef } from "react";

export interface ApiChannel {
  id: string;
  name: string;
  type: "openclaw" | "openclaw-direct";
  active: boolean;
  createdAt: string;
  builtin?: boolean;
  token?: string;
}

export function useChannels(baseUrl: string, token: string) {
  const baseUrlRef = useRef(baseUrl);
  const tokenRef = useRef(token);
  baseUrlRef.current = baseUrl;
  tokenRef.current = token;

  const request = useCallback(async (path: string, init?: RequestInit): Promise<Response> => {
    const res = await fetch(`${baseUrlRef.current}${path}`, {
      ...init,
      headers: {
        ...init?.headers,
        Authorization: `Bearer ${tokenRef.current}`,
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

  const createChannel = useCallback(async (name: string, type: string): Promise<ApiChannel> => {
    const res = await request("/api/channels", {
      method: "POST",
      body: JSON.stringify({ name, type }),
    });
    return await res.json();
  }, [request]);

  const deleteChannel = useCallback(async (id: string): Promise<void> => {
    await request(`/api/channels/${encodeURIComponent(id)}`, { method: "DELETE" });
  }, [request]);

  const activateChannel = useCallback(async (id: string): Promise<void> => {
    await request(`/api/channels/${encodeURIComponent(id)}/activate`, { method: "POST" });
  }, [request]);

  const rotateToken = useCallback(async (id: string): Promise<string> => {
    const res = await request(`/api/channels/${encodeURIComponent(id)}/rotate`, { method: "POST" });
    const body = await res.json();
    return body.token;
  }, [request]);

  return { listChannels, createChannel, deleteChannel, activateChannel, rotateToken };
}
