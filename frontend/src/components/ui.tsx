import { motion } from "motion/react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

/* ---------- icons (SF Symbols-ish, 24px grid) ---------- */

const PATHS: Record<string, string> = {
  play: "M8 5.5v13l10-6.5z",
  search: "M10.5 4a6.5 6.5 0 1 0 4.03 11.6l4.43 4.43 1.06-1.06-4.43-4.43A6.5 6.5 0 0 0 10.5 4zm0 1.5a5 5 0 1 1 0 10 5 5 0 0 1 0-10z",
  clips: "M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v11a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5zm6 2.2v6.6l5.5-3.3z",
  people: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zm7.5 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2.5 19c0-3 2.9-5.5 6.5-5.5s6.5 2.5 6.5 5.5v.5h-13zm14.5.5V19c0-1.6-.6-3-1.6-4.1.4-.1.8-.1 1.1-.1 3 0 5.5 2 5.5 4.5v.2z",
  queue: "M4 6h16v1.5H4zm0 5.25h16v1.5H4zM4 16.5h10V18H4zm13 .75 3-2.25v4.5z",
  gear: "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zm8.3 5-.1-3-2.1-.4a6.6 6.6 0 0 0-.7-1.6l1.2-1.8-2.1-2.1-1.8 1.2c-.5-.3-1-.5-1.6-.7L12.7 3h-3l-.4 2.1c-.6.2-1.1.4-1.6.7L5.9 4.6 3.8 6.7 5 8.5c-.3.5-.5 1-.7 1.6L2.2 10.5l-.1 3 2.1.4c.2.6.4 1.1.7 1.6l-1.2 1.8 2.1 2.1 1.8-1.2c.5.3 1 .5 1.6.7l.4 2.1h3l.4-2.1c.6-.2 1.1-.4 1.6-.7l1.8 1.2 2.1-2.1-1.2-1.8c.3-.5.5-1 .7-1.6z",
  star: "M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.8z",
  close: "M6.4 5.3 12 10.9l5.6-5.6 1.1 1.1-5.6 5.6 5.6 5.6-1.1 1.1-5.6-5.6-5.6 5.6-1.1-1.1 5.6-5.6-5.6-5.6z",
  check: "M9.5 16.2 5.3 12l-1.1 1.1 5.3 5.3L20 7.9l-1.1-1.1z",
  filter: "M4 6h16v1.5H4zm3 5.25h10v1.5H7zm3 5.25h4V18h-4z",
  sparkle: "M12 2.5l1.9 5.6 5.6 1.9-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.9zM18.5 15l.9 2.6 2.6.9-2.6.9-.9 2.6-.9-2.6-2.6-.9 2.6-.9z",
  folder: "M3.5 6.5A1.5 1.5 0 0 1 5 5h4.4l2 2H19a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z",
  send: "M3.4 20.4 21 12 3.4 3.6 3.4 10l12.6 2-12.6 2z",
  refresh: "M12 5a7 7 0 1 0 7 7h-1.5A5.5 5.5 0 1 1 12 6.5c1.6 0 3 .7 4 1.8L13.5 11H20V4.5l-2.9 2.9A7 7 0 0 0 12 5z",
  trash: "M9 3.5h6l.5 1.5H20v1.5H4V5h4.5zM5.5 8h13l-1 12.5h-11z",
  plus: "M11.25 4h1.5v7.25H20v1.5h-7.25V20h-1.5v-7.25H4v-1.5h7.25z",
  shield: "M12 2.8 4.5 5.7v5.8c0 4.6 3.2 8.7 7.5 9.8 4.3-1.1 7.5-5.2 7.5-9.8V5.7zm-1.2 13.4-3.6-3.6 1.1-1.1 2.5 2.5 5.2-5.2 1.1 1.1z",
  chat: "M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H9l-5 4z",
  edit: "M4 16.8V20h3.2l9.4-9.4-3.2-3.2zm15.7-8.7a.9.9 0 0 0 0-1.3l-2.5-2.5a.9.9 0 0 0-1.3 0l-1.6 1.6 3.8 3.8z",
  external: "M14 4h6v6h-1.5V6.6l-7.2 7.2-1.1-1.1 7.2-7.2H14zM5 6.5A1.5 1.5 0 0 1 6.5 5H11v1.5H6.5v11h11V13H19v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 17.5z",
  chevron: "M9.3 5.3 16 12l-6.7 6.7-1.1-1.1 5.6-5.6-5.6-5.6z",
  select: "M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm.5 1.5v13h13v-13zm5.3 9.9L7.6 12.2l1-1 2.2 2.1 4.6-4.6 1 1z",
};

