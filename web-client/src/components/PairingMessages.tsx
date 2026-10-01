import { useState } from "react";
import { ownerPost } from "../auth/api";

interface Prompts { intro: string; code: string }

export default function PairingMessages() {
  const [prompts, setPrompts] = useState<Prompts | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  async function loadOrSave(save: boolean) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      setPrompts(await ownerPost(`/api/enrollment/v1/${save ? "save-prompts" : "prompts"}`,
        save ? prompts! : {}));
      if (save) setMessage("Pairing messages saved. They'll be used the next time a device enters pairing mode.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save pairing messages.");
    } finally { setBusy(false); }
  }
  return <div className="space-y-3">
    {!prompts && <button disabled={busy} onClick={() => void loadOrSave(false)}>Edit pairing messages</button>}
    {prompts && <fieldset disabled={busy} className="space-y-3">
      <legend>Pairing messages</legend>
      <p>Spoken using your default speech voice. The code waits until the Action button is pressed.</p>
      <label className="block">Welcome message
        <textarea className="block w-full" rows={4} maxLength={400} value={prompts.intro}
          onChange={(e) => setPrompts({ ...prompts, intro: e.target.value })} />
      </label>
      <label className="block">Code announcement
        <textarea className="block w-full" rows={3} maxLength={200} value={prompts.code}
          onChange={(e) => setPrompts({ ...prompts, code: e.target.value })} />
      </label>
      <p>Include {"{code}"} exactly once in the code announcement. Each Action-button press repeats it.</p>
      <button onClick={() => void loadOrSave(true)}>Save pairing messages</button>
    </fieldset>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
