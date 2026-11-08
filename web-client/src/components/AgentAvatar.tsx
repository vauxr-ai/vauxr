import { useEffect, useState } from "react";
import { ownerFetch } from "../auth/api";
import Icon from "./Icon";
export default function AgentAvatar({ agentId, revision = 0 }: {agentId: string; revision?: number}) {
  const [image, setImage] = useState("");
  useEffect(() => {
    const controller = new AbortController(); let url = "";
    setImage("");
    ownerFetch(`/api/agents/${encodeURIComponent(agentId)}/avatar`, {signal: controller.signal})
      .then(async response => {
        if (!response.ok) return;
        const blob = await response.blob();
        if (!controller.signal.aborted) { url = URL.createObjectURL(blob); setImage(url); }
      }).catch(() => {});
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [agentId, revision]);
  return <span className="resource-icon agent-avatar">{image ? <img src={image} alt="" /> : <Icon name="agents" size={24} />}</span>;
}
