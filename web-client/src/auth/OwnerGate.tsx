import { useEffect, useRef, useState, type ReactNode } from "react";
import { jsonResponse, ownerFetch, ownerPost, setCsrf } from "./api";
import { retireBrowser } from "./browser";

export default function OwnerGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<{
    state: string;
    environment_managed: boolean;
  }>();
  const [loggedIn, setLoggedIn] = useState(false);
  const loggedInRef = useRef(loggedIn);
  loggedInRef.current = loggedIn;
  const [secret, setSecret] = useState("");
  const [pending, setPending] = useState<{
    operator_token: string;
    save_acknowledgement: string;
  }>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = async () => {
    setStatus(await jsonResponse(await ownerFetch("/api/auth/status")));
    const res = await ownerFetch("/api/auth/session");
    if (res.ok) {
      const session = await res.json();
      setCsrf(session.csrf_token);
      setLoggedIn(true);
    }
  };
  useEffect(() => {
    void refresh().catch(() =>
      setError(
        "Cannot reach the configured owner origin. Check the server Host/origin and transport configuration.",
      ),
    );
    const expire = () => {
      setLoggedIn(false);
      setCsrf("");
      setSecret("");
      setPending(undefined);
      window.dispatchEvent(new Event("voice-stop"));
    };
    const channel = new BroadcastChannel("vauxr-owner");
    channel.onmessage = expire;
    window.addEventListener("owner-expired", expire);
    const timer = setInterval(() => {
      if (!loggedInRef.current) return;
      void ownerFetch("/api/auth/session").catch(() => {
        /* network loss is not proof of logout */
      });
    }, 60000);
    return () => {
      channel.close();
      clearInterval(timer);
      window.removeEventListener("owner-expired", expire);
    };
  }, []);
  useEffect(() => {
    if (!pending) return;
    const timer = setTimeout(() => {
      setPending(undefined);
      setError("Save window expired. Use the console again.");
    }, 300000);
    return () => clearTimeout(timer);
  }, [pending]);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  }
  const logout = () =>
    run(async () => {
      window.dispatchEvent(new Event("voice-stop"));
      // Retire this profile's scoped device before ending the authority needed to revoke it.
      await retireBrowser();
      await ownerPost("/api/auth/logout");
      setCsrf("");
      setLoggedIn(false);
      setSecret("");
      setPending(undefined);
      const channel = new BroadcastChannel("vauxr-owner");
      channel.postMessage("logout");
      channel.close();
    });
  return (
    <>
      <div className="auth-panel card m-4 space-y-3 p-4">
        <h1>Vauxr owner</h1>
        {location.protocol === "http:" && (
          <p>
            HTTP administration is supported on your LAN. Traffic is
            unencrypted: an on-path attacker can read or alter credentials,
            cookies and this page. Optional trusted HTTPS/WSS protects
            transport; never bypass certificate warnings.
          </p>
        )}
        {status?.environment_managed && (
          <p>
            Environment-managed owner access. Generate an operator token with{" "}
            <code>vauxr-owner generate-token</code>, update the authoritative
            OPERATOR_TOKEN and restart to replace access. Browser setup cannot
            override it.
          </p>
        )}
        {error && <p role="alert">{error}</p>}
        {loggedIn ? (
          <button disabled={busy} onClick={logout}>
            Log out on this browser
          </button>
        ) : (
          <>
            <p>
              {status
                ? `Owner state: ${status.state}`
                : "Checking owner access…"}
            </p>
            <p>
              First setup: run <code>vauxr-owner claim</code> in the private
              local server console. Lost access: run{" "}
              <code>vauxr-owner recover</code> there, then submit its code
              below. Recovery preserves paired clients and settings. Use the
              server's DATA_DIR and origin configuration.
            </p>
            {pending ? (
              <div className="space-y-3">
                <p>
                  Save this generated operator token in your password manager
                  now. It will not be displayed again after this step. It cannot
                  log in until saved. If the save reply is lost, discard this
                  display and try the saved token at login before using console
                  recovery.
                </p>
                <output className="block break-all select-all">
                  {pending.operator_token}
                </output>
                <button
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await ownerPost("/api/auth/save", {
                        save_acknowledgement: pending.save_acknowledgement,
                        saved: true,
                      });
                      setPending(undefined);
                      setSecret("");
                      await refresh();
                    })
                  }
                >
                  I saved this in my password manager
                </button>
                <button
                  onClick={() => {
                    setPending(undefined);
                    setSecret("");
                  }}
                >
                  Discard display
                </button>
              </div>
            ) : (
              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    const token = secret;
                    setSecret("");
                    const session = await ownerPost("/api/auth/login", {
                      operator_token: token,
                    });
                    setCsrf(session.csrf_token);
                    setLoggedIn(true);
                  });
                }}
              >
                <label>
                  Operator token or console code
                  <input
                    className="block w-full bg-zinc-800 p-2"
                    type="password"
                    autoComplete="off"
                    value={secret}
                    onChange={(e) => setSecret(e.target.value)}
                  />
                </label>
                <button disabled={busy || !secret}>
                  Log in with saved operator token
                </button>{" "}
                {!status?.environment_managed && (
                  <button
                    type="button"
                    disabled={busy || !secret}
                    onClick={() =>
                      run(async () => {
                        const code = secret;
                        setSecret("");
                        setPending(
                          await ownerPost("/api/auth/claim", { code }),
                        );
                      })
                    }
                  >
                    Submit console setup/recovery code
                  </button>
                )}
              </form>
            )}
          </>
        )}
      </div>
      {loggedIn && children}
    </>
  );
}
