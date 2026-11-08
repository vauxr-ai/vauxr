import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ModelProviders } from "./ProviderSettings";
import PluginSettings from "./PluginSettings";
import SettingsPanel from "./SettingsPanel";
const empty = {
  profiles: [],
  speech_auth: {},
  capabilities: { openai_environment_configured: false },
};
const profile = {
  id: "shared",
  name: "Shared OpenAI",
  provider: "openai",
  endpoint: "https://api.openai.com",
  model: "model",
  auth: "inline",
  credential_configured: true,
};
function response(value: unknown, status = 200) {
  return { ok: status === 200, status, json: async () => value } as Response;
}
beforeEach(() => vi.restoreAllMocks());
it("retains server credentials unless explicitly submitted, clears input after failure", async () => {
  const user = userEvent.setup();
  const calls: RequestInit[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_p: string, init: RequestInit) => {
      if (init.method === "PUT") {
        calls.push(init);
        return response({}, 400);
      }
      return response({ ...empty, profiles: [profile] });
    }),
  );
  render(<ModelProviders onDirtyChange={vi.fn()} />);
  await user.click(
    await screen.findByRole("button", { name: /Shared OpenAI/ }),
  );
  const credential = screen.getByLabelText("Replace credential");
  expect(credential).toHaveValue("");
  await user.clear(screen.getByLabelText("Display name"));
  await user.type(screen.getByLabelText("Display name"), "Renamed");
  await user.click(
    screen.getByRole("button", { name: "Save provider profile" }),
  );
  await screen.findByRole("alert");
  expect(JSON.parse(String(calls[0].body))).not.toHaveProperty("credential");
  await user.type(credential, "private-test-key");
  await user.click(
    screen.getByRole("button", { name: "Save provider profile" }),
  );
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(credential).toHaveValue("");
  expect(screen.getByRole("alert")).not.toHaveTextContent("private-test-key");
  expect(sessionStorage.getItem("private-test-key")).toBeNull();
});
it("shows empty plugin catalog with no fabricated controls", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response({ plugins: [] })),
  );
  render(<PluginSettings onDirtyChange={vi.fn()} />);
  await screen.findByText("No plugin settings registered");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Save plugin settings" }),
  ).not.toBeInTheDocument();
});
it("renders declared settings and submits secrets separately", async () => {
  const user = userEvent.setup();
  const plugin = {
    id: "registered",
    name: "Registered plugin",
    fields: [
      {
        key: "enabled",
        label: "Enabled",
        type: "boolean",
        choices: [],
        required: false,
      },
      {
        key: "key",
        label: "Private key",
        type: "secret",
        choices: [],
        required: false,
      },
    ],
    values: { enabled: false },
    secrets: { key: true },
  };
  const fetcher = vi.fn(async () => response({ plugins: [plugin] }));
  vi.stubGlobal("fetch", fetcher);
  render(<PluginSettings onDirtyChange={vi.fn()} />);
  await screen.findByText("Registered plugin");
  await user.click(screen.getByLabelText("Enabled"));
  await user.type(screen.getByLabelText("Private key"), "replacement");
  await user.click(
    screen.getByRole("button", { name: "Save plugin settings" }),
  );
  await screen.findByText("Plugin settings saved.");
  const put = fetcher.mock.calls.find(
    (c) => (c as unknown as [string, RequestInit])[1]?.method === "PUT",
  ) as unknown as [string, RequestInit];
  expect(JSON.parse(String(put[1].body))).toEqual({
    values: { enabled: true },
    secrets: { key: "replacement" },
    clear_secrets: [],
  });
  expect(screen.getByLabelText("Private key")).toHaveValue("");
});
it("opens dedicated settings screens while voice is disconnected", async () => {
  const fetcher = vi.fn(async (path: string) =>
    response(
      path === "/api/settings/providers"
        ? empty
        : path === "/api/settings/speech"
          ? { providers: [] }
          : path === "/api/speech"
            ? { defaults: {} }
            : { requests: [] },
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <SettingsPanel wsUrl="" token="" wsState="disconnected" addLog={vi.fn()} />,
  );
  await screen.findByText("Global STT default");
  fireEvent.click(screen.getByRole("button", { name: /^Apps & Integrations/ }));
  await screen.findByRole("heading", { name: "Connect an agent", exact: true });
  expect(screen.queryByText("Spoken four-digit code")).not.toBeInTheDocument();
  expect(screen.queryByText("Global STT default")).not.toBeInTheDocument();
});

it("keeps another plugin’s unsaved form when one plugin saves", async () => {
  const user = userEvent.setup();
  const plugins = ["first", "second"].map((id) => ({
    id,
    name: id,
    fields: [
      {
        key: "label",
        label: `${id} label`,
        type: "string",
        choices: [],
        required: false,
      },
    ],
    values: { label: "saved" },
    secrets: {},
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_path: string, init?: RequestInit) =>
      response({
        plugins:
          init?.method === "PUT"
            ? plugins.map((p) =>
                p.id === "first"
                  ? { ...p, values: { label: "first draft" } }
                  : p,
              )
            : plugins,
      }),
    ),
  );
  render(<PluginSettings onDirtyChange={vi.fn()} />);
  await screen.findByText("first", { selector: "h3" });
  await user.clear(screen.getByLabelText("first label"));
  await user.type(screen.getByLabelText("first label"), "first draft");
  await user.clear(screen.getByLabelText("second label"));
  await user.type(screen.getByLabelText("second label"), "second draft");
  await user.click(
    screen.getAllByRole("button", { name: "Save plugin settings" })[0],
  );
  await screen.findByText("Plugin settings saved.");
  expect(screen.getByLabelText("second label")).toHaveValue("second draft");
  expect(screen.getByText("Unsaved plugin edits")).toBeInTheDocument();
});
