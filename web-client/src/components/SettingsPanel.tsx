import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ComponentProps } from "react";
import InterfaceEditor from "./InterfaceEditor";
import PluginsPanel from "./PluginsPanel";
import PluginSettings from "./PluginSettings";
import AccessPanel from "./AccessPanel";
import IntegrationEnrollment from "./IntegrationEnrollment";
import WebhooksPanel from "./WebhooksPanel";
import { ModelProviders, VoiceProviders } from "./ProviderSettings";
import Icon, { type IconName } from "./Icon";
const screens: {
  id: string;
  label: string;
  description: string;
  icon: IconName;
}[] = [
  {
    id: "stt",
    label: "Speech to text",
    description: "STT services & models",
    icon: "mic",
  },
  {
    id: "tts",
    label: "Text to speech",
    description: "TTS models & voices",
    icon: "volume",
  },
  {
    id: "realtime",
    label: "Realtime",
    description: "Live voice connections",
    icon: "connection",
  },
  {
    id: "models",
    label: "Model providers",
    description: "Connections & shared credentials",
    icon: "agents",
  },
  {
    id: "plugins",
    label: "Plugins",
    description: "Registered settings & packages",
    icon: "api",
  },
  {
    id: "apps",
    label: "Apps & Integrations",
    description: "Connect external agents",
    icon: "connection",
  },
  {
    id: "interfaces",
    label: "Interfaces",
    description: "Isolated HTML & CSS surface",
    icon: "devices",
  },
  {
    id: "webhooks",
    label: "Webhooks",
    description: "Device gesture actions",
    icon: "api",
  },
  {
    id: "security",
    label: "Access & security",
    description: "Identity lifecycle & recovery",
    icon: "rotate-key",
  },
];
export default function SettingsPanel(
  props: ComponentProps<typeof WebhooksPanel>,
) {
  const [screen, setScreen] = useState("stt");
  const [dirty, setDirty] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const previousScreen = useRef(screen);
  useLayoutEffect(() => {
    if (previousScreen.current !== screen) {
      previousScreen.current = screen;
      heading.current?.focus();
    }
  }, [screen]);
  useEffect(() => {
    props.onDirtyChange?.(dirty);
    return () => props.onDirtyChange?.(false);
  }, [dirty, props.onDirtyChange]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const current = screens.find((s) => s.id === screen)!;
  return (
    <div className="settings-layout administration">
      <nav className="settings-nav" aria-label="Settings sections">
        {screens.map((s) => (
          <button
            key={s.id}
            aria-current={screen === s.id ? "page" : undefined}
            onClick={() => {
              if (s.id === screen) return;
              if (dirty && !confirm("Discard unsaved Settings edits?")) return;
              setDirty(false);
              setScreen(s.id);
            }}
          >
            <Icon name={s.icon} />
            <span>
              <strong>{s.label}</strong>
              <small>{s.description}</small>
            </span>
            <Icon name="chevron-right" size={14} />
          </button>
        ))}
      </nav>
      <div className="settings-content">
        <h2 ref={heading} tabIndex={-1} className="sr-only">
          {current.label} settings
        </h2>
        {(["stt", "tts", "realtime"] as const).map(
          (kind) =>
            screen === kind && (
              <VoiceProviders key={kind} kind={kind} onDirtyChange={setDirty} />
            ),
        )}
        {screen === "models" && <ModelProviders onDirtyChange={setDirty} />}
        {screen === "plugins" && (
          <>
            <PluginSettings onDirtyChange={setDirty} />
            <PluginsPanel />
          </>
        )}
        {screen === "apps" && (
          <section className="card settings-form">
            <span className="eyebrow">Apps & Integrations</span>
            <h2>Connect an agent</h2>
            <p>
              Start enrollment from the Vauxr plugin in your OpenClaw runtime,
              then approve its matching request here. Approval connects that
              runtime without changing your selected agent.
            </p>
            <p>
              Integration credential rotation and revocation are available in
              Access &amp; security. Device pairing is in Devices → + Add
              Device.
            </p>
            <IntegrationEnrollment />
          </section>
        )}
        {screen === "interfaces" && (
          <InterfaceEditor onDirtyChange={setDirty} />
        )}
        {screen === "security" && <AccessPanel mode="security" />}
        {screen === "webhooks" && (
          <WebhooksPanel {...props} onDirtyChange={setDirty} />
        )}
      </div>
    </div>
  );
}
