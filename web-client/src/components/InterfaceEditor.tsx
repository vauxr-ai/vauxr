import { useEffect, useRef, useState } from "react";
import { ownerFetch, jsonResponse } from "../auth/api";
type Source = {title:string; html:string; css:string; revision:string; published:boolean};
const fields = (source:Source) => ({title:source.title,html:source.html,css:source.css});
export default function InterfaceEditor({onDirtyChange}:{onDirtyChange?:(dirty:boolean)=>void}) {
  const [source,setSource] = useState<Source|null>(null);
  const [saved,setSaved] = useState<Source|null>(null);
  const [document,setDocument] = useState("");
  const [previewed,setPreviewed] = useState("");
  const [status,setStatus] = useState("Loading interface…");
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const mounted = useRef(true);
  const dirty = !!source && !!saved && JSON.stringify(fields(source)) !== JSON.stringify(fields(saved));
  useEffect(() => {onDirtyChange?.(dirty); return () => onDirtyChange?.(false);},[dirty,onDirtyChange]);
  const preview = async (value:Source) => {
    const result = await jsonResponse(await ownerFetch("/api/interface/preview", {method:"POST",body:JSON.stringify(fields(value))}));
    if (mounted.current) {setDocument(result.document);setPreviewed(JSON.stringify(fields(value)));}
  };
  const load = async () => {
    setBusy(true);setError("");
    try {
      const value = await jsonResponse(await ownerFetch("/api/interface"));
      if (!mounted.current) return;
      setSource(value);setSaved(value);await preview(value);
      setStatus(value.published ? "Current published static interface." : "No custom interface published. This is an editable starter.");
    } catch {if(mounted.current)setError("Interface unavailable. Reload before retrying.");}
    finally {if(mounted.current)setBusy(false);}
  };
  useEffect(() => {mounted.current=true; void load(); return () => {mounted.current=false;};},[]);
  useEffect(() => {
    const update = () => {if (!dirty) void load(); else setStatus("An agent finished a change. Your draft is retained; reload the current interface before publishing over it.");};
    window.addEventListener("companion-resource-changed",update);
    return () => window.removeEventListener("companion-resource-changed",update);
  },[dirty]);
  return <section id="interface-editor" tabIndex={-1} aria-label="Interface editor" className="card auth-panel p-5 space-y-3">
    <h2>Interface editor</h2>
    <p>Edit bounded HTML and CSS source. Preview is static: scripts, links, forms, remote assets and Vauxr API access are unavailable. Trusted navigation, administration and voice stay outside the isolated frame.</p>
    <p role="status">{status}</p>
    {error && <p role="alert">{error}</p>}
    {source && <form className="grid min-w-0 gap-3" onSubmit={async event => {
      event.preventDefault();setBusy(true);setError("");
      try {
        const value = await jsonResponse(await ownerFetch("/api/interface",{method:"POST",body:JSON.stringify({...fields(source),revision:source.revision})}));
        setSource(value);setSaved(value);await preview(value);setStatus("Interface published. Existing device, agent and voice configuration is unchanged.");
      } catch {setError("Publish failed or its outcome is uncertain. Reload the current interface before retrying; your draft is retained.");}
      finally {if(mounted.current)setBusy(false);}
    }}>
      <label>Interface title<input value={source.title} maxLength={120} required disabled={busy} onChange={e=>setSource({...source,title:e.target.value})}/></label>
      <label>HTML source<textarea className="w-full min-w-0 font-mono" aria-label="HTML source" rows={6} maxLength={20000} value={source.html} disabled={busy} onChange={e=>setSource({...source,html:e.target.value})}/></label>
      <label>CSS source<textarea className="w-full min-w-0 font-mono" aria-label="CSS source" rows={5} maxLength={12000} value={source.css} disabled={busy} onChange={e=>setSource({...source,css:e.target.value})}/></label>
      {dirty && <p role="status">Unsaved interface edits. Preview does not publish them.</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy} onClick={()=>{setBusy(true);setError("");void preview(source).then(()=>setStatus("Draft preview updated; it has not been published.")).catch(()=>setError("Preview unavailable; source retained.")).finally(()=>setBusy(false));}}>Preview draft</button>
        <button disabled={busy||!dirty}>Publish interface</button>
        <button type="button" disabled={busy} onClick={()=>void load()}>Reload published interface</button>
      </div>
      {previewed !== JSON.stringify(fields(source)) && <p>Preview shows the last rendered source. Choose Preview draft to update it.</p>}
    </form>}
    {document && <iframe title="Isolated interface preview" sandbox="" referrerPolicy="no-referrer"
      allow="camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'"
      className="w-full min-w-0 rounded-lg border" style={{height:340,background:"white"}} srcDoc={document}/>}
  </section>;
}
