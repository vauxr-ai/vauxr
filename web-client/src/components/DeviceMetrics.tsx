import { useEffect, useState } from "react";
import { ownerFetch } from "../auth/api";
import RefreshButton from "./RefreshButton";

type Metrics = {
  device_id: string; window: string; observed_since: string; bucket_seconds: number; retention_days: number;
  metrics: {key: string; label: string; definition: string}[];
  totals: Record<string, number>;
  buckets: {start: string; [key: string]: string | number}[];
};
export default function DeviceMetrics({deviceId}: {deviceId:string}) {
  const [window, setWindow] = useState("24h");
  const [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<Metrics | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(""); setData(null);
    ownerFetch(`/api/devices/${encodeURIComponent(deviceId)}/metrics/${window}`, {signal:controller.signal})
      .then(async response => { if (!response.ok) throw new Error("Device usage could not be loaded"); return await response.json() as Metrics; })
      .then(body => { if (!controller.signal.aborted) setData(body); })
      .catch(err => { if (!controller.signal.aborted) setError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [deviceId, window, refresh]);
  const format = (date: string) => new Date(date).toLocaleString(undefined,{month:"short",day:"numeric",hour:"numeric",minute:"2-digit"});
  return <div className="device-metrics">
    <div className="device-metric-controls"><label className="device-field">Time frame<select value={window} onChange={event => setWindow(event.target.value)}><option value="1h">Last hour</option><option value="24h">24 hourly buckets</option><option value="7d">7 days</option><option value="30d">30 days</option></select></label><RefreshButton label="Refresh device metrics" busy={busy} onClick={() => setRefresh(n => n+1)}/></div>
    {busy && <p role="status">Loading usage…</p>}
    {error && <p role="alert">{error}</p>}
    {data && <>
      <p className="device-muted">Recorded since {format(data.observed_since)}. {data.retention_days}-day retention; no earlier history is inferred. Current bucket is partial.</p>
      {!Object.values(data.totals).some(Boolean) && <p role="status">No recorded voice usage in this time frame.</p>}
      <div className="device-metric-grid">{data.metrics.map(metric => {
        const values = data.buckets.map(bucket => Number(bucket[metric.key]) || 0);
        const max = Math.max(1,...values);
        const points = values.map((value,index) => `${index * 280 / Math.max(1,values.length-1)},${58-value/max*48}`).join(" ");
        return <figure key={metric.key} className="device-metric">
          <figcaption>{metric.label}</figcaption><strong>{data.totals[metric.key] || 0}</strong>
          <svg role="img" aria-label={`${metric.label}: ${data.totals[metric.key] || 0} across ${values.length} buckets`} viewBox="0 0 280 64" preserveAspectRatio="none"><path d="M0 58H280" stroke="currentColor" opacity=".2"/><polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke"/></svg>
          <p className="device-muted">{metric.definition}</p>
        </figure>;
      })}</div>
      <details><summary>Usage data</summary><div style={{overflowX:"auto"}}><table><caption className="sr-only">Device usage per time bucket</caption><thead><tr><th>Bucket start</th>{data.metrics.map(metric=><th key={metric.key}>{metric.label}</th>)}</tr></thead><tbody>{data.buckets.map(bucket=><tr key={bucket.start}><th>{format(bucket.start)}</th>{data.metrics.map(metric=><td key={metric.key}>{bucket[metric.key] || 0}</td>)}</tr>)}</tbody></table></div></details>
    </>}
  </div>;
}
