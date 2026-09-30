import { AnimatePresence, motion, useDragControls } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { CATEGORY_STYLE, IN_PROGRESS, api, fmtDate, fmtTime, fmtViews, type Clip, type QA } from "../api";
import { Button, Icon, Pill, ProgressRing, Spinner, useToast } from "./ui";

export function ClipSheet({ clipId, categories, onClose, onChanged }: {
  clipId: string | null;
  categories: string[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const drag = useDragControls();
  return (
    <AnimatePresence>
      {clipId && (
        <motion.div className="fixed inset-0 z-50" initial={{ opacity: 1 }} exit={{ opacity: 1 }}>
          <motion.div
            className="absolute inset-0 bg-black/50"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            className="absolute inset-x-0 bottom-0 mx-auto flex max-h-[94vh] max-w-3xl flex-col overflow-hidden rounded-t-[22px] bg-bg2 shadow-[var(--shadow)]"
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", stiffness: 380, damping: 38 }}
            drag="y"
            dragControls={drag}
            dragListener={false}
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0, bottom: 0.6 }}
            onDragEnd={(_, info) => {
              if (info.offset.y > 120 || info.velocity.y > 600) onClose();
            }}
          >
            <div className="flex cursor-grab justify-center pb-1 pt-2 active:cursor-grabbing" onPointerDown={(e) => drag.start(e)}>
              <div className="h-[5px] w-9 rounded-full bg-label3" />
            </div>
            <ClipDetail clipId={clipId} categories={categories} onClose={onClose} onChanged={onChanged} />
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function ClipDetail({ clipId, categories, onClose, onChanged }: { clipId: string; categories: string[]; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const [clip, setClip] = useState<Clip | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const player = useRef<HTMLVideoElement>(null);

  const load = useCallback(async () => {
    try {
      setClip(await api.get<Clip>(`/api/clips/${encodeURIComponent(clipId)}`));
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }, [clipId, toast]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while the pipeline is working on this clip.
  useEffect(() => {
    if (!clip || !IN_PROGRESS.has(clip.status)) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [clip, load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const run = async (key: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(key);
    try {
      await fn();
      if (ok) toast(ok);
      await load();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  if (!clip) {
    return (
      <div className="flex h-64 items-center justify-center text-label2">
        <Spinner size={24} />
      </div>
    );
  }

  const a = clip.analysis;
  const cat = clip.category ? CATEGORY_STYLE[clip.category] ?? CATEGORY_STYLE.Other : null;
  const working = IN_PROGRESS.has(clip.status);
  const checks = (clip.qa ?? []).filter((q) => q.kind !== "user");
  const chat = (clip.qa ?? []).filter((q) => q.kind === "user");
  const id = encodeURIComponent(clip.id);
  const seek = (t: number) => {
    const v = player.current;
    if (v) {
      v.currentTime = t;
      v.play().catch(() => {});
    }
  };

  return (
    <div className="flex-1 overflow-y-auto overscroll-contain pb-[max(24px,env(safe-area-inset-bottom))]">
      <div className="flex items-center justify-between px-4 pb-2">
        <button
          onClick={() => run("star", () => api.patch(`/api/clips/${id}`, { starred: !clip.starred }))}
          className={`pressable rounded-full p-2 ${clip.starred ? "text-yellow-400" : "text-label2"}`}
          aria-label={clip.starred ? "Unstar" : "Star"}
        >
          <Icon name="star" size={22} />
        </button>
        <button onClick={onClose} className="pressable rounded-full bg-fill2 p-1.5 text-label2" aria-label="Close">
          <Icon name="close" size={18} />
        </button>
      </div>

      <div className="px-4">
        <div className="overflow-hidden rounded-[14px] bg-black">
          {clip.has_file ? (
            <video ref={player} src={`/media/clip/${id}`} controls playsInline className="aspect-video w-full" poster={clip.thumbnail_url} />
          ) : (
            <iframe
              title={clip.title}
              src={`https://clips.twitch.tv/embed?clip=${id}&parent=${location.hostname}&autoplay=false`}
              className="aspect-video w-full"
              allowFullScreen
            />
          )}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {cat && <Pill color={cat.color}>{cat.emoji} {clip.category}</Pill>}
          {a?.secondary_categories?.map((c) => (
            <Pill key={c}>{CATEGORY_STYLE[c]?.emoji} {c}</Pill>
          ))}
          {a?.mood && <Pill>{a.mood}</Pill>}
          {a && <Pill>{"⚡".repeat(Math.max(1, a.energy))}</Pill>}
          {a?.profanity && <Pill color="#ff9f0a">Swearing</Pill>}
        </div>

        <h2 className="mt-3 text-[22px] font-bold leading-tight">{clip.title}</h2>
        <p className="mt-1 text-[14px] text-label2">
          {clip.streamer_name} · {clip.game_name || "No game"} · {fmtViews(clip.view_count)} views · {fmtDate(clip.created_at)} · clipped by {clip.creator_name}
        </p>

        {/* status */}
        {working && (
          <div className="mt-4 flex items-center gap-3 rounded-[14px] bg-fill2 p-3">
            <ProgressRing value={clip.job?.progress ?? 0.02} />
            <div>
              <div className="text-[15px] font-semibold">AI is watching this clip</div>
              <div className="text-[13px] text-label2">{clip.job?.message ?? "Queued"}</div>
            </div>
          </div>
        )}
        {clip.status === "error" && (
          <div className="mt-4 rounded-[14px] bg-[color-mix(in_srgb,var(--red)_14%,transparent)] p-3 text-[14px] text-bad">
            {clip.error}
          </div>
        )}

        {/* summary */}
        {a ? (
          <section className="mt-5">
            <div className="flex items-center justify-between">
              <h3 className="text-[13px] font-semibold uppercase tracking-wide text-label2">What happens</h3>
              <Confidence clip={clip} />
            </div>
            {editing ? (
              <EditForm clip={clip} categories={categories} onCancel={() => setEditing(false)} onSave={(patch) => run("edit", async () => { await api.patch(`/api/clips/${id}`, patch); setEditing(false); }, "Saved. Search is updated.")} />
            ) : (
              <p className="mt-1.5 text-[16px] leading-relaxed">{clip.summary}</p>
            )}
            {!!a.moments?.length && (
              <ol className="mt-3 space-y-1">
                {a.moments.map((m, i) => (
                  <li key={i}>
                    <button onClick={() => seek(m.t)} className="flex w-full gap-3 rounded-[10px] px-2 py-1.5 text-left hover:bg-fill2">
                      <span className="w-10 shrink-0 font-mono text-[13px] font-semibold text-accent">{fmtTime(m.t)}</span>
                      <span className="text-[15px]">{m.description}</span>
                    </button>
                  </li>
                ))}
              </ol>
            )}
            {!!clip.tags?.length && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {clip.tags.map((t) => (
                  <span key={t} className="rounded-full bg-fill2 px-2.5 py-1 text-[13px] text-label2">#{t}</span>
                ))}
              </div>
            )}
            {a.best_out > a.best_in && (
              <p className="mt-3 text-[13px] text-label2">
                Suggested cut: <button className="font-semibold text-accent" onClick={() => seek(a.best_in)}>{fmtTime(a.best_in)}</button> → {fmtTime(a.best_out)}
              </p>
            )}
          </section>
        ) : (
          !working && (
            <div className="mt-5 rounded-[14px] bg-fill2 p-4 text-[15px] text-label2">
              Not analysed yet. The AI will download it, look at frames, listen to the audio and describe what happens.
            </div>
          )
        )}

        {/* actions */}
        <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Button kind="filled" busy={busy === "analyze"} disabled={working} onClick={() => run("analyze", () => api.post(`/api/clips/${id}/analyze`), "Queued for analysis")}>
            <Icon name="sparkle" size={18} /> {a ? "Re-analyse" : "Analyse"}
          </Button>
          {a && (
            <Button busy={busy === "verify"} disabled={working} onClick={() => run("verify", () => api.post(`/api/clips/${id}/verify`), "Re-checked")}>
              <Icon name="shield" size={18} /> Double-check
            </Button>
          )}
          {a && (
            <Button kind="gray" busy={busy === "cross"} disabled={working} onClick={() => run("cross", () => api.post(`/api/clips/${id}/verify`, { provider: "claude" }), "Cross-checked with Claude")} title="Ask Claude to check the local AI's work">
              <Icon name="shield" size={18} /> Check with Claude
            </Button>
          )}
          {a && !editing && (
            <Button kind="gray" onClick={() => setEditing(true)}>
              <Icon name="edit" size={18} /> Correct
            </Button>
          )}
          <Button kind="gray" busy={busy === "resolve"} onClick={() => run("resolve", async () => {
            const r = await api.post<{ imported: number; already_there: number; errors: string[] }>("/api/resolve/send", { ids: [clip.id] });
            if (r.errors.length) throw new Error(r.errors.join("; "));
          }, "Sent to Resolve")} title={clip.has_file ? "Import into the open Resolve project" : "Downloads the clip, then imports it into the open Resolve project"}>
            <Icon name="send" size={18} /> To Resolve
          </Button>
          <Button kind="gray" busy={busy === "reveal"} onClick={() => run("reveal", () => api.post(`/api/clips/${id}/reveal`))} title={clip.has_file ? "Show the video file" : "Download the video and show it in its folder"}>
            <Icon name="folder" size={18} /> {clip.has_file ? "Show file" : "Download"}
          </Button>
          <Button kind="gray" onClick={() => window.open(clip.url, "_blank")}>
            <Icon name="external" size={18} /> Twitch
          </Button>
        </div>

        {/* fact checks */}
        {!!checks.length && (
          <section className="mt-7">
            <h3 className="text-[13px] font-semibold uppercase tracking-wide text-label2">How the AI double-checked itself</h3>
            <ul className="mt-2 overflow-hidden rounded-[14px] bg-bg3">
              {checks.map((c, i) => (
                <li key={c.id} className={`flex gap-3 px-3 py-2.5 ${i < checks.length - 1 ? "hairline" : ""}`}>
                  <Verdict q={c} />
                  <div className="min-w-0">
                    <div className="text-[15px] font-medium">{c.question}</div>
                    <div className="text-[14px] text-label2">{c.answer}</div>
                    {c.kind === "crosscheck" && <div className="mt-0.5 text-[12px] text-label3">Cross-check · {c.provider}</div>}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}

        <AskBox clipId={clip.id} chat={chat} disabled={!clip.frames?.length} onAsked={load} />

        {/* transcript */}
        {!!clip.transcript?.length && (
          <details className="mt-7 rounded-[14px] bg-bg3 p-3">
            <summary className="cursor-pointer text-[15px] font-semibold">Transcript</summary>
            <div className="mt-2 space-y-1">
              {clip.transcript.map((s, i) => (
                <button key={i} onClick={() => seek(s.start)} className="flex w-full gap-3 rounded-lg px-1 py-1 text-left hover:bg-fill2">
                  <span className="w-10 shrink-0 font-mono text-[13px] text-label2">{fmtTime(s.start)}</span>
                  <span className="text-[15px]">{s.text}</span>
                </button>
              ))}
            </div>
          </details>
        )}
        {clip.provider && <p className="mt-4 text-center text-[12px] text-label3">Analysed by {clip.provider}{clip.corrected ? " · corrected by you" : ""}</p>}
      </div>
    </div>
  );
}

function Confidence({ clip }: { clip: Clip }) {
  if (clip.confidence == null) return null;
  const pct = Math.round(clip.confidence * 100);
  const color = clip.corrected ? "var(--blue)" : clip.needs_review ? "var(--orange)" : "var(--green)";
  return (
    <span className="flex items-center gap-1.5 text-[13px] font-medium" style={{ color }} title="How sure the AI is, after checking itself">
      <ProgressRing value={clip.confidence} size={18} stroke={2.5} color={color} />
      {clip.corrected ? "Corrected" : clip.needs_review ? `${pct}% · needs a look` : `${pct}% sure`}
    </span>
  );
}

function Verdict({ q }: { q: QA }) {
  const [bg, icon] = q.supported === 1 ? ["var(--green)", "check"] : q.supported === 0 ? ["var(--red)", "close"] : ["var(--orange)", "?"];
  return (
    <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[13px] font-bold text-white" style={{ background: bg }}>
      {icon === "?" ? "?" : <Icon name={icon} size={15} />}
    </span>
  );
}

function AskBox({ clipId, chat, disabled, onAsked }: { clipId: string; chat: QA[]; disabled: boolean; onAsked: () => void }) {
  const toast = useToast();
  const [q, setQ] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [provider, setProvider] = useState<"" | "claude">("");
  const suggestions = ["What's the funniest moment?", "Does anyone swear?", "Who else is in this clip?", "Where would you cut it?"];

  const ask = async (question: string) => {
    if (!question.trim()) return;
    setPending(question);
    setQ("");
    try {
      await api.post(`/api/clips/${encodeURIComponent(clipId)}/ask`, { question, provider: provider || undefined });
      onAsked();
    } catch (e) {
      toast((e as Error).message, "error");
      setQ(question);
    } finally {
      setPending(null);
    }
  };

  return (
    <section className="mt-7">
      <div className="flex items-center justify-between">
        <h3 className="text-[13px] font-semibold uppercase tracking-wide text-label2">Ask about this clip</h3>
        <button onClick={() => setProvider(provider ? "" : "claude")} className="text-[13px] font-medium text-accent">
          {provider ? "Using Claude" : "Using default AI"}
        </button>
      </div>
      <div className="mt-2 space-y-2">
        {chat.map((m) => (
          <div key={m.id} className="space-y-2">
            <div className="ml-auto w-fit max-w-[85%] rounded-[18px] rounded-br-[6px] bg-accent px-3.5 py-2 text-[15px] text-white">{m.question}</div>
            <div className="w-fit max-w-[85%] rounded-[18px] rounded-bl-[6px] bg-bg3 px-3.5 py-2 text-[15px] leading-snug">{m.answer}</div>
          </div>
        ))}
        {pending && (
          <div className="space-y-2">
            <div className="ml-auto w-fit max-w-[85%] rounded-[18px] rounded-br-[6px] bg-accent px-3.5 py-2 text-[15px] text-white">{pending}</div>
            <div className="flex w-fit items-center gap-2 rounded-[18px] bg-bg3 px-3.5 py-2 text-label2"><Spinner size={14} /> Looking…</div>
          </div>
        )}
      </div>
      {!chat.length && !pending && !disabled && (
        <div className="no-scrollbar mt-2 flex gap-2 overflow-x-auto">
          {suggestions.map((s) => (
            <button key={s} onClick={() => ask(s)} className="pressable shrink-0 rounded-full bg-fill2 px-3 py-1.5 text-[13px]">{s}</button>
          ))}
        </div>
      )}
      <form
        className="mt-3 flex items-center gap-2 rounded-full bg-bg3 py-1 pl-4 pr-1"
        onSubmit={(e) => {
          e.preventDefault();
          ask(q);
        }}
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          disabled={disabled || !!pending}
          placeholder={disabled ? "Analyse the clip first" : "e.g. What does he say after he dies?"}
          className="flex-1 bg-transparent py-1.5 text-[15px] outline-none placeholder:text-label3"
        />
        <button type="submit" disabled={!q.trim() || !!pending} className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-white disabled:opacity-30" aria-label="Ask">
          <Icon name="send" size={16} />
        </button>
      </form>
    </section>
  );
}

function EditForm({ clip, categories, onSave, onCancel }: {
  clip: Clip;
  categories: string[];
  onSave: (patch: { summary: string; tags: string[]; category: string }) => void;
  onCancel: () => void;
}) {
  const [summary, setSummary] = useState(clip.summary ?? "");
  const [tags, setTags] = useState((clip.tags ?? []).join(", "));
  const [category, setCategory] = useState(clip.category ?? "Other");
  return (
    <div className="mt-2 space-y-2">
      <textarea value={summary} onChange={(e) => setSummary(e.target.value)} rows={3} className="w-full rounded-[12px] bg-bg3 p-3 text-[15px] outline-none" aria-label="Summary" />
      <input value={tags} onChange={(e) => setTags(e.target.value)} className="w-full rounded-[12px] bg-bg3 p-3 text-[15px] outline-none" placeholder="tags, comma separated" aria-label="Tags" />
      <select value={category} onChange={(e) => setCategory(e.target.value)} className="w-full rounded-[12px] bg-bg3 p-3 text-[15px] outline-none" aria-label="Category">
        {categories.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </select>
      <div className="flex gap-2">
        <Button kind="filled" onClick={() => onSave({ summary, tags: tags.split(",").map((t) => t.trim()).filter(Boolean), category })}>Save</Button>
        <Button kind="gray" onClick={onCancel}>Cancel</Button>
      </div>
      <p className="text-[12px] text-label2">Your corrections are treated as the truth and won't be overwritten by re-checks.</p>
    </div>
  );
}
