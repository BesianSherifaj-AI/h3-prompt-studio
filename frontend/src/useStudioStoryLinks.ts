import { useEffect, useRef } from "react";
import { api, ApiError } from "./api";
import type { VideoJob } from "./VideoWorkspace";

export type StudioStoryLink = {
  kind: "continuation" | "alternate";
  storyId: string;
  runId: string;
  projectId: string;
  parent?: string;
  originalRunId?: string;
};
const KEY = "h3-studio-link-queue";
const safeId = (value: unknown): value is string =>
  typeof value === "string" && /^[a-zA-Z0-9_-]{1,200}$/.test(value);
export function normalizeStudioLinks(
  value: unknown,
  projectId?: string,
): StudioStoryLink[] {
  const rows = Array.isArray(value) ? value : value ? [value] : [];
  const seen = new Set<string>();
  return rows.flatMap((row): StudioStoryLink[] => {
    const item = {
      ...row,
      kind: row?.kind || "continuation",
      projectId: row?.projectId || projectId,
    };
    if (
      ![item.storyId, item.runId, item.projectId].every(safeId) ||
      seen.has(item.runId)
    )
      return [];
    if (
      item.kind === "continuation"
        ? !safeId(item.parent)
        : item.kind === "alternate"
          ? !safeId(item.originalRunId)
          : true
    )
      return [];
    seen.add(item.runId);
    return [item];
  });
}
export function studioLinkOperation(link: StudioStoryLink) {
  return link.kind === "alternate"
    ? {
        path: `/stories/${link.storyId}/alternates`,
        body: { run_id: link.runId, original_run_id: link.originalRunId },
      }
    : {
        path: `/stories/${link.storyId}/attach`,
        body: { run_id: link.runId, expected_parent: link.parent },
      };
}
export function studioLinkDefinitive(error: unknown) {
  return (
    error instanceof ApiError &&
    error.status >= 400 &&
    error.status < 500 &&
    ![408, 425, 429].includes(error.status)
  );
}
export async function reconcileStudioLink(
  link: StudioStoryLink,
  call: typeof api = api,
): Promise<{ status: "waiting" | "finished"; story?: any; error?: string }> {
  let job: VideoJob;
  try {
    job = await call(`/video/runs/${link.runId}`);
  } catch (error) {
    // The intent is saved before submission; the first GET can precede admission.
    if (
      error instanceof ApiError &&
      [400, 404].includes(error.status) &&
      /not found/i.test(error.message)
    )
      return { status: "waiting" };
    throw error;
  }
  if (link.kind === "continuation" && job.status !== "succeeded")
    return job.status === "failed"
      ? {
          status: "finished",
          error:
            "This clip did not finish. The previous story ending is still saved.",
        }
      : { status: "waiting" };
  const operation = studioLinkOperation(link);
  return {
    status: "finished",
    story: await call(operation.path, operation.body),
  };
}

/** Reconcile saved IDs only. This hook never submits or repeats a video render. */
export function useStudioStoryLinks(
  onStory: (story: any) => void,
  onError: (message: string) => void,
) {
  const links = useRef<StudioStoryLink[]>([]),
    loaded = useRef(false);
  const callbacks = useRef({ onStory, onError });
  callbacks.current = { onStory, onError };
  const backoff = useRef(
    new Map<string, { attempts: number; after: number }>(),
  );
  const persist = () => {
    try {
      localStorage.setItem(KEY, JSON.stringify(links.current));
    } catch {
      /* The current tab still reconciles. */
    }
  };
  const load = () => {
    if (loaded.current) return;
    loaded.current = true;
    try {
      links.current = normalizeStudioLinks(
        JSON.parse(localStorage.getItem(KEY) || "[]"),
      );
    } catch {
      /* No valid pending links. */
    }
  };
  const forget = (id: string) => {
    links.current = links.current.filter((link) => link.runId !== id);
    backoff.current.delete(id);
    persist();
  };
  const register = (link: StudioStoryLink) => {
    load();
    if (!normalizeStudioLinks(link).length)
      throw new Error(
        "This story link is incomplete. The video was not submitted.",
      );
    const existing = links.current.find((item) => item.runId === link.runId);
    if (
      existing &&
      JSON.stringify(studioLinkOperation(existing)) !==
        JSON.stringify(studioLinkOperation(link))
    )
      throw new Error(
        "This request already belongs to a different story ending.",
      );
    links.current = [
      ...links.current.filter((item) => item.runId !== link.runId),
      link,
    ];
    persist();
  };
  const restore = (projectId: string) => {
    load();
    try {
      const key = `h3-studio-link:${projectId}`;
      const legacy = normalizeStudioLinks(
        JSON.parse(localStorage.getItem(key) || "null"),
        projectId,
      );
      for (const link of legacy) register(link);
      localStorage.removeItem(key);
    } catch {
      /* Existing malformed browser data cannot submit anything. */
    }
  };
  useEffect(() => {
    load();
    let alive = true,
      timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      for (const link of [...links.current]) {
        if (!alive) break;
        if ((backoff.current.get(link.runId)?.after || 0) > Date.now())
          continue;
        try {
          const result = await reconcileStudioLink(link);
          if (!alive) break;
          if (result.status === "finished") {
            if (result.story) callbacks.current.onStory(result.story);
            if (result.error) callbacks.current.onError(result.error);
            forget(link.runId);
          } else
            backoff.current.set(link.runId, {
              attempts: 0,
              after: Date.now() + 1500,
            });
        } catch (error) {
          if (!alive) break;
          if (studioLinkDefinitive(error)) {
            forget(link.runId);
            callbacks.current.onError(
              `Could not link this take: ${(error as Error).message}`,
            );
          } else {
            const attempts =
              (backoff.current.get(link.runId)?.attempts || 0) + 1;
            backoff.current.set(link.runId, {
              attempts,
              after:
                Date.now() + Math.min(30000, 1500 * 2 ** Math.min(attempts, 5)),
            });
            if (attempts === 1)
              callbacks.current.onError(
                "Your video is saved. Reconnecting its story link automatically; no video will be generated again.",
              );
          }
        }
      }
      if (alive) timer = setTimeout(poll, 1500);
    };
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);
  return { register, restore, forget };
}
