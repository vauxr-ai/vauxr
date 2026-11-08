import { useEffect, useState } from "react";
import { ownerFetch, jsonResponse } from "../auth/api";
type Plugin = {id:string; name:string; distribution:string; revision?:string; sha256?:string; source?:string; download?:string; filename?:string};
export default function PluginsPanel() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [error, setError] = useState("");
  useEffect(() => { const control = new AbortController();
    ownerFetch("/api/plugins", {signal:control.signal}).then(jsonResponse).then(setPlugins)
      .catch(() => {if (!control.signal.aborted) setError("Plugin catalog unavailable. Refresh to retry.");});
    return () => control.abort();
  }, []);
  return <section id="plugins" tabIndex={-1} aria-label="Integration plugins" className="card auth-panel p-5 space-y-3">
    <h2>Integration plugins</h2>
    <p>Install an integration in its own runtime, then enroll its connection in Agents. Installing a package does not connect it or configure a model. Running plugins use the existing trusted integration connection.</p>
    {error && <p role="alert">{error}</p>}
    {!plugins.length && !error && <p role="status">Loading plugins…</p>}
    {plugins.map(plugin => <section key={plugin.id} aria-label={`${plugin.name} plugin`} className="space-y-2">
      <h3>{plugin.name}</h3>
      {plugin.distribution === "builtin" ? <p>Built in and enabled. Configure its independent runtime in Agents → Vauxr Support.</p> : plugin.distribution === "bundled" ? <p>Included standalone integration. Add a named LangGraph agent in Agents, configure its own local model runtime, and configure named household webhook actions in Settings.</p> : <>
        <p>Available package · source revision <code className="break-all">{plugin.revision}</code></p>
        <a href={plugin.download} download={plugin.filename}>Download OpenClaw plugin</a>
        <p>Install the downloaded archive in your OpenClaw runtime:</p>
        <pre className="whitespace-pre-wrap break-all">openclaw plugins install --force --accept-capabilities ./{plugin.filename}</pre>
        <p>Restart that runtime's gateway using its supported restart control, then run <code>/vauxr pair</code> from an authenticated OpenClaw administration surface and approve its matching request in Agents. Existing installations retain their connection configuration.</p>
        <p>SHA-256: <code className="break-all">{plugin.sha256}</code></p>
        <a href={plugin.source} target="_blank" rel="noreferrer">Inspect package source</a>
      </>}
    </section>)}
    <p>The Pipecat integration plugin is not available in this catalog yet.</p>
  </section>;
}
