import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CATEGORY_STYLE, IN_PROGRESS, api, qs, type Clip, type Facets, type Streamer } from "../api";
import { ClipCard, ClipCardSkeleton } from "../components/ClipCard";
import { Button, Chip, Empty, Icon, LargeTitle, Segmented, Spinner, Toggle, useToast } from "../components/ui";

type Filters = {
  sort: string;
  game: string;
  min_views: string;
  max_duration: string;
  min_energy: string;
  date_from: string;
  date_to: string;
  starred: boolean;
  verified_only: boolean;
  needs_review: boolean;
  status: string;
};

const EMPTY_FILTERS: Filters = {
  sort: "newest", game: "", min_views: "", max_duration: "", min_energy: "", date_from: "", date_to: "",
  starred: false, verified_only: false, needs_review: false, status: "",
};

const PAGE = 48;

export function ClipsPage({ streamers, onOpen, refreshKey, goTo, initialStreamerId = null }: {
  streamers: Streamer[];
  initialStreamerId?: number | null;
  onOpen: (id: string) => void;
  refreshKey: number;
  goTo: (tab: "streamers" | "settings") => void;
}) {
  const toast = useToast();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [streamerId, setStreamerId] = useState<number | null>(initialStreamerId);
  const [category, setCategory] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [showFilters, setShowFilters] = useState(false);
  const [clips, setClips] = useState<Clip[]>([]);
  const [total, setTotal] = useState(0);
  const [mode, setMode] = useState("browse");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const searchRef = useRef<HTMLInputElement>(null);
  const reqId = useRef(0);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  // "/" or Ctrl+K focuses search, like Spotlight.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.tagName?.match(/INPUT|TEXTAREA|SELECT/);
      if ((e.key === "/" && !typing) || (e.key === "k" && (e.metaKey || e.ctrlKey))) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const params = useMemo(
    () => ({ q: debounced, streamer_id: streamerId ?? undefined, category, ...filters }),
    [debounced, streamerId, category, filters],
  );

  const load = useCallback(async (append = false, silent = false) => {
    const id = ++reqId.current;
    if (append) setLoadingMore(true);
    else if (!silent) setLoading(true);
    try {
      const offset = append ? clips.length : 0;
      const limit = append ? PAGE : Math.max(PAGE, silent ? clips.length : PAGE);
      const res = await api.get<{ total: number; results: Clip[]; mode: string }>(`/api/clips${qs({ ...params, limit: Math.min(limit, 200), offset })}`);
      if (id !== reqId.current) return;
      setClips((prev) => (append ? [...prev, ...res.results] : res.results));
      setTotal(res.total);
      setMode(res.mode);
    } catch (e) {
      if (!silent) toast((e as Error).message, "error");
    } finally {
      if (id === reqId.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, clips.length, toast]);

  useEffect(() => {
    load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, refreshKey]);

  useEffect(() => {
    api.get<Facets>(`/api/facets${qs({ streamer_id: streamerId ?? undefined })}`).then(setFacets).catch(() => {});
  }, [streamerId, refreshKey]);

  // Refresh quietly while any visible clip is being processed.
  const anyBusy = clips.some((c) => IN_PROGRESS.has(c.status));
  useEffect(() => {
    if (!anyBusy) return;
    const t = setInterval(() => load(false, true), 4000);
    return () => clearInterval(t);
  }, [anyBusy, load]);

  // Infinite scroll
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && !loading && !loadingMore && clips.length < total) load(true);
    }, { rootMargin: "600px" });
    io.observe(el);
    return () => io.disconnect();
  }, [clips.length, total, loading, loadingMore, load]);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const bulk = async (kind: "analyze" | "resolve" | "queue" | "star") => {
    const ids = [...selected];
    try {
      if (kind === "analyze") {
        await api.post("/api/clips/analyze", { ids });
        toast(`Queued ${ids.length} clip${ids.length === 1 ? "" : "s"}`);
      } else if (kind === "resolve") {
        const r = await api.post<{ imported: number; already_there: number; errors: string[] }>("/api/resolve/send", { ids });
        toast(`Resolve: ${r.imported} imported${r.errors.length ? `, ${r.errors.length} skipped` : ""}`, r.errors.length && !r.imported ? "error" : "ok");
      } else if (kind === "queue") {
        await api.post("/api/resolve/queue", { ids });
        toast("Queued. Run Workspace › Scripts › Clip Manager in Resolve.");
      } else {
        await Promise.all(ids.map((id) => api.patch(`/api/clips/${encodeURIComponent(id)}`, { starred: true })));
        toast("Starred");
      }
      setSelected(new Set());
      setSelecting(false);
      load(false, true);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const activeFilterCount = Object.entries(filters).filter(([k, v]) => k !== "sort" && v).length;
  const counts = facets?.counts;
  const categories = facets?.categories ?? [];

  if (!streamers.length) {
    return (
      <Empty icon="clips" title="No clips yet">
        Add a streamer and their clips will show up here, sorted and searchable by what actually happens in them.
        <div className="mt-5 flex justify-center gap-2">
          <Button kind="filled" onClick={() => goTo("streamers")}><Icon name="plus" size={18} /> Add a streamer</Button>
          <Button kind="gray" onClick={() => goTo("settings")}>Settings</Button>
        </div>
      </Empty>
    );
  }

  return (
    <div>
      <LargeTitle
        title="Clips"
        subtitle={counts ? `${counts.total.toLocaleString()} clips · ${counts.analyzed.toLocaleString()} analysed${counts.needs_review ? ` · ${counts.needs_review} to check` : ""}` : undefined}
        right={
          <Button kind={selecting ? "filled" : "gray"} className="!px-3 !py-1.5 !text-[14px]" onClick={() => { setSelecting(!selecting); setSelected(new Set()); }}>
            {selecting ? "Done" : "Select"}
          </Button>
        }
      />

      {/* search */}
      <div className="sticky top-0 z-30 px-4 pb-2 pt-1 sm:px-6 glass">
        <div className="flex items-center gap-2">
          <label className="flex flex-1 items-center gap-2 rounded-[11px] bg-fill2 px-3 py-2">
            <Icon name="search" size={18} className="text-label2" />
            <input
              ref={searchRef}
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Describe the moment — “jumpscare in a horror game”"
              className="flex-1 bg-transparent text-[17px] outline-none placeholder:text-label3"
              aria-label="Search clips"
            />
            {q && (
              <button onClick={() => setQ("")} className="rounded-full bg-label3 p-0.5 text-bg" aria-label="Clear search">
                <Icon name="close" size={12} />
              </button>
            )}
          </label>
          <button
            onClick={() => setShowFilters(true)}
            className={`pressable relative rounded-[11px] p-2.5 ${activeFilterCount ? "bg-accentsoft text-accent" : "bg-fill2 text-label"}`}
            aria-label="Filters"
          >
            <Icon name="filter" size={18} />
            {activeFilterCount > 0 && <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-white">{activeFilterCount}</span>}
          </button>
        </div>

        {/* streamers */}
        {streamers.length > 1 && (
          <div className="no-scrollbar -mx-4 mt-2 flex gap-2 overflow-x-auto px-4 sm:-mx-6 sm:px-6">
            <Chip active={streamerId === null} onClick={() => setStreamerId(null)}>All streamers</Chip>
            {streamers.map((s) => (
              <Chip key={s.id} active={streamerId === s.id} onClick={() => setStreamerId(streamerId === s.id ? null : s.id)}>
                {s.profile_image_url && <img src={s.profile_image_url} alt="" className="h-5 w-5 rounded-full" />}
                {s.display_name}
              </Chip>
            ))}
          </div>
        )}

        {/* categories */}
        {categories.length > 0 && (
          <div className="no-scrollbar -mx-4 mt-2 flex gap-2 overflow-x-auto px-4 sm:-mx-6 sm:px-6">
            <Chip active={!category} onClick={() => setCategory("")}>All</Chip>
            {categories.map((c) => {
              const style = CATEGORY_STYLE[c.name] ?? CATEGORY_STYLE.Other;
              return (
                <Chip key={c.name} active={category === c.name} color={style.color} onClick={() => setCategory(category === c.name ? "" : c.name)}>
                  {style.emoji} {c.name} <span className="opacity-60">{c.n}</span>
                </Chip>
              );
            })}
          </div>
        )}
      </div>

      {debounced && !loading && (
        <p className="px-4 pt-2 text-[13px] text-label2 sm:px-6">
          {total.toLocaleString()} match{total === 1 ? "" : "es"}
          {mode === "keyword" ? " · keyword search (turn on meaning search by installing fastembed)" : " · ranked by meaning + keywords"}
        </p>
      )}

      <div className="grid grid-cols-1 gap-x-4 gap-y-6 px-4 pb-32 pt-3 sm:grid-cols-2 sm:px-6 lg:grid-cols-3 2xl:grid-cols-4">
        {loading
          ? Array.from({ length: 8 }).map((_, i) => <ClipCardSkeleton key={i} />)
          : clips.map((c) => (
              <ClipCard key={c.id} clip={c} onOpen={() => onOpen(c.id)} selecting={selecting} selected={selected.has(c.id)} onToggleSelect={() => toggle(c.id)} />
            ))}
      </div>
      {!loading && !clips.length && (
        <Empty icon="search" title={debounced ? "Nothing matches" : "No clips here yet"}>
          {debounced ? "Try describing it differently, or clear some filters." : "Clips appear as the streamer sync runs. Check the Queue tab."}
        </Empty>
      )}
      <div ref={sentinel} className="flex justify-center pb-10">{loadingMore && <Spinner size={22} />}</div>

      {/* bulk bar */}
      <AnimatePresence>
        {selecting && selected.size > 0 && (
          <motion.div
            initial={{ y: 80, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 80, opacity: 0 }}
            className="glass fixed inset-x-3 bottom-[88px] z-40 mx-auto md:bottom-6 md:left-[252px] flex max-w-xl flex-wrap items-center justify-center gap-2 rounded-[18px] p-2 shadow-[var(--shadow)]"
          >
            <span className="px-2 text-[14px] font-semibold">{selected.size} selected</span>
            <Button className="!px-3 !py-2 !text-[14px]" onClick={() => bulk("analyze")}><Icon name="sparkle" size={16} /> Analyse</Button>
            <Button className="!px-3 !py-2 !text-[14px]" onClick={() => bulk("resolve")}><Icon name="send" size={16} /> To Resolve</Button>
            <Button kind="gray" className="!px-3 !py-2 !text-[14px]" onClick={() => bulk("queue")}>Queue for Resolve</Button>
            <Button kind="gray" className="!px-3 !py-2 !text-[14px]" onClick={() => bulk("star")}><Icon name="star" size={16} /></Button>
          </motion.div>
        )}
      </AnimatePresence>

      <FilterSheet open={showFilters} onClose={() => setShowFilters(false)} filters={filters} setFilters={setFilters} facets={facets} />
    </div>
  );
}

function FilterSheet({ open, onClose, filters, setFilters, facets }: {
  open: boolean;
  onClose: () => void;
  filters: Filters;
  setFilters: (f: Filters) => void;
  facets: Facets | null;
}) {
  const set = <K extends keyof Filters>(k: K, v: Filters[K]) => setFilters({ ...filters, [k]: v });
  const field = "w-full rounded-[10px] bg-bg3 px-3 py-2 text-[15px] outline-none";
  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50">
          <motion.div className="absolute inset-0 bg-black/50" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
          <motion.div
            role="dialog"
            aria-label="Filters"
            className="absolute inset-x-0 bottom-0 mx-auto max-h-[88vh] max-w-lg overflow-y-auto rounded-t-[22px] bg-bg2 p-4 pb-[max(24px,env(safe-area-inset-bottom))]"
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", stiffness: 380, damping: 38 }}
          >
            <div className="mb-4 flex items-center justify-between">
              <button className="text-[16px] text-accent" onClick={() => setFilters(EMPTY_FILTERS)}>Reset</button>
              <h2 className="text-[17px] font-semibold">Filters</h2>
              <button className="text-[16px] font-semibold text-accent" onClick={onClose}>Done</button>
            </div>
            <div className="space-y-4">
              <div>
                <div className="mb-1.5 text-[13px] text-label2">Sort (when not searching)</div>
                <Segmented size="sm" value={filters.sort} onChange={(v) => set("sort", v)} options={[
                  { value: "newest", label: "Newest" }, { value: "views", label: "Most viewed" }, { value: "energy", label: "Hype" }, { value: "oldest", label: "Oldest" },
                ]} />
              </div>
              <div>
                <div className="mb-1.5 text-[13px] text-label2">Analysis</div>
                <Segmented size="sm" value={filters.status} onChange={(v) => set("status", v)} options={[
                  { value: "", label: "All" }, { value: "analyzed", label: "Analysed" }, { value: "pending", label: "Not yet" },
                ]} />
              </div>
              <label className="block">
                <div className="mb-1.5 text-[13px] text-label2">Game</div>
                <select className={field} value={filters.game} onChange={(e) => set("game", e.target.value)}>
                  <option value="">Any game</option>
                  {facets?.games.map((g) => <option key={g.name} value={g.name}>{g.name} ({g.n})</option>)}
                </select>
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label><div className="mb-1.5 text-[13px] text-label2">From</div><input type="date" className={field} value={filters.date_from} onChange={(e) => set("date_from", e.target.value)} /></label>
                <label><div className="mb-1.5 text-[13px] text-label2">To</div><input type="date" className={field} value={filters.date_to} onChange={(e) => set("date_to", e.target.value)} /></label>
                <label><div className="mb-1.5 text-[13px] text-label2">Min views</div><input inputMode="numeric" className={field} value={filters.min_views} onChange={(e) => set("min_views", e.target.value.replace(/\D/g, ""))} placeholder="0" /></label>
                <label><div className="mb-1.5 text-[13px] text-label2">Max length (s)</div><input inputMode="numeric" className={field} value={filters.max_duration} onChange={(e) => set("max_duration", e.target.value.replace(/\D/g, ""))} placeholder="60" /></label>
              </div>
              <div>
                <div className="mb-1.5 text-[13px] text-label2">Minimum energy</div>
                <Segmented size="sm" value={filters.min_energy} onChange={(v) => set("min_energy", v)} options={[
                  { value: "", label: "Any" }, { value: "3", label: "⚡3+" }, { value: "4", label: "⚡4+" }, { value: "5", label: "⚡5" },
                ]} />
              </div>
              <div className="overflow-hidden rounded-[12px] bg-bg3">
                {([
                  ["starred", "Starred only"],
                  ["verified_only", "Only clips the AI is sure about"],
                  ["needs_review", "Only clips that need a look"],
                ] as const).map(([k, label], i) => (
                  <div key={k} className={`flex items-center justify-between px-3 py-2 ${i < 2 ? "hairline" : ""}`}>
                    <span className="text-[15px]">{label}</span>
                    <Toggle checked={filters[k]} onChange={(v) => set(k, v)} label={label} />
                  </div>
                ))}
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
