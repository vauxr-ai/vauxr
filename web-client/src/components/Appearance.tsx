import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export type Theme = "light" | "dark" | "system";
const AppearanceContext = createContext<{
  theme: Theme;
  setTheme: (theme: Theme) => void;
}>({ theme: "system", setTheme: () => {} });

export const useAppearance = () => useContext(AppearanceContext);

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem("vauxr-appearance");
      return saved === "light" || saved === "dark" ? saved : "system";
    } catch {
      return "system";
    }
  });

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    let frame = 0;
    const apply = () => {
      cancelAnimationFrame(frame);
      const root = document.documentElement;
      // Switch ink and surfaces together; interpolated old-theme ink can fail AA.
      root.dataset.themeSwitching = "";
      root.dataset.theme =
        theme === "system" ? (media?.matches ? "dark" : "light") : theme;
      frame = requestAnimationFrame(() => {
        frame = requestAnimationFrame(() => { delete root.dataset.themeSwitching; });
      });
    };
    apply();
    media?.addEventListener("change", apply);
    try {
      localStorage.setItem("vauxr-appearance", theme);
    } catch {
      /* Appearance still works without storage. */
    }
    return () => {
      cancelAnimationFrame(frame);
      delete document.documentElement.dataset.themeSwitching;
      media?.removeEventListener("change", apply);
    };
  }, [theme]);

  return (
    <AppearanceContext.Provider value={{ theme, setTheme }}>
      {children}
    </AppearanceContext.Provider>
  );
}

export default function Appearance({
  theme,
  onChange,
}: {
  theme: Theme;
  onChange: (theme: Theme) => void;
}) {
  return (
    <label className="appearance">
      Appearance
      <select value={theme} onChange={(e) => onChange(e.target.value as Theme)}>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
        <option value="system">System</option>
      </select>
    </label>
  );
}
