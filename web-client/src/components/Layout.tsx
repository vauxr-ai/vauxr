import type { ReactNode } from "react";

interface Props {
  sidebar: ReactNode;
  main: ReactNode;
  talk: ReactNode;
}

/**
 * Three-column app shell: 15% sidebar / 70% main content / 15% talk panel.
 * Each column owns its own scroll; the shell never scrolls.
 */
export default function Layout({ sidebar, main, talk }: Props) {
  return (
    <div className="grid h-screen w-screen grid-cols-[15%_minmax(0,1fr)_15%] overflow-hidden bg-bg text-zinc-200">
      {sidebar}
      <main className="flex min-w-0 flex-col overflow-hidden">{main}</main>
      {talk}
    </div>
  );
}
