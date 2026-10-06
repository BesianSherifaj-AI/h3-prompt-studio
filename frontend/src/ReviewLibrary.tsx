import { useEffect, useId, useRef, useState } from "react";
import { Film, LoaderCircle, RefreshCw, Upload, X } from "lucide-react";
import { api } from "./api";
import VideoReview, {
  reviewVerdictLabel,
  type VideoReviewRecord,
} from "./VideoReview";
import "./ReviewLibrary.css";

export type ReviewVideo = {
  id: string;
  name: string;
  kind: "asset";
  duration?: number;
  width?: number;
  height?: number;
  video_url: string;
  review?: VideoReviewRecord;
};
export type ReviewLibraryProps = {
  active?: boolean;
  onClose: () => void;
  onUsePrompt?: (prompt: string) => void;
};
export function reviewUploadError(file: Pick<File, "name" | "size">) {
  if (!/\.(mp4|webm|mov)$/i.test(file.name))
    return `${file.name}: choose an MP4, WebM or MOV video.`;
  if (file.size === 0) return `${file.name}: the file is empty.`;
  if (file.size > 128 * 1024 * 1024)
    return `${file.name}: videos must be 128 MB or smaller.`;
  return "";
}
export default function ReviewLibrary({
  active = true,
  onClose,
  onUsePrompt,
}: ReviewLibraryProps) {
  const inputId = useId();
  const [videos, setVideos] = useState<ReviewVideo[]>([]),
    [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(false),
    [uploading, setUploading] = useState(false),
    [error, setError] = useState("");
  const [dragging, setDragging] = useState(false),
    [filter, setFilter] = useState("all"),
    [search, setSearch] = useState("");
  const [playbackError, setPlaybackError] = useState(false),
    [playbackAttempt, setPlaybackAttempt] = useState(0);
  const player = useRef<HTMLVideoElement>(null),
    dialog = useRef<HTMLDivElement>(null),
    mounted = useRef(true),
    uploadInFlight = useRef(false),
    revision = useRef(0);
  const selected = videos.find((video) => video.id === selectedId);
  const visible = videos.filter(
    (video) =>
      (!search.trim() ||
        video.name.toLowerCase().includes(search.trim().toLowerCase())) &&
      (filter === "all" || (video.review?.verdict || "unreviewed") === filter),
  );
  const refresh = async (preferId?: string) => {
    const request = ++revision.current;
    setLoading(true);
    try {
      const result = await api(
        "/reviews/library",
        undefined,
        undefined,
        undefined,
        { timeoutMs: 15_000 },
      );
      if (!mounted.current || request !== revision.current) return;
      const items: ReviewVideo[] = Array.isArray(result.videos)
        ? result.videos
        : [];
      setVideos(items);
      setSelectedId((current) =>
        items.some((video) => video.id === (preferId || current))
          ? preferId || current
          : items[0]?.id || "",
      );
    } catch (error) {
      if (mounted.current && request === revision.current)
        setError(
          error instanceof Error
            ? error.message
            : "Could not load your video library.",
        );
    } finally {
      if (mounted.current && request === revision.current) setLoading(false);
    }
  };
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      revision.current += 1;
    };
  }, []);
  useEffect(() => {
    if (!active) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    setError("");
    void refresh();
    dialog.current?.focus();
    return () => previousFocus?.focus();
  }, [active]);
  useEffect(() => {
    setPlaybackError(false);
    setPlaybackAttempt(0);
  }, [selectedId]);
  const upload = async (files: File[]) => {
    if (!files.length || uploadInFlight.current) return;
    uploadInFlight.current = true;
    setUploading(true);
    setDragging(false);
    setError("");
    const errors: string[] = [];
    let addedId = "";
    try {
      for (const file of files) {
        const validation = reviewUploadError(file);
        if (validation) {
          errors.push(validation);
          continue;
        }
        try {
          const form = new FormData();
          form.append("file", file);
          const asset = await api(
            "/reviews/import",
            undefined,
            form,
            undefined,
            { timeoutMs: 120_000 },
          );
          if (asset.media_type !== "video")
            errors.push(`${file.name}: this file has no playable video track.`);
          else if (!addedId) addedId = asset.id;
        } catch (error) {
          errors.push(
            `${file.name}: ${error instanceof Error ? error.message : "Upload failed."}`,
          );
        }
        if (!mounted.current) return;
      }
      await refresh(addedId || undefined);
      if (mounted.current && errors.length) setError(errors.join("\n"));
    } finally {
      uploadInFlight.current = false;
      if (mounted.current) setUploading(false);
    }
  };
  if (!active) return null;
  return (
    <div
      className="review-library-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialog}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${inputId}-title`}
        className="review-library"
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
          if (event.key === "Tab") {
            const nodes = Array.from(
              dialog.current?.querySelectorAll<HTMLElement>(
                "button:not(:disabled),a[href],input:not(:disabled),textarea:not(:disabled),select:not(:disabled),summary,video[controls]",
              ) || [],
            ).filter((node) => node.getClientRects().length > 0);
            if (!nodes?.length) return;
            const first = nodes[0],
              last = nodes[nodes.length - 1];
            if (
              event.shiftKey &&
              (document.activeElement === first ||
                document.activeElement === dialog.current)
            ) {
              event.preventDefault();
              last.focus();
            } else if (
              !event.shiftKey &&
              (document.activeElement === last ||
                document.activeElement === dialog.current)
            ) {
              event.preventDefault();
              first.focus();
            }
          }
        }}
      >
        <header className="review-library-header">
          <div>
            <span>YOUR LOCAL VIDEO DESK</span>
            <h2 id={`${inputId}-title`}>Watch. Decide. Improve.</h2>
            <p>
              Review a video from your computer, then turn the fixes into a
              better prompt.
            </p>
          </div>
          <button
            type="button"
            aria-label="Close video review"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>
        <div className="review-library-body">
          <aside className="review-library-sidebar">
            <div
              className={`review-library-drop ${dragging ? "dragging" : ""}`}
              onDragOver={(event) => {
                event.preventDefault();
                if (!uploading) setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                if (!uploading)
                  void upload(Array.from(event.dataTransfer.files));
              }}
            >
              <Upload size={22} />
              <strong>Drop videos here</strong>
              <span>
                MP4 · WebM · MOV
                <br />
                Up to 128 MB / 60 minutes each
              </span>
              <label
                htmlFor={inputId}
                className={`review-library-upload-button ${uploading ? "disabled" : ""}`}
              >
                {uploading ? "Adding videos…" : "Choose videos"}
              </label>
              <input
                id={inputId}
                type="file"
                accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov"
                multiple
                disabled={uploading}
                onChange={(event) => {
                  void upload(Array.from(event.target.files || []));
                  event.target.value = "";
                }}
              />
            </div>
            <div className="review-library-list-heading">
              <strong>{videos.length} saved videos</strong>
              <button
                type="button"
                disabled={loading || uploading}
                aria-label="Refresh video library"
                onClick={() => {
                  setError("");
                  void refresh();
                }}
              >
                <RefreshCw size={14} />
              </button>
            </div>
            <input
              className="review-library-search"
              aria-label="Search video library"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Find a video…"
            />
            <select
              aria-label="Filter video reviews"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            >
              <option value="all">All videos</option>
              <option value="unreviewed">Not reviewed</option>
              <option value="approved">Approved</option>
              <option value="needs_changes">Needs changes</option>
              <option value="rejected">Rejected</option>
            </select>
            {loading && (
              <p className="review-library-status" role="status">
                <LoaderCircle size={14} /> Loading library…
              </p>
            )}
            <div className="review-library-list">
              {visible.map((video) => (
                <button
                  type="button"
                  key={video.id}
                  aria-pressed={selectedId === video.id}
                  onClick={() => setSelectedId(video.id)}
                >
                  <Film size={17} />
                  <span>
                    <strong>{video.name}</strong>
                    <small>
                      {Number.isFinite(video.duration)
                        ? `${video.duration!.toFixed(1)}s · `
                        : ""}
                      {
                        reviewVerdictLabel[
                          video.review?.verdict || "unreviewed"
                        ]
                      }
                    </small>
                  </span>
                </button>
              ))}
              {!loading && !visible.length && (
                <p className="review-library-status">
                  {videos.length
                    ? "No videos match this filter."
                    : "Add your first video to start reviewing."}
                </p>
              )}
            </div>
          </aside>
          <main className="review-library-content">
            {error && (
              <div role="alert" className="review-library-error">
                {error}
              </div>
            )}
            {selected ? (
              <>
                <div className="review-library-selected">
                  <h3>{selected.name}</h3>
                  <a href={selected.video_url} download>
                    Save video
                  </a>
                </div>
                <video
                  key={`${selected.id}:${playbackAttempt}`}
                  ref={player}
                  controls
                  playsInline
                  preload="metadata"
                  src={selected.video_url}
                  aria-label={`Review video: ${selected.name}`}
                  onError={() => setPlaybackError(true)}
                  onLoadedData={() => setPlaybackError(false)}
                />
                {playbackError && (
                  <div role="alert" className="review-library-error">
                    <p>
                      Your browser could not play this video. MP4 with H.264
                      video and AAC audio works well.
                    </p>
                    <button
                      type="button"
                      onClick={() => {
                        setPlaybackError(false);
                        setPlaybackAttempt((value) => value + 1);
                      }}
                    >
                      Retry playback
                    </button>
                  </div>
                )}
                <VideoReview
                  key={selected.id}
                  kind="asset"
                  videoId={selected.id}
                  title={selected.name}
                  onSeek={(seconds) => {
                    if (player.current) {
                      player.current.currentTime = Math.min(
                        seconds,
                        Number.isFinite(player.current.duration)
                          ? player.current.duration
                          : seconds,
                      );
                      player.current.pause();
                      player.current.scrollIntoView({
                        block: "nearest",
                        behavior: "smooth",
                      });
                    }
                  }}
                  onUsePrompt={onUsePrompt}
                  onSaved={(review) =>
                    setVideos((items) =>
                      items.map((video) =>
                        video.id === review.id ? { ...video, review } : video,
                      ),
                    )
                  }
                />
              </>
            ) : (
              <div className="review-library-empty">
                <Film size={42} />
                <h3>Your videos, reviewed with care.</h3>
                <p>
                  Choose a local video on the left. Watch it, mark what works,
                  and ask Qwen for a repair prompt.
                </p>
                <span>
                  Generated takes also have a review panel beneath their player.
                </span>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
