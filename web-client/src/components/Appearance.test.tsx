import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Appearance, { AppearanceProvider, useAppearance } from "./Appearance";
function Control() {
  const { theme, setTheme } = useAppearance();
  return <Appearance theme={theme} onChange={setTheme} />;
}
it("persists explicit appearance and follows OS changes only in System mode", async () => {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => values.get(key),
    setItem: (key: string, value: string) => values.set(key, value),
  });
  let listener = () => {};
  const media = {
    matches: false,
    addEventListener: (_: string, fn: () => void) => {
      listener = fn;
    },
    removeEventListener: vi.fn(),
  };
  vi.stubGlobal("matchMedia", () => media);
  const user = userEvent.setup();
  const view = render(
    <AppearanceProvider>
      <Control />
    </AppearanceProvider>,
  );
  expect(document.documentElement.dataset.theme).toBe("light");
  act(() => {
    media.matches = true;
    listener();
  });
  expect(document.documentElement.dataset.theme).toBe("dark");
  await user.selectOptions(screen.getByLabelText("Appearance"), "light");
  act(() => listener());
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(values.get("vauxr-appearance")).toBe("light");
  view.unmount();
  render(
    <AppearanceProvider>
      <Control />
    </AppearanceProvider>,
  );
  expect(screen.getByLabelText("Appearance")).toHaveValue("light");
  vi.unstubAllGlobals();
});
