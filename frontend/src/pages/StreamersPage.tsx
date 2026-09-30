import { useState } from "react";
import { api, type Job, type Streamer } from "../api";
import { Button, Empty, Icon, LargeTitle, ProgressRing, Segmented, useToast } from "../components/ui";

const RANGES = [
  { value: "30", label: "30 days" },
  { value: "365", label: "1 year" },
  { value: "", label: "All time" },
];

export function StreamersPage({ streamers, jobs, reload, onPick, twitchReady, goTo }: {
  streamers: Streamer[];
  jobs: Job[];
  reload: () => void;
  onPick: (id: number) => void;
  twitchReady: boolean;
  goTo: (tab: "settings") => void;
}) {
  const toast = useToast();
  const [login, setLogin] = useState("");
  const [range, setRange] = useState("365");
  const [busy, setBusy] = useState<string | null>(null);

  const act = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key);
    try {
      await fn();
      toast(ok);
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  const add = () =>
    act("add", async () => {
      await api.post("/api/streamers", { login, since_days: range ? Number(range) : null });
      setLogin("");
    }, `Fetching ${login}'s clips…`);

  return (
    <div className="mx-auto max-w-2xl px-4 pb-32 sm:px-6">
      <LargeTitle title="Streamers" subtitle="Whose clips should the AI sort?" />
      {!twitchReady && (
        <button onClick={() => goTo("settings")} className="mb-4 w-full rounded-[14px] bg-accentsoft p-4 text-left">
          <div className="font-semibold text-accent">Connect Twitch first</div>
          <div className="text-[14px] text-label2">Add your free Twitch Client ID and Secret in Settings (takes 2 minutes).</div>
        </button>
      )}
      <div className="rounded-[14px] bg-bg2 p-3">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (login.trim()) add();
          }}
        >
          <input
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            placeholder="Twitch name or URL"
            className="flex-1 rounded-[10px] bg-bg3 px-3 py-2.5 text-[16px] outline-none placeholder:text-label3"
            aria-label="Streamer name"
          />
          <Button kind="filled" busy={busy === "add"} disabled={!login.trim() || !twitchReady}>
            <Icon name="plus" size={18} /> Add
          </Button>
        </form>
        <div className="mt-3 flex items-center gap-3">
          <span className="text-[13px] text-label2">Fetch clips from</span>
          <div className="flex-1"><Segmented size="sm" value={range} onChange={setRange} options={RANGES} /></div>
        </div>
      </div>

      {!streamers.length ? (
        <Empty icon="people" title="No streamers yet">Add anyone — you don't need to be their editor. Only public clips are fetched.</Empty>
      ) : (
        <ul className="mt-6 overflow-hidden rounded-[14px] bg-bg2">
          {streamers.map((s, i) => {
            const sync = jobs.find((j) => j.kind === "sync" && j.streamer_id === s.id && (j.status === "running" || j.status === "queued"));
            const analysed = s.analyzed_count ?? 0;
            return (
              <li key={s.id} className={`flex items-center gap-3 px-3 py-3 ${i < streamers.length - 1 ? "hairline" : ""}`}>
                <button onClick={() => onPick(s.id)} className="flex flex-1 items-center gap-3 text-left" aria-label={`Show ${s.display_name}'s clips`}>
                  {s.profile_image_url ? (
                    <img src={s.profile_image_url} alt="" className="h-12 w-12 rounded-full" />
                  ) : (
                    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-fill text-[18px] font-bold">{s.display_name[0]}</div>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="text-[17px] font-semibold">{s.display_name}</div>
                    <div className="text-[13px] text-label2">
                      {sync ? sync.message || "Fetching clips…" : `${s.clip_count.toLocaleString()} clips · ${analysed.toLocaleString()} analysed`}
                    </div>
                    {!sync && s.clip_count > 0 && (
                      <div className="mt-1.5 h-1 w-full max-w-48 overflow-hidden rounded-full bg-fill">
                        <div className="h-full rounded-full bg-accent" style={{ width: `${(analysed / s.clip_count) * 100}%` }} />
                      </div>
                    )}
                  </div>
                </button>
                {sync ? (
                  <ProgressRing value={sync.progress} />
                ) : (
                  <div className="flex gap-1">
                    <button className="pressable rounded-full p-2 text-accent" title="Fetch new clips" aria-label="Fetch new clips" onClick={() => act(`sync${s.id}`, () => api.post(`/api/streamers/${s.id}/sync`), "Checking for new clips")}>
                      <Icon name="refresh" size={20} />
                    </button>
                    {analysed < s.clip_count && (
                      <button className="pressable rounded-full p-2 text-accent" title="Analyse every clip" aria-label="Analyse every clip" onClick={() => act(`an${s.id}`, () => api.post("/api/clips/analyze", { streamer_id: s.id }), "Queued. Most-viewed clips go first.")}>
                        <Icon name="sparkle" size={20} />
                      </button>
                    )}
                    <button
                      className="pressable rounded-full p-2 text-bad"
                      title="Remove"
                      aria-label="Remove streamer"
                      onClick={() => confirm(`Remove ${s.display_name} and their clip analysis? Downloaded video files are kept.`) && act(`del${s.id}`, () => api.del(`/api/streamers/${s.id}`), "Removed")}
                    >
                      <Icon name="trash" size={20} />
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-3 px-2 text-[13px] text-label2">
        New clips are analysed automatically (you can turn that off in Settings). The AI works on the most-viewed clips first when you press ✦.
      </p>
    </div>
  );
}
