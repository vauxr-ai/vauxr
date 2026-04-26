import type { SVGProps } from "react";

export type IconName =
  | "connection"
  | "channels"
  | "devices"
  | "api"
  | "settings"
  | "logo"
  | "mic"
  | "mic-off"
  | "volume"
  | "volume-off"
  | "stop"
  | "refresh"
  | "plus"
  | "trash"
  | "rotate-key"
  | "copy"
  | "chevron-right";

interface Props extends SVGProps<SVGSVGElement> {
  name: IconName;
  size?: number;
}

// Lucide-style stroked icons. Single source of truth so we can swap the set later.
export default function Icon({ name, size = 18, className, ...rest }: Props) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    className,
    ...rest,
  };

  switch (name) {
    case "connection":
      return (
        <svg {...common}>
          <path d="M5 12.55a11 11 0 0 1 14 0" />
          <path d="M8.5 16.05a6 6 0 0 1 7 0" />
          <line x1="12" y1="20" x2="12.01" y2="20" />
          <path d="M2 8.82a15 15 0 0 1 20 0" />
        </svg>
      );
    case "channels":
      return (
        <svg {...common}>
          <rect x="3" y="3" width="7" height="7" rx="1.5" />
          <rect x="14" y="3" width="7" height="7" rx="1.5" />
          <rect x="3" y="14" width="7" height="7" rx="1.5" />
          <rect x="14" y="14" width="7" height="7" rx="1.5" />
        </svg>
      );
    case "devices":
      return (
        <svg {...common}>
          <rect x="2" y="6" width="14" height="12" rx="2" />
          <path d="M16 10h4a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-4" />
          <line x1="6" y1="22" x2="12" y2="22" />
          <line x1="9" y1="18" x2="9" y2="22" />
        </svg>
      );
    case "api":
      return (
        <svg {...common}>
          <path d="M9 5h-2a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h2" />
          <path d="M15 5h2a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-2" />
          <line x1="12" y1="8" x2="12" y2="16" />
          <line x1="9" y1="12" x2="15" y2="12" />
        </svg>
      );
    case "settings":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      );
    case "logo":
      // Stylised "V" inside a rounded square — Vauxr mark
      return (
        <svg {...common} fill="currentColor" stroke="none">
          <rect x="2" y="2" width="20" height="20" rx="5" opacity="0.15" />
          <path d="M7 7l5 10 5-10h-2.5l-2.5 5.5L9.5 7H7z" />
        </svg>
      );
    case "mic":
      return (
        <svg {...common}>
          <rect x="9" y="3" width="6" height="11" rx="3" />
          <path d="M19 11a7 7 0 0 1-14 0" />
          <line x1="12" y1="18" x2="12" y2="22" />
          <line x1="8" y1="22" x2="16" y2="22" />
        </svg>
      );
    case "mic-off":
      return (
        <svg {...common}>
          <line x1="2" y1="2" x2="22" y2="22" />
          <path d="M9 9v3a3 3 0 0 0 5.12 2.12" />
          <path d="M15 9.34V5a3 3 0 0 0-5.94-.6" />
          <path d="M19 11a7 7 0 0 1-1.11 3.78" />
          <path d="M5 11a7 7 0 0 0 11.91 4.91" />
          <line x1="12" y1="18" x2="12" y2="22" />
          <line x1="8" y1="22" x2="16" y2="22" />
        </svg>
      );
    case "volume":
      return (
        <svg {...common}>
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
          <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
          <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
        </svg>
      );
    case "volume-off":
      return (
        <svg {...common}>
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
          <line x1="22" y1="9" x2="16" y2="15" />
          <line x1="16" y1="9" x2="22" y2="15" />
        </svg>
      );
    case "stop":
      return (
        <svg {...common}>
          <rect x="6" y="6" width="12" height="12" rx="1.5" />
        </svg>
      );
    case "refresh":
      return (
        <svg {...common}>
          <path d="M21 12a9 9 0 0 1-15.5 6.36L3 16" />
          <path d="M3 12a9 9 0 0 1 15.5-6.36L21 8" />
          <polyline points="21 3 21 8 16 8" />
          <polyline points="3 21 3 16 8 16" />
        </svg>
      );
    case "plus":
      return (
        <svg {...common}>
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
      );
    case "trash":
      return (
        <svg {...common}>
          <polyline points="3 6 5 6 21 6" />
          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
          <path d="M10 11v6M14 11v6" />
          <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
        </svg>
      );
    case "rotate-key":
      return (
        <svg {...common}>
          <circle cx="8" cy="15" r="4" />
          <line x1="10.85" y1="12.15" x2="20" y2="3" />
          <line x1="18" y1="5" x2="20" y2="7" />
          <line x1="15" y1="8" x2="18" y2="11" />
        </svg>
      );
    case "copy":
      return (
        <svg {...common}>
          <rect x="9" y="9" width="13" height="13" rx="2" />
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
      );
    case "chevron-right":
      return (
        <svg {...common}>
          <polyline points="9 18 15 12 9 6" />
        </svg>
      );
  }
}
