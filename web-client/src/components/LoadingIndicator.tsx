/** Keep loading states visible to assistive technology without visual text jitter. */
export default function LoadingIndicator({label}: {label: string}) {
  return <div className="loading-indicator" role="status" aria-label={label}>
    <span className="loading-spinner" aria-hidden="true"/><span className="sr-only">{label}</span>
  </div>;
}
