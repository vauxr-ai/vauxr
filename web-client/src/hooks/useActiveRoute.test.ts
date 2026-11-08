import { act, renderHook } from "@testing-library/react";
import { useActiveRoute } from "./useActiveRoute";
const { listAgents } = vi.hoisted(() => ({ listAgents: vi.fn() }));
vi.mock("./useAgents", () => ({ useAgents: () => ({ listAgents }) }));
const agent = (name: string) => [{ id: "route", name, active: true }];

it("observes remote routing, refreshes on visibility, and cancels polling at unmount", async () => {
  vi.useFakeTimers();
  listAgents.mockResolvedValue(agent("Original"));
  const view = renderHook(useActiveRoute);
  await act(async () => {});
  expect(view.result.current).toBe("Original");
  listAgents.mockResolvedValue(agent("Remote name"));
  await act(async () => {
    vi.advanceTimersByTime(5000);
  });
  expect(view.result.current).toBe("Remote name");
  listAgents.mockResolvedValue(agent("Another tab"));
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(view.result.current).toBe("Another tab");
  const signal = listAgents.mock.calls.at(-1)![0] as AbortSignal;
  const calls = listAgents.mock.calls.length;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => {
    vi.advanceTimersByTime(10000);
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(listAgents).toHaveBeenCalledTimes(calls);
  vi.useRealTimers();
});

it("ignores stale responses even when a transport ignores AbortSignal", async () => {
  let resolveOld!: (value: unknown) => void;
  listAgents.mockReturnValueOnce(
    new Promise((resolve) => {
      resolveOld = resolve;
    }),
  );
  const view = renderHook(useActiveRoute);
  listAgents.mockResolvedValue(agent("Latest"));
  await act(async () => {
    window.dispatchEvent(new Event("agent-route-changed"));
  });
  expect(view.result.current).toBe("Latest");
  await act(async () => resolveOld(agent("Stale")));
  expect(view.result.current).toBe("Latest");
  view.unmount();
});
