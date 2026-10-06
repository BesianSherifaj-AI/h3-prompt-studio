import { useEffect, useId, useRef, useState } from "react";
import {
  Check,
  Clipboard,
  Download,
  LoaderCircle,
  Save,
  Sparkles,
} from "lucide-react";
import { api, downloadText } from "./api";
import "./VideoReview.css";

export const reviewCriteria = [
  "prompt_match",
  "identity",
  "motion",
  "continuity",
  "framing",
  "audio",
] as const;
export type ReviewCriterion = (typeof reviewCriteria)[number];
export type ReviewVerdict =
  "unreviewed" | "approved" | "needs_changes" | "rejected";
export type ReviewCheck = "unchecked" | "pass" | "fail";
export type VideoReviewRecord = {
  kind: "run" | "asset";
  id: string;
  verdict: ReviewVerdict;
  notes: string;
  checklist: Partial<Record<ReviewCriterion, ReviewCheck>>;
  source_prompt: string;
  updated?: number;
  ai?: {
    verdict: string;
    summary: string;
    model?: string;
    model_key?: string;
    seconds?: number;
    improved_prompt: string;
    source_prompt?: string;
    intent?: string;
    issues: {
      timestamp: number;
      severity: string;
      category: string;
      description: string;
      prompt_fix?: string;
    }[];
    limitations: string[];
    samples: { timestamp: number; url: string }[];
    media?: { duration?: number };
  } | null;
};
export const reviewVerdictLabel: Record<ReviewVerdict, string> = {
  unreviewed: "Not reviewed",
  approved: "Good · approved",
  needs_changes: "Needs changes",
  rejected: "Reject this take",
};
const criterionLabels: Record<ReviewCriterion, string> = {
  prompt_match: "Matches the prompt",
  identity: "Faces & identity",
  motion: "Motion & anatomy",
  continuity: "Continuity & objects",
  framing: "Camera & framing",
  audio: "Audio & dialogue",
};
export function reviewTimestamp(value: number) {
  if (!Number.isFinite(value) || value < 0) return "Time unknown";
  const tenths = Math.round(value * 10),
    seconds = Math.floor(tenths / 10);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}${tenths % 10 ? `.${tenths % 10}` : ""}`;
}
export function reviewRequestIsCurrent(
  request: string,
  current: string,
  mounted = true,
) {
  return mounted && request === current;
}
export function emptyVideoReview(
  kind: "run" | "asset",
  id: string,
): VideoReviewRecord {
  return {
    kind,
    id,
    verdict: "unreviewed",
    notes: "",
    checklist: {},
    source_prompt: "",
    ai: null,
  };
}
export type VideoReviewProps = {
  kind: "run" | "asset";
  videoId: string;
  title?: string;
  aiDisabled?: boolean;
  onSeek?: (seconds: number) => void;
  onUsePrompt?: (prompt: string) => void;
  onSaved?: (review: VideoReviewRecord) => void;
};

export default function VideoReview({
  kind,
  videoId,
  title,
  aiDisabled,
  onSeek,
  onUsePrompt,
  onSaved,
}: VideoReviewProps) {
  const fieldId = useId(),
    scope = `${kind}:${videoId}`,
    path = `/reviews/${kind}/${encodeURIComponent(videoId)}`;
  const currentScope = useRef(scope),
    mounted = useRef(true),
    inFlight = useRef(false);
  currentScope.current = scope;
  const [review, setReview] = useState<VideoReviewRecord>(() =>
    emptyVideoReview(kind, videoId),
  );
  const [loading, setLoading] = useState(true),
    [loaded, setLoaded] = useState(false),
    [busy, setBusy] = useState<"save" | "analyze" | "">("");
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [dirty, setDirty] = useState(false);
  const [intent, setIntent] = useState(""),
    [samples, setSamples] = useState(6),
    [attempt, setAttempt] = useState(0);
  const [repairCopied, setRepairCopied] = useState(false);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController(),
      requestScope = scope;
    setLoading(true);
    setLoaded(false);
    setError("");
    setNotice("");
    setDirty(false);
    setIntent("");
    setReview(emptyVideoReview(kind, videoId));
    setRepairCopied(false);
    void api(path, undefined, undefined, undefined, {
      signal: controller.signal,
      timeoutMs: 15_000,
    })
      .then((result: VideoReviewRecord) => {
        if (
          !controller.signal.aborted &&
          reviewRequestIsCurrent(
            requestScope,
            currentScope.current,
            mounted.current,
          )
        ) {
          setReview(result);
          setLoaded(true);
          setIntent(result.ai?.intent || "");
        }
      })
      .catch((error) => {
        if (
          !controller.signal.aborted &&
          reviewRequestIsCurrent(
            requestScope,
            currentScope.current,
            mounted.current,
          )
        )
          setError(
            error instanceof Error
              ? error.message
              : "Could not load this review.",
          );
      })
      .finally(() => {
        if (
          !controller.signal.aborted &&
          reviewRequestIsCurrent(
            requestScope,
            currentScope.current,
            mounted.current,
          )
        )
          setLoading(false);
      });
    return () => {
      mounted.current = false;
      controller.abort();
    };
  }, [scope, path, attempt, kind, videoId]);
  const edit = (patch: Partial<VideoReviewRecord>) => {
    setReview((value) => ({ ...value, ...patch }));
    setDirty(true);
    setNotice("");
  };
  const persist = async (analyze = false) => {
    if (!loaded || loading || inFlight.current || (analyze && aiDisabled))
      return;
    const requestScope = scope;
    inFlight.current = true;
    setBusy(analyze ? "analyze" : "save");
    setError("");
    setNotice("");
    const isCurrent = () =>
      reviewRequestIsCurrent(
        requestScope,
        currentScope.current,
        mounted.current,
      );
    try {
      const saved: VideoReviewRecord = await api(
        path,
        {
          verdict: review.verdict,
          checklist: review.checklist,
          notes: review.notes,
          source_prompt: review.source_prompt,
        },
        undefined,
        "PUT",
        { timeoutMs: 15_000 },
      );
      if (!isCurrent()) return;
      setReview(saved);
      setDirty(false);
      onSaved?.(saved);
      if (analyze) {
        const result: VideoReviewRecord = await api(
          `${path}/analyze`,
          {
            intent: intent.trim(),
            source_prompt: review.source_prompt,
            sample_count: samples,
          },
          undefined,
          "POST",
          { timeoutMs: 360_000 },
        );
        if (!isCurrent()) return;
        setReview(result);
        setRepairCopied(false);
        onSaved?.(result);
        setNotice("AI review saved. Play the complete video before deciding.");
      } else setNotice("Review saved on this computer.");
    } catch (error) {
      if (isCurrent())
        setError(
          error instanceof Error ? error.message : "Review failed. Try again.",
        );
    } finally {
      inFlight.current = false;
      if (isCurrent()) setBusy("");
    }
  };
  const locked = !loaded || loading || !!busy;
  const ai = review.ai;
  const staleAi =
    !!ai &&
    ((typeof ai.source_prompt === "string" &&
      ai.source_prompt !== review.source_prompt) ||
      (typeof ai.intent === "string" && ai.intent !== intent.trim()));
  const seek = (seconds: number) => {
    if (Number.isFinite(seconds) && seconds >= 0) onSeek?.(seconds);
  };
  return (
    <section
      className="video-review"
      aria-label={title ? `Review ${title}` : "Review this video"}
    >
      <header className="video-review-heading">
        <div>
          <span>LOCAL REVIEW</span>
          <h4>Is this take good enough?</h4>
        </div>
        <span className={`video-review-verdict verdict-${review.verdict}`}>
          {reviewVerdictLabel[review.verdict] || "Not reviewed"}
        </span>
      </header>
      <p className="video-review-help">
        Watch the full clip, check the action and listen to the sound. Your
        decision and notes stay saved locally.
      </p>
      {loading && <p role="status">Loading saved review…</p>}
      <fieldset disabled={locked} className="video-review-decision">
        <legend>Your decision</legend>
        <div>
          {(["approved", "needs_changes", "rejected"] as ReviewVerdict[]).map(
            (verdict) => (
              <button
                key={verdict}
                type="button"
                aria-pressed={review.verdict === verdict}
                onClick={() => edit({ verdict })}
              >
                {verdict === "approved" && <Check size={14} />}{" "}
                {reviewVerdictLabel[verdict]}
              </button>
            ),
          )}
        </div>
      </fieldset>
      <fieldset disabled={locked} className="video-review-checklist">
        <legend>Quality checklist</legend>
        {reviewCriteria.map((criterion) => (
          <label key={criterion} htmlFor={`${fieldId}-${criterion}`}>
            <span>{criterionLabels[criterion]}</span>
            <select
              id={`${fieldId}-${criterion}`}
              value={review.checklist[criterion] || "unchecked"}
              onChange={(event) =>
                edit({
                  checklist: {
                    ...review.checklist,
                    [criterion]: event.target.value as ReviewCheck,
                  },
                })
              }
            >
              <option value="unchecked">Not checked</option>
              <option value="pass">Good</option>
              <option value="fail">Needs work</option>
            </select>
          </label>
        ))}
      </fieldset>
      <label className="video-review-field" htmlFor={`${fieldId}-notes`}>
        Your notes
        <textarea
          id={`${fieldId}-notes`}
          rows={3}
          maxLength={6000}
          disabled={locked}
          value={review.notes || ""}
          onChange={(event) => edit({ notes: event.target.value })}
          placeholder="What worked? What needs fixing? Add the time, such as 0:04."
        />
      </label>
      <div className="video-review-save">
        <button
          type="button"
          disabled={locked || !dirty}
          onClick={() => void persist()}
        >
          <Save size={14} /> Save review
        </button>
        <span role="status">
          {busy === "save" ? "Saving…" : dirty ? "Unsaved changes" : notice}
        </span>
      </div>
      <details className="video-review-ai" open={!!ai}>
        <summary>
          <Sparkles size={15} /> Qwen 3.8 27B · review & repair prompt
        </summary>
        <div>
          <p className="video-review-help">
            AI examines sampled frames. Motion between samples, sound and lip
            sync still need your playback review. AI advice keeps your decision
            separate.
          </p>
          <label className="video-review-field" htmlFor={`${fieldId}-source`}>
            Prompt used for this clip
            <textarea
              id={`${fieldId}-source`}
              rows={4}
              disabled={locked}
              maxLength={16000}
              value={review.source_prompt || ""}
              onChange={(event) => edit({ source_prompt: event.target.value })}
              placeholder="Paste the original prompt, or describe what should happen."
            />
          </label>
          <label className="video-review-field" htmlFor={`${fieldId}-intent`}>
            What should AI focus on?
            <input
              id={`${fieldId}-intent`}
              disabled={locked}
              maxLength={2000}
              value={intent}
              onChange={(event) => setIntent(event.target.value)}
              placeholder="E.g. check the hand touching the cup and keep the face consistent."
            />
          </label>
          <div className="video-review-ai-actions">
            <label htmlFor={`${fieldId}-samples`}>
              Frame samples{" "}
              <select
                id={`${fieldId}-samples`}
                disabled={locked}
                value={samples}
                onChange={(event) => setSamples(Number(event.target.value))}
              >
                {[4, 6, 8].map((count) => (
                  <option value={count} key={count}>
                    {count}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="primary"
              disabled={locked || aiDisabled}
              onClick={() => void persist(true)}
            >
              {busy === "analyze" ? (
                <LoaderCircle size={15} className="video-review-spin" />
              ) : (
                <Sparkles size={15} />
              )}{" "}
              {busy === "analyze"
                ? "Reviewing with Qwen…"
                : ai
                  ? "Review again"
                  : "Analyze & improve prompt"}
            </button>
          </div>
          {busy === "analyze" && (
            <p role="status" className="video-review-help">
              Reviewing sampled frames with your local model. This can take a
              few minutes.
            </p>
          )}
          {ai && (
            <div className="video-review-result">
              <div className="video-review-result-heading">
                <strong>
                  AI assessment ·{" "}
                  {reviewVerdictLabel[ai.verdict as ReviewVerdict] ||
                    ai.verdict}
                </strong>
                {(ai.model_key || ai.model) && <small>{ai.model_key || ai.model}</small>}
              </div>
              <p>{ai.summary}</p>
              {staleAi && (
                <p className="video-review-limits" role="status">
                  This report used an earlier prompt or focus. Analyze again to
                  review your changes.
                </p>
              )}
              {!!ai.samples?.length && (
                <div
                  className="video-review-samples"
                  aria-label="Frames examined by AI"
                >
                  {ai.samples.map((frame, index) => (
                    <button
                      type="button"
                      key={`${frame.timestamp}:${index}`}
                      onClick={() => seek(frame.timestamp)}
                      disabled={
                        !onSeek ||
                        !Number.isFinite(frame.timestamp) ||
                        frame.timestamp < 0
                      }
                      aria-label={`Seek to examined frame at ${reviewTimestamp(frame.timestamp)}`}
                    >
                      <img
                        src={frame.url}
                        loading="lazy"
                        alt={`Examined frame ${index + 1}`}
                      />
                      <span>{reviewTimestamp(frame.timestamp)}</span>
                    </button>
                  ))}
                </div>
              )}
              {!!ai.issues?.length && (
                <ol className="video-review-issues">
                  {ai.issues.map((issue, index) => (
                    <li key={index}>
                      <div>
                        <button
                          type="button"
                          disabled={
                            !onSeek ||
                            !Number.isFinite(issue.timestamp) ||
                            issue.timestamp < 0
                          }
                          onClick={() => seek(issue.timestamp)}
                        >
                          {reviewTimestamp(issue.timestamp)}
                        </button>
                        <span
                          className={issue.severity === "major" ? "major" : ""}
                        >
                          {issue.severity} · {issue.category}
                        </span>
                      </div>
                      <p>{issue.description}</p>
                      {issue.prompt_fix && (
                        <small>Prompt fix: {issue.prompt_fix}</small>
                      )}
                    </li>
                  ))}
                </ol>
              )}
              {!!ai.limitations?.length && (
                <div className="video-review-limits">
                  <strong>Evidence limits</strong>
                  <ul>
                    {ai.limitations.map((limit, index) => (
                      <li key={index}>{limit}</li>
                    ))}
                  </ul>
                </div>
              )}
              {ai.improved_prompt && (
                <div className="video-review-repair">
                  <label htmlFor={`${fieldId}-repair`}>
                    Repair prompt
                    <textarea
                      id={`${fieldId}-repair`}
                      value={ai.improved_prompt}
                      readOnly
                      rows={7}
                    />
                  </label>
                  <div>
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(
                            ai.improved_prompt,
                          );
                          if (
                            reviewRequestIsCurrent(
                              scope,
                              currentScope.current,
                              mounted.current,
                            )
                          )
                            setRepairCopied(true);
                        } catch {
                          setError(
                            "Clipboard is unavailable. Select the repair prompt above or download it.",
                          );
                        }
                      }}
                    >
                      <Clipboard size={14} />{" "}
                      {repairCopied ? "Copied" : "Copy prompt"}
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        downloadText(
                          `review-${kind}-${videoId}-prompt.txt`,
                          ai.improved_prompt,
                        )
                      }
                    >
                      <Download size={14} /> Download
                    </button>
                    {onUsePrompt && (
                      <button
                        type="button"
                        className="primary"
                        disabled={staleAi || locked}
                        onClick={() => onUsePrompt(ai.improved_prompt)}
                      >
                        Use in prompt writer
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </details>
      {error && (
        <div role="alert" className="video-review-error">
          <p>{error}</p>
          {!loaded && !loading && !busy && (
            <button
              type="button"
              onClick={() => setAttempt((value) => value + 1)}
            >
              Reload saved review
            </button>
          )}
        </div>
      )}
    </section>
  );
}
