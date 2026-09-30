import { useRef, useState } from "react";
import { CATEGORY_STYLE, IN_PROGRESS, fmtDate, fmtTime, fmtViews, type Clip } from "../api";
import { Icon, Spinner } from "./ui";

export function ClipCard({ clip, onOpen, selecting, selected, onToggleSelect }: {
  clip: Clip;
  onOpen: () => void;
  selecting: boolean;
  selected: boolean;
  onToggleSelect: () => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [hover, setHover] = useState(false);
  const cat = clip.category ? CATEGORY_STYLE[clip.category] ?? CATEGORY_STYLE.Other : null;
  const busy = IN_PROGRESS.has(clip.status);
  const downloaded = !!clip.file_path;

  const startPreview = () => {
    setHover(true);
    const v = video.current;
    if (v) {
      if (clip.match) v.currentTime = Math.max(0, clip.match.t - 0.5);
      v.play().catch(() => {});
    }
  };
  const stopPreview = () => {
    setHover(false);
    video.current?.pause();
  };

  // Drag a downloaded clip straight into Resolve / Explorer (Chromium browsers).
  const onDragStart = (e: React.DragEvent) => {
    if (!downloaded) return;
    const name = clip.file_path!.split(/[\\/]/).pop();
    e.dataTransfer.setData("DownloadURL", `video/mp4:${name}:${location.origin}/media/clip/${encodeURIComponent(clip.id)}`);
    e.dataTransfer.setData("text/uri-list", `${location.origin}/media/clip/${encodeURIComponent(clip.id)}`);
  };

  return (
    <article
      className="group pressable cursor-pointer"
      onClick={selecting ? onToggleSelect : onOpen}
      onMouseEnter={startPreview}
      onMouseLeave={stopPreview}
      draggable={downloaded}
      onDragStart={onDragStart}
      data-testid="clip-card"
    >
      <div className={`relative aspect-video overflow-hidden rounded-[14px] bg-fill2 ${selected ? "ring-[3px] ring-accent" : ""}`}>
        <img src={clip.thumbnail_url} alt="" loading="lazy" onError={(e) => (e.currentTarget.style.visibility = "hidden")} className="absolute inset-0 h-full w-full object-cover transition-transform duration-500 group-hover:scale-[1.03]" />
        {downloaded && (
          <video
            ref={video}
            src={hover ? `/media/clip/${encodeURIComponent(clip.id)}` : undefined}
            muted
            playsInline
            loop
            preload="none"
            className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-300 ${hover ? "opacity-100" : "opacity-0"}`}
          />
        )}
        <div className="absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-black/60 to-transparent" />
        <span className="absolute bottom-2 right-2 rounded-md bg-black/60 px-1.5 py-0.5 text-[12px] font-semibold text-white backdrop-blur">
          {fmtTime(clip.duration)}
        </span>
        {clip.match && (
          <span className="absolute bottom-2 left-2 flex items-center gap-1 rounded-md bg-accent/90 px-1.5 py-0.5 text-[12px] font-semibold text-white">
            <Icon name="play" size={11} /> {fmtTime(clip.match.t)}
          </span>
        )}
        <div className="absolute left-2 top-2 flex gap-1.5">
          {cat && (
            <span className="glass rounded-full px-2 py-[3px] text-[12px] font-semibold">
              {cat.emoji} {clip.category}
            </span>
          )}
        </div>
        <div className="absolute right-2 top-2 flex gap-1.5">
          {busy && (
            <span className="glass flex items-center gap-1 rounded-full px-2 py-[3px] text-[12px] font-medium">
              <Spinner size={12} /> {clip.status === "queued" ? "Queued" : "Watching"}
            </span>
          )}
          {clip.status === "error" && <span className="rounded-full bg-bad px-2 py-[3px] text-[12px] font-semibold text-white">Failed</span>}
          {clip.needs_review === 1 && clip.status === "done" && (
            <span className="rounded-full bg-warn px-2 py-[3px] text-[12px] font-semibold text-black" title="The AI wasn't sure about this one">Check</span>
          )}
          {clip.starred === 1 && <span className="text-yellow-300 drop-shadow"><Icon name="star" size={18} /></span>}
        </div>
        {selecting && (
          <span className={`absolute bottom-2 right-14 flex h-6 w-6 items-center justify-center rounded-full border-2 border-white ${selected ? "bg-accent" : "bg-black/30"}`}>
            {selected && <Icon name="check" size={16} className="text-white" />}
          </span>
        )}
      </div>
      <div className="px-0.5 pt-2">
        <h3 className="line-clamp-2 text-[15px] font-semibold leading-snug">{clip.title}</h3>
        <p className="mt-0.5 text-[13px] text-label2">
          {clip.streamer_name} · {fmtViews(clip.view_count)} views · {fmtDate(clip.created_at)}
        </p>
        {clip.match ? (
          <p className="mt-1 line-clamp-2 text-[13px] text-label2">
            <span className="text-accent">“{clip.match.text}”</span>
          </p>
        ) : (
          clip.summary && <p className="mt-1 line-clamp-2 text-[13px] leading-snug text-label2">{clip.summary}</p>
        )}
      </div>
    </article>
  );
}

export function ClipCardSkeleton() {
  return (
    <div>
      <div className="skeleton aspect-video rounded-[14px]" />
      <div className="skeleton mt-2 h-4 w-4/5 rounded" />
      <div className="skeleton mt-1.5 h-3 w-1/2 rounded" />
    </div>
  );
}