export function Icon({ name, size = 20, className = "" }: { name: keyof typeof PATHS | string; size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d={PATHS[name] ?? PATHS.sparkle} />
    </svg>
  );
}

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className="spin" aria-label="Loading">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function ProgressRing({ value, size = 34, stroke = 3.5, color = "var(--accent)" }: { value: number; size?: number; stroke?: number; color?: string }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} className="-rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--fill)" strokeWidth={stroke} />
      <motion.circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={c}
        animate={{ strokeDashoffset: c * (1 - Math.max(0, Math.min(1, value))) }}
        transition={{ type: "spring", stiffness: 120, damping: 20 }}
      />
    </svg>
  );
}

/* ---------- controls ---------- */

export function Segmented<T extends string>({ value, options, onChange, size = "md" }: {
  value: T;
  options: { value: T; label: ReactNode }[];
  onChange: (v: T) => void;
  size?: "sm" | "md";
}) {
  return (
    <div className={`relative flex rounded-[9px] bg-fill p-[2px] ${size === "sm" ? "text-[13px]" : "text-[14px]"}`} role="tablist">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.value)}
            className={`relative z-10 flex-1 whitespace-nowrap rounded-[7px] px-3 font-medium transition-colors ${size === "sm" ? "py-1" : "py-1.5"} ${active ? "text-label" : "text-label2"}`}
          >
            {active && (
              <motion.span
                layoutId={`seg-${options.map((x) => x.value).join("-")}`}
                className="absolute inset-0 -z-10 rounded-[7px] bg-bg4 shadow-[0_3px_8px_rgba(0,0,0,0.12),0_3px_1px_rgba(0,0,0,0.04)]"
                transition={{ type: "spring", stiffness: 500, damping: 38 }}
              />
            )}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-[31px] w-[51px] shrink-0 rounded-full transition-colors duration-200 ${checked ? "bg-good" : "bg-fill"}`}
    >
      <motion.span
        className="absolute top-[2px] h-[27px] w-[27px] rounded-full bg-white shadow-[0_3px_8px_rgba(0,0,0,0.15),0_3px_1px_rgba(0,0,0,0.06)]"
        animate={{ left: checked ? 22 : 2 }}
        transition={{ type: "spring", stiffness: 600, damping: 35 }}
      />
    </button>
  );
}

export function Chip({ active, onClick, children, color }: { active?: boolean; onClick?: () => void; children: ReactNode; color?: string }) {
  return (
    <button
      onClick={onClick}
      className={`pressable flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-3.5 py-1.5 text-[14px] font-medium transition-colors ${
        active ? "bg-label text-bg" : "bg-fill2 text-label"
      }`}
      style={active && color ? { background: color, color: "#000" } : undefined}
    >
      {children}
    </button>
  );
}

export function Pill({ children, color, className = "" }: { children: ReactNode; color?: string; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-[3px] text-[12px] font-semibold ${className}`}
      style={{ background: color ? `${color}2e` : "var(--fill)", color: color ?? "var(--label)" }}
    >
      {children}
    </span>
  );
}

