import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useState } from "react";
import { api, type Job, type QueueInfo, type Settings, type Status, type Streamer } from "./api";
import { ClipSheet } from "./components/ClipSheet";
import { Icon, ToastProvider } from "./components/ui";
import { ClipsPage } from "./pages/ClipsPage";
import { QueuePage } from "./pages/QueuePage";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupWizard } from "./pages/SetupWizard";
import { StreamersPage } from "./pages/StreamersPage";

type Tab = "clips" | "streamers" | "queue" | "settings";

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "clips", label: "Clips", icon: "clips" },
  { id: "streamers", label: "Streamers", icon: "people" },
  { id: "queue", label: "Queue", icon: "queue" },
  { id: "settings", label: "Settings", icon: "gear" },
];

function readTheme() {
  try {
    return localStorage.getItem("theme") || "system";
  } catch {
    return "system";
  }
}

export default function App() {
  const [tab, setTab] = useState<Tab>("clips");
  const [openClip, setOpenClip] = useState<string | null>(null);
  const [streamers, setStreamers] = useState<Streamer[] | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [queueInfo, setQueueInfo] = useState<QueueInfo>({ paused: null, error_groups: [], eta_seconds: null });
  const [status, setStatus] = useState<Status | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [theme, setThemeState] = useState(readTheme);
  const [setup, setSetup] = useState<number | null>(null); // wizard step to open, or null

  const setTheme = (t: string) => {
    setThemeState(t);
    try {
      localStorage.setItem("theme", t);
    } catch {
      /* private mode */
    }
  };
  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const loadStreamers = useCallback(() => api.get<Streamer[]>("/api/streamers").then(setStreamers).catch(() => setStreamers([])), []);
  const loadJobs = useCallback(
    () => api.get<{ jobs: Job[]; counts: Record<string, number> } & QueueInfo>("/api/jobs").then((r) => {
      setJobs(r.jobs);
      setCounts(r.counts);
      setQueueInfo({ paused: r.paused ?? null, error_groups: r.error_groups ?? [], eta_seconds: r.eta_seconds ?? null });
    }).catch(() => {}),
    [],
  );

  // First run: open the setup wizard until it's been completed (or skipped).
  useEffect(() => {
    api.get<{ settings: Settings }>("/api/settings").then(({ settings }) => {
      if (!settings.setup_complete) setSetup(0);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    loadStreamers();
    loadJobs();
    api.get<Status>("/api/status").then(setStatus).catch(() => {});
  }, [loadStreamers, loadJobs]);

  // Poll the queue; when work finishes, refresh the lists.
  const active = (counts.running ?? 0) + (counts.queued ?? 0);
  useEffect(() => {
    const t = setInterval(loadJobs, active ? 2000 : 8000);
    return () => clearInterval(t);
  }, [active, loadJobs]);
  const done = counts.done ?? 0;
  useEffect(() => {
    loadStreamers();
  }, [done, active, loadStreamers]);
  const syncing = jobs.some((j) => j.kind === "sync" && j.status === "running");
  useEffect(() => {
    if (!syncing) return;
    const t = setInterval(() => setRefreshKey((k) => k + 1), 5000);
    return () => clearInterval(t);
  }, [syncing]);

  const [pickedStreamer, setPickedStreamer] = useState<number | null>(null);

  return (
    <ToastProvider>
      <div className="min-h-dvh bg-bg">
        {/* Desktop: sidebar. Mobile: bottom tab bar. */}
        <aside className="glass fixed inset-y-0 left-0 z-40 hidden w-60 flex-col gap-1 p-3 hairline-top md:flex" style={{ boxShadow: "inset -0.5px 0 0 var(--sep)" }}>
          <div className="mb-4 flex items-center gap-2.5 px-2 pt-3">
            <img src="/icon.svg" alt="" className="h-8 w-8" />
            <span className="text-[17px] font-bold">Clip Manager</span>
          </div>
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`relative flex items-center gap-3 rounded-[10px] px-3 py-2 text-[15px] font-medium ${tab === t.id ? "text-accent" : "text-label"}`}
            >
              {tab === t.id && <motion.span layoutId="side-active" className="absolute inset-0 rounded-[10px] bg-accentsoft" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
              <Icon name={t.icon} size={20} className="relative" />
              <span className="relative">{t.label}</span>
              {t.id === "queue" && active > 0 && <span className="relative ml-auto rounded-full bg-accent px-1.5 text-[12px] font-bold text-white">{active}</span>}
            </button>
          ))}
          <div className="mt-auto px-2 pb-2 text-[12px] text-label3">
            {status && (status.mode === "local" ? `AI: local ${status.local.ok ? "ready" : "not ready"}` : `AI: Claude ${status.claude.ok ? "ready" : "needs key"}`)}
          </div>
        </aside>

        <main className="md:pl-60">
          <AnimatePresence mode="wait">
            <motion.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
              {tab === "clips" && streamers && (
                <ClipsPage key={pickedStreamer ?? "all"} initialStreamerId={pickedStreamer} streamers={streamers} onOpen={setOpenClip} refreshKey={refreshKey + done} goTo={setTab} />
              )}
              {tab === "streamers" && streamers && (
                <StreamersPage
                  streamers={streamers}
                  jobs={jobs}
                  reload={() => {
                    loadStreamers();
                    loadJobs();
                  }}
                  onPick={(id) => {
                    setPickedStreamer(id);
                    setTab("clips");
                  }}
                  twitchReady={!!status?.twitch}
                  goTo={setTab}
                />
              )}
              {tab === "queue" && <QueuePage jobs={jobs} counts={counts} info={queueInfo} reload={loadJobs} onOpen={setOpenClip} onFix={() => setSetup(1)} />}
              {tab === "settings" && <SettingsPage theme={theme} setTheme={setTheme} onSaved={() => api.get<Status>("/api/status").then(setStatus)} onRunSetup={(step) => setSetup(step)} />}
            </motion.div>
          </AnimatePresence>
        </main>

        <nav className="glass hairline-top fixed inset-x-0 bottom-0 z-40 flex justify-around pb-[max(8px,env(safe-area-inset-bottom))] pt-1.5 md:hidden">
          {TABS.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)} className={`relative flex w-20 flex-col items-center gap-0.5 text-[10px] font-medium ${tab === t.id ? "text-accent" : "text-label2"}`}>
              <Icon name={t.icon} size={26} />
              {t.label}
              {t.id === "queue" && active > 0 && <span className="absolute right-4 top-0 rounded-full bg-bad px-1.5 text-[11px] font-bold text-white">{active}</span>}
            </button>
          ))}
        </nav>

        {setup !== null && (
          <SetupWizard
            startAt={setup}
            onDone={() => {
              setSetup(null);
              loadStreamers();
              loadJobs();
              api.get<Status>("/api/status").then(setStatus).catch(() => {});
              setRefreshKey((k) => k + 1);
            }}
          />
        )}
        <ClipSheet clipId={openClip} categories={CATEGORY_LIST} onClose={() => setOpenClip(null)} onChanged={() => setRefreshKey((k) => k + 1)} />
      </div>
    </ToastProvider>
  );
}

const CATEGORY_LIST = [
  "Funny", "Fail", "Clutch / Highlight", "Rage", "Jumpscare / Scary", "Wholesome",
  "Chat / Donation Reaction", "IRL", "Music", "Collab / Guest", "Drama / Serious", "Other",
];
