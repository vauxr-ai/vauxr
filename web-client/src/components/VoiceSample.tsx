import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ownerFetch } from "../auth/api";
import type { Backend } from "./SpeechSettings";

export type BrowserVoiceProps = { browserDeviceId?: string; browserConnected?: boolean; browserCredential?: () => string | null };

/** Samples are authenticated to the connected browser, never the selected physical device. */
export default function VoiceSample({browserDeviceId, browserConnected = false, browserCredential, backend: selectedTts, voice: selectedVoice, mode}: BrowserVoiceProps & {backend?: Backend; voice: string; mode: string}) {
  const [previewStatus, setPreviewStatus] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const preview = useRef<{controller?: AbortController; audio?: HTMLAudioElement; url?: string}>({});
  const stopPreview = useCallback(() => {
    preview.current.controller?.abort();
    preview.current.audio?.pause();
    if (preview.current.url) URL.revokeObjectURL(preview.current.url);
    preview.current = {};
    setPreviewBusy(false);
  }, []);
  const canPreview = !!(browserConnected && browserDeviceId && browserCredential?.() && selectedTts?.kind === "tts" && selectedTts.readiness === "ready" && selectedTts.voices.includes(selectedVoice));
  useEffect(() => {
    stopPreview();
    setPreviewStatus("");
    return stopPreview;
  }, [selectedVoice, selectedTts?.id, browserConnected, browserDeviceId, mode, canPreview, stopPreview]);
  async function testVoice() {
    stopPreview();
    if (!canPreview || !selectedTts) return;
    const controller = new AbortController();
    preview.current = { controller };
    setPreviewBusy(true);
    setPreviewStatus("Preparing voice sample…");
    try {
      const response = await ownerFetch("/api/speech/preview", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Vauxr-Browser": browserCredential?.() || "" },
        body: JSON.stringify({device_id: browserDeviceId, tts_backend: selectedTts.id, voice: selectedVoice}),
        signal: controller.signal,
      });
      if (!response.ok) {
        const failure = await response.json().catch(() => ({}));
        throw new Error(failure.error || `Voice preview failed (${response.status})`);
      }
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      preview.current = { controller, audio, url };
      audio.onended = () => { if (!controller.signal.aborted) { stopPreview(); setPreviewStatus("Voice sample finished."); } };
      audio.onerror = () => { if (!controller.signal.aborted) { stopPreview(); setPreviewStatus("Voice sample playback failed."); } };
      await audio.play();
      if (controller.signal.aborted) { audio.pause(); return; }
      if (!controller.signal.aborted) setPreviewStatus("Playing in this browser.");
    } catch (err) {
      if (!controller.signal.aborted) {
        stopPreview();
        setPreviewStatus(err instanceof Error ? err.message : "Voice preview failed.");
      }
    }
  }
  const description = useId();
  const reason = !browserConnected ? "Connect browser voice to test a sample." : !canPreview ? "Select a ready text-to-speech provider and voice to test a sample." : previewBusy ? "Voice sample in progress." : "Test selected voice in this browser";
  return <div className="pipeline-sample">
    <span className="pipeline-sample-trigger" tabIndex={!canPreview ? 0 : undefined} aria-label={!canPreview ? reason : undefined} title={reason}>
      <button type="button" className="pipeline-icon pipeline-sample-button" disabled={!canPreview || previewBusy} aria-label="Test selected voice" aria-describedby={description} onClick={() => void testVoice()}>
        <svg aria-hidden="true" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M16 6 8 12H3v8h5l8 6ZM21 11c3 3 3 7 0 10m4-14c6 5 6 13 0 18"/></svg>
      </button><span className="pipeline-sample-tooltip" role="tooltip">{reason}</span>
    </span>
    <span id={description} className="sr-only">{reason}</span>
    {previewBusy && <button type="button" className="pipeline-sample-stop" onClick={() => {stopPreview(); setPreviewStatus("Voice sample stopped.");}}>Stop</button>}
    {previewStatus && <span className="pipeline-sample-status" role="status">{previewStatus}</span>}
  </div>;
}
