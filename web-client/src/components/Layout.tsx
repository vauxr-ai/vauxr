import type { ReactNode } from "react";
interface Props {
  sidebar: ReactNode;
  main: ReactNode;
  talk: ReactNode;
  expanded?: boolean;
}
/** Management and the stable voice owner occupy separate desktop space. */
export default function Layout({ sidebar, main, talk, expanded }: Props) {
  return (
    <div className={`app-shell ${expanded ? "companion-expanded" : ""}`}>
      {sidebar}
      <main id="management" className="management">
        {main}
      </main>
      {talk}
    </div>
  );
}
