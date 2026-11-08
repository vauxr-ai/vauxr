import { useEffect, useState } from "react";
import { useAgents } from "./useAgents";

/** Observe shared routing metadata without changing conversation ownership. */
export function useActiveRoute() {
  const { listAgents } = useAgents();
  const [name, setName] = useState("Loading active route…");

  useEffect(() => {
    let alive = true;
    let revision = 0;
    let controller: AbortController | undefined;
    const refresh = async () => {
      const attempt = ++revision;
      controller?.abort();
      controller = new AbortController();
      try {
        const agents = await listAgents(controller.signal);
        if (alive && attempt === revision) {
          setName(
            agents.find((agent) => agent.active)?.name || "No active route",
          );
        }
      } catch {
        if (alive && attempt === revision) setName("Route unavailable");
      }
    };
    const visible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    void refresh();
    const timer = window.setInterval(visible, 5000);
    window.addEventListener("agent-route-changed", visible);
    document.addEventListener("visibilitychange", visible);
    return () => {
      alive = false;
      revision++;
      controller?.abort();
      window.clearInterval(timer);
      window.removeEventListener("agent-route-changed", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [listAgents]);

  return name;
}
