import { useEffect } from "react";
import { api, fmtDuration, type Job, type QueueInfo } from "../api";
import { Button, Empty, Icon, LargeTitle, ProgressRing, Spinner, useToast } from "../components/ui";

const KIND_LABEL = { sync: "Fetching clips", analyze: "Watching clip", verify: "Double-checking" } as const;

export function QueuePage({ jobs, counts, info, reload, onOpen, onFix }: {
  jobs: Job[];
  counts: Record<string, number>;
  info: QueueInfo;
  reload: () => void;
  onOpen: (clipId: string) => void;
  onFix: () => void;
}) {
  const toast = useToast();
  useEffect(() => {
    reload(); // fresh numbers as soon as the tab opens
  }, [reload]);
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast(ok);
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const running = jobs.filter((j) => j.status === "running");
  const queued = jobs.filter((j) => j.status === "queued");
  const finished = jobs.filter((j) => !["running", "queued"].includes(j.status));

  const paused = info.paused;
  const subtitle = paused
    ? `Paused · ${(counts.queued ?? 0).toLocaleString()} waiting`
    : `${counts.running ?? 0} running · ${(counts.queued ?? 0).toLocaleString()} waiting${info.eta_seconds ? ` · ${fmtDuration(info.eta_seconds)} left` : ""}`;

  return (
    <div className="mx-auto max-w-2xl px-4 pb-32 sm:px-6">
      <LargeTitle title="Queue" subtitle={subtitle} />

      {paused && (
        <div role="alert" className="mb-4 rounded-[14px] p-3.5" style={{ background: "color-mix(in srgb, var(--red) 14%, transparent)" }}>
          <div className="text-[15px] font-semibold" style={{ color: "var(--red)" }}>
            {paused.auto ? "Analysis paused: the same problem kept happening" : "Analysis paused"}
          </div>
          {paused.auto && <div className="mt-1 text-[14px] text-label">{paused.reason}</div>}
          <div className="mt-1 text-[13px] text-label2">
            {paused.auto
              ? "Waiting clips are kept. Fix the problem, then press Resume."
              : "Waiting clips are kept. Nothing new is analysed until you resume."}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {paused.auto && <Button kind="gray" onClick={onFix}>Fix it: open setup</Button>}
            <Button onClick={() => act(() => api.post("/api/jobs/resume"), "Resumed")}>Resume</Button>
          </div>
        </div>
      )}

      <div className="mb-6 grid grid-cols-3 gap-2">
        <Stat label="Waiting" value={counts.queued ?? 0} />
        <Stat label="Done" value={counts.done ?? 0} color="var(--green)" />
        <Stat label="Failed" value={counts.error ?? 0} color={counts.error ? "var(--red)" : undefined} />
      </div>

      {!!info.error_groups.length && (
        <section className="mb-6" aria-label="Why clips failed">
          <h3 className="mb-1.5 px-4 text-[13px] uppercase tracking-wide text-label2">Why clips failed</h3>
          <ul className="overflow-hidden rounded-[14px] bg-bg2">
            {info.error_groups.map((g, i, arr) => (
              <li key={g.reason} className={`flex items-start gap-3 px-3 py-2.5 text-[14px] ${i < arr.length - 1 ? "hairline" : ""}`}>
                <span className="shrink-0 font-semibold tabular-nums" style={{ color: "var(--red)" }}>{g.count.toLocaleString()}×</span>
                <span className="min-w-0 break-words text-label">{g.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="mb-6 flex flex-wrap gap-2">
        {!paused && !!counts.queued && <Button kind="gray" onClick={() => act(() => api.post("/api/jobs/pause"), "Paused")}>Pause</Button>}
        {!!counts.queued && <Button kind="gray" onClick={() => act(() => api.post("/api/jobs/cancel-queued"), "Stopped waiting jobs")}>Stop waiting jobs</Button>}
        {!!counts.error && <Button onClick={() => act(() => api.post("/api/jobs/retry-failed"), "Retrying failed clips")}><Icon name="refresh" size={18} /> Retry failed</Button>}
        {!!finished.length && <Button kind="gray" onClick={() => act(() => api.post("/api/jobs/clear-finished"), "Cleared")}>Clear finished</Button>}
      </div>

      {!jobs.length && <Empty icon="queue" title="All caught up">Nothing is running. Add a streamer or analyse some clips.</Empty>}

      {[["Now", running], ["Up next", queued.slice(0, 50)], ["Recent", finished.slice(0, 50)]].map(([title, list]) =>
        (list as Job[]).length ? (
          <section key={title as string} className="mb-6">
            <h3 className="mb-1.5 px-4 text-[13px] uppercase tracking-wide text-label2">{title as string}{title === "Up next" && queued.length > 50 ? ` (showing 50 of ${queued.length})` : ""}</h3>
            <ul className="overflow-hidden rounded-[14px] bg-bg2">
              {(list as Job[]).map((j, i, arr) => (
                <li key={j.id} className={`flex items-center gap-3 px-3 py-2.5 ${i < arr.length - 1 ? "hairline" : ""}`}>
                  <button className="flex min-w-0 flex-1 items-center gap-3 text-left" onClick={() => j.clip_id && onOpen(j.clip_id)} disabled={!j.clip_id}>
                    {j.thumbnail_url ? (
                      <img src={j.thumbnail_url} alt="" className="h-10 w-[72px] shrink-0 rounded-[8px] object-cover" />
                    ) : (
                      <div className="flex h-10 w-[72px] shrink-0 items-center justify-center rounded-[8px] bg-fill2 text-label2"><Icon name="people" /></div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[15px] font-medium">{j.clip_title || j.streamer_name || KIND_LABEL[j.kind]}</div>
                      <div className={`truncate text-[13px] ${j.status === "error" ? "text-bad" : "text-label2"}`}>
                        {KIND_LABEL[j.kind]}{j.message ? ` · ${j.message}` : ""}
                      </div>
                    </div>
                  </button>
                  {j.status === "running" && (j.kind === "sync" ? <Spinner size={22} /> : <ProgressRing value={j.progress} />)}
                  {j.status === "queued" && (
                    <button className="rounded-full p-1.5 text-label2" onClick={() => act(() => api.post(`/api/jobs/${j.id}/cancel`), "Cancelled")} aria-label="Cancel">
                      <Icon name="close" size={16} />
                    </button>
                  )}
                  {j.status === "done" && <span className="text-good"><Icon name="check" /></span>}
                  {j.status === "error" && <span className="text-bad"><Icon name="close" /></span>}
                </li>
              ))}
            </ul>
          </section>
        ) : null,
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div className="rounded-[14px] bg-bg2 p-3">
      <div className="text-[26px] font-bold tabular-nums" style={{ color }}>{value.toLocaleString()}</div>
      <div className="text-[13px] text-label2">{label}</div>
    </div>
  );
}
