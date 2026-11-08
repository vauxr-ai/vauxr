import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SupportSettings from "./SupportSettings";

it("loads a persisted profile reference and submits only that reference", async () => {
  const user = userEvent.setup();
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => ({
    ok: true, status: 200, json: async () => path === "/api/settings/providers"
      ? { profiles: [{ id: "local", name: "Local fixture", provider: "ollama" }, { id: "api", name: "API fixture", provider: "openai" }] }
      : init?.method === "POST" ? { profile_id: "api", status: "unsupported" }
      : { profile_id: "local", status: "configured" },
  } as Response));
  vi.stubGlobal("fetch", fetcher);
  render(<SupportSettings agentId="household" />);
  await waitFor(() => expect(screen.getByLabelText("Model provider")).toBeEnabled());
  expect(screen.getByLabelText("Model profile")).toHaveValue("local");
  await user.selectOptions(screen.getByLabelText("Model profile"), "api");
  await user.click(screen.getByRole("button", { name: "Save agent runtime" }));
  await screen.findByText(/Unsupported: OpenAI profiles/);
  const post = fetcher.mock.calls.find(c => c[1]?.method === "POST")!;
  expect(post[0]).toBe("/api/local-agents/household/runtime");
  expect(JSON.parse(String(post[1]?.body))).toEqual({ profile_id: "api" });
});
