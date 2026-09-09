import type { ReactNode } from "react";
import type { MoveQueue } from "./GameActionQueue";
import { storyTurnLabel, type StoryTurn } from "./storyTypes";

export function continuityReviewDetails(turn?: StoryTurn) {
  const observation = turn?.observation as { continuity_checks?: unknown } | undefined;
  return Array.isArray(observation?.continuity_checks) ? observation.continuity_checks.flatMap(check =>
    check && check.status === "mismatch" && typeof check.detail === "string" ? [check.detail] : []) : [];
}

export function GameTurnProgress({ turn, queue, now, onReview, children }: {
  turn?: StoryTurn; queue: MoveQueue; now: number; onReview: () => void; children?: ReactNode;
}) {
  const review = !!turn && ["awaiting_review", "awaiting_acceptance", "inspection_failed"].includes(turn.status);
  const localMovement = turn?.planning_mode === "deterministic_movement";
  const navigation = turn?.navigation_move as { status?: string; return_run_id?: string } | undefined;
  const working = !!turn && ["planning", "assets", "rendering", "observing", "stopping", "awaiting_assistant", "uncertain"].includes(turn.status);
  const queued = queue.items.find(item => !item.submitted);
  const sending = queue.items.find(item => item.submitted && item.id !== turn?.id && item.id !== turn?.request_id);
  const step = review ? 3 : working ? ["rendering", "observing"].includes(turn!.status) ? 2 : 1 : sending ? 1 : queued ? 0 : 4;
  const title = review ? turn?.status === "awaiting_review" ? "Your response is ready for review" : "New video ready · your review is needed" : working ? storyTurnLabel(turn) : sending ? "Sending your move · waiting for acknowledgement" : queue.paused && queued ? "Your move is queued and paused" : queued ? `Move queued · starts in ${Math.max(0, Math.ceil((queued.readyAt - now) / 1000))}s` : turn?.status === "failed" ? "Your last move needs attention" : "Ready for your next move";
  return <section className={`game-turn-feedback${review ? " needs-review" : ""}`} aria-label="Current move progress" aria-live="polite">
    <strong>{title}</strong>
    {(working || review) && turn?.message ? <p className="game-current-move">{turn.message}</p> : (sending || queued) && <p className="game-current-move">{(sending || queued)?.message}</p>}
    <ol aria-label="Move stages">{["Queued", localMovement ? "Preparing movement" : "Writing", "Rendering", "Review", "Your next move"].map((label, index) => <li key={label} aria-current={index === step ? "step" : undefined}>{label}</li>)}</ol>
    {localMovement && <p>Movement uses the saved frame and game rules. No language model is called for this turn.</p>}
    {localMovement && navigation?.status === "ready" && navigation.return_run_id && <p>This movement uses the previously saved view of that position.</p>}
    {review && <>
      <p>{turn?.status === "awaiting_review" ? "Approve the response to render it. Your queued moves wait until this scene finishes." : "The new video is ready. Choose which outcome to keep before the next queued move can start."}</p>
      {continuityReviewDetails(turn).map((detail, index) => <p className="game-review-reason" key={index}>{detail}</p>)}
      <div className="game-review-actions">{children}<button type="button" className="quiet" onClick={onReview}>Review details</button></div>
    </>}
    {!review && turn?.status === "failed" && <button type="button" onClick={onReview}>Review failed move</button>}
    {queued && (review || working || queue.paused) && <p>{queue.items.filter(item => !item.submitted).length} move(s) queued{review ? " · waiting for your review" : queue.paused ? " · queue paused" : " · waiting for the current scene"}.</p>}
    {queue.error && <p role="alert">{queue.error}</p>}
  </section>;
}