export function Button({ children, onClick, kind = "tinted", disabled, busy, className = "", title }: {
  children: ReactNode;
  onClick?: () => void;
  kind?: "filled" | "tinted" | "gray" | "plain" | "danger";
  disabled?: boolean;
  busy?: boolean;
  className?: string;
  title?: string;
}) {
  const styles = {
    filled: "bg-accent text-white",
    tinted: "bg-accentsoft text-accent",
    gray: "bg-fill2 text-label",
    plain: "text-accent",
    danger: "bg-[color-mix(in_srgb,var(--red)_16%,transparent)] text-bad",
  }[kind];
  return (
    <button
      onClick={onClick}
      disabled={disabled || busy}
      title={title}
      className={`pressable inline-flex items-center justify-center gap-1.5 rounded-[12px] px-4 py-2.5 text-[15px] font-semibold disabled:opacity-40 ${styles} ${className}`}
    >
      {busy && <Spinner />}
      {children}
    </button>
  );
}

/* ---------- inset grouped lists (Settings-style) ---------- */

export function Group({ title, footer, children }: { title?: ReactNode; footer?: ReactNode; children: ReactNode }) {
  return (
    <section className="mb-8">
      {title && <h3 className="mb-1.5 px-4 text-[13px] uppercase tracking-wide text-label2">{title}</h3>}
      <div className="overflow-hidden rounded-[12px] bg-bg2">{children}</div>
      {footer && <p className="mt-1.5 px-4 text-[13px] leading-snug text-label2">{footer}</p>}
    </section>
  );
}

export function Row({ label, detail, children, onClick, last }: { label: ReactNode; detail?: ReactNode; children?: ReactNode; onClick?: () => void; last?: boolean }) {
  const Comp = onClick ? "button" : "div";
  return (
    <Comp onClick={onClick} className={`flex min-h-[44px] w-full items-center gap-3 pl-4 text-left ${onClick ? "active:bg-fill2" : ""}`}>
      <div className={`flex min-h-[44px] flex-1 items-center gap-3 py-2 pr-4 ${last ? "" : "hairline"}`}>
        <div className={children ? "max-w-[60%] shrink-0" : "flex-1"}>
          <div className="whitespace-nowrap text-[16px]">{label}</div>
          {detail && <div className="text-[13px] text-label2">{detail}</div>}
        </div>
        {children && <div className="flex min-w-0 flex-1 justify-end">{children}</div>}
      </div>
    </Comp>
  );
}

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full min-w-0 bg-transparent text-right text-[16px] text-label2 outline-none placeholder:text-label3 focus:text-label ${props.className ?? ""}`}
    />
  );
}

export function LargeTitle({ title, subtitle, right }: { title: string; subtitle?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-end justify-between gap-3 px-4 pb-2 pt-6 sm:px-6">
      <div>
        <h1 className="text-[34px] font-bold leading-tight tracking-[0.01em]">{title}</h1>
        {subtitle && <div className="text-[15px] text-label2">{subtitle}</div>}
      </div>
      {right && <div className="flex items-center gap-2 pb-1">{right}</div>}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon: string; title: string; children?: ReactNode }) {
  return (
    <div className="mx-auto flex max-w-sm flex-col items-center px-6 py-20 text-center">
      <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-[18px] bg-fill2 text-label2">
        <Icon name={icon} size={32} />
      </div>
      <h2 className="text-[20px] font-semibold">{title}</h2>
      {children && <div className="mt-2 text-[15px] leading-relaxed text-label2">{children}</div>}
    </div>
  );
}

/* ---------- toasts ---------- */

type ToastT = { id: number; text: string; kind: "ok" | "error" };
const ToastCtx = createContext<(text: string, kind?: "ok" | "error") => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastT[]>([]);
  const push = useCallback((text: string, kind: "ok" | "error" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 6000 : 3000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 top-3 z-[100] flex flex-col items-center gap-2 px-4">
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            initial={{ opacity: 0, y: -20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            className="glass pointer-events-auto flex max-w-md items-center gap-2 rounded-full px-4 py-2.5 text-[14px] font-medium shadow-[var(--shadow)]"
            role="status"
          >
            <span className={t.kind === "error" ? "text-bad" : "text-good"}>
              <Icon name={t.kind === "error" ? "close" : "check"} size={16} />
            </span>
            {t.text}
          </motion.div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
