import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { Project } from "./model";
import {
  normalizeImageGenerators,
  storyTurnPending,
  validStoryTicket,
  type ImageGeneratorModel,
  type Story,
  type StoryPlan,
  type StorySettings,
  type StoryTicket,
  type StoryTurn,
} from "./storyTypes";

const SELECTED = "h3-game:selected-story";
const CREATE = "h3-game:pending-create";
export type PendingStoryCreation = {
  requestId: string;
  body: {
    project: Project;
    mode: "game";
    premise: string;
    player_name: string;
    source_run_id?: string;
    settings: StorySettings;
    request_id: string;
  };
};
export function readPendingStoryCreation(): PendingStoryCreation | null {
  try {
    const pending = JSON.parse(localStorage.getItem(CREATE) || "null");
    const body = pending?.body;
    return typeof pending?.requestId === "string" &&
      !!pending.requestId &&
      body?.request_id === pending.requestId &&
      body.mode === "game" &&
      typeof body.premise === "string" &&
      typeof body.player_name === "string" &&
      typeof body.project?.id === "string" &&
      Array.isArray(body.project.assets) &&
      Array.isArray(body.project.subjects) &&
      !!body.project.story &&
      Number.isFinite(body.settings?.duration) &&
      (body.source_run_id === undefined ||
        typeof body.source_run_id === "string")
      ? pending
      : null;
  } catch {
    return null;
  }
}
const ticketKey = (id: string) => `h3-game:pending:${id}`;
function readTicket(id: string): StoryTicket | null {
  try {
    const value = JSON.parse(localStorage.getItem(ticketKey(id)) || "null");
    return validStoryTicket(value, id) ? value : null;
  } catch {
    return null;
  }
}
function storeTicket(ticket: StoryTicket | null, id: string) {
  try {
    if (ticket) localStorage.setItem(ticketKey(id), JSON.stringify(ticket));
    else localStorage.removeItem(ticketKey(id));
  } catch {
    /* The server also owns request reconciliation. */
  }
}
function asStory(value: unknown): Story | null {
  const v = value as Story;
  return v && typeof v.id === "string" && Array.isArray(v.turns) && !!v.settings
    ? v
    : null;
}

/** Opening the game performs reads only. Every mutation is an explicit user action. */
export function useStorySession(skipRestore = false) {
  const [stories, setStories] = useState<Story[]>([]),
    [story, setStory] = useState<Story | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true),
    [submitting, setSubmitting] = useState(false),
    [error, setError] = useState("");
  const [pendingTicket, setPendingTicket] = useState<StoryTicket | null>(null);
  const [pendingCreation, setPendingCreation] = useState(
    readPendingStoryCreation,
  );
  const [generators, setGenerators] = useState<ImageGeneratorModel[]>([]),
    [defaultGenerator, setDefaultGenerator] = useState("");
  const selected = useRef(""),
    current = useRef<Story | null>(null),
    mutation = useRef(0),
    pollRevision = useRef(0);
  const sending = useRef(false),
    mounted = useRef(true),
    ticket = useRef<StoryTicket | null>(null);
  const createAttempt = useRef<PendingStoryCreation | null>(pendingCreation);
  const rememberCreation = useCallback((next: PendingStoryCreation | null) => {
    createAttempt.current = next;
    if (mounted.current) setPendingCreation(next);
    try {
      if (next) localStorage.setItem(CREATE, JSON.stringify(next));
      else localStorage.removeItem(CREATE);
    } catch {
      /* The in-memory snapshot and request ID still protect this session. */
    }
  }, []);

  const apply = useCallback((incoming: Story) => {
    if (!mounted.current || selected.current !== incoming.id) return;
    current.current = incoming;
    setStory(incoming);
    setStories((items) => [
      incoming,
      ...items.filter((s) => s.id !== incoming.id),
    ]);
    if (
      ticket.current?.storyId === incoming.id &&
      incoming.turns.some((t) => t.request_id === ticket.current?.requestId)
    ) {
      storeTicket(null, incoming.id);
      ticket.current = null;
      setPendingTicket(null);
    }
  }, []);
  const refresh = useCallback(
    async (id = selected.current) => {
      if (!id) return null;
      const revision = ++pollRevision.current,
        beganMutation = mutation.current;
      const incoming = asStory(await api(`/stories/${encodeURIComponent(id)}`));
      if (!incoming)
        throw new Error(
          "The story response was incomplete. Reconnect to check its status.",
        );
      if (
        revision === pollRevision.current &&
        beganMutation === mutation.current &&
        selected.current === id
      ) {
        apply(incoming);
        setError("");
      }
      return incoming;
    },
    [apply],
  );
  const selectStory = useCallback(
    async (id: string) => {
      mutation.current++;
      pollRevision.current++;
      selected.current = id;
      current.current = null;
      setSelectedId(id);
      setStory(null);
      setError("");
      setLoading(!!id);
      ticket.current = id ? readTicket(id) : null;
      setPendingTicket(ticket.current);
      try {
        if (id) localStorage.setItem(SELECTED, id);
        else localStorage.removeItem(SELECTED);
      } catch {
        /* Optional browser persistence. */
      }
      if (!id) return;
      try {
        await refresh(id);
      } catch (e) {
        if (selected.current === id) setError((e as Error).message);
      } finally {
        if (selected.current === id) setLoading(false);
      }
    },
    [refresh],
  );
  const acceptCreation = useCallback(
    (incoming: Story) => {
      rememberCreation(null);
      mutation.current++;
      selected.current = incoming.id;
      setSelectedId(incoming.id);
      ticket.current = readTicket(incoming.id);
      setPendingTicket(ticket.current);
      try {
        localStorage.setItem(SELECTED, incoming.id);
      } catch {
        /* Optional persistence. */
      }
      apply(incoming);
      return incoming;
    },
    [apply, rememberCreation],
  );
  useEffect(() => {
    mounted.current = true;
    let alive = true;
    const init = async () => {
      const results = await Promise.allSettled([
        api("/stories"),
        api("/assets/generators"),
      ]);
      if (!alive) return;
      if (results[0].status === "fulfilled") {
        const list: Story[] = Array.isArray(results[0].value?.stories)
          ? results[0].value.stories.filter(
              (item: Story) => item.mode === "game",
            )
          : [];
        setStories(list);
        try {
          const pending = createAttempt.current;
          if (pending) {
            const found = list.find(
              (item) => item.create_request_id === pending.requestId,
            );
            if (found) {
              const recovered = asStory(
                await api(`/stories/${encodeURIComponent(found.id)}`),
              );
              if (
                alive &&
                recovered &&
                createAttempt.current?.requestId === pending.requestId
              )
                acceptCreation(recovered);
            }
          }
        } catch {
          /* Keep the original creation available for an explicit retry. */
        }
        let saved = "";
        try {
          if (!skipRestore) saved = localStorage.getItem(SELECTED) || "";
        } catch {
          /* No saved selection. */
        }
        if (
          saved &&
          !createAttempt.current &&
          !selected.current &&
          list.some((s) => s.id === saved)
        )
          await selectStory(saved);
      } else setError((results[0].reason as Error).message);
      if (results[1].status === "fulfilled") {
        setGenerators(normalizeImageGenerators(results[1].value?.models));
        setDefaultGenerator(results[1].value?.default_model || "");
      }
      if (alive) setLoading(false);
    };
    void init();
    return () => {
      alive = false;
      mounted.current = false;
      pollRevision.current++;
    };
  }, [acceptCreation, selectStory, skipRestore]);
  useEffect(() => {
    if (!selectedId) return;
    let alive = true,
      timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        await refresh(selectedId);
      } catch (e) {
        if (alive && selected.current === selectedId)
          setError((e as Error).message);
      }
      if (alive)
        timer = setTimeout(
          poll,
          current.current?.turns.some(storyTurnPending) || ticket.current
            ? 1700
            : 6000,
        );
    };
    timer = setTimeout(poll, 1700);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [selectedId, refresh]);

  const runTicket = async (next: StoryTicket) => {
    if (sending.current)
      throw new Error("The previous action is still being sent.");
    if (ticket.current && ticket.current.requestId !== next.requestId)
      throw new Error(
        "Reconnect to the previous request before making another move.",
      );
    sending.current = true;
    setSubmitting(true);
    setError("");
    mutation.current++;
    ticket.current = next;
    setPendingTicket(next);
    storeTicket(next, next.storyId);
    try {
      const result = await api(next.path, next.body);
      mutation.current++;
      if (ticket.current?.requestId === next.requestId) {
        ticket.current = null;
        storeTicket(null, next.storyId);
        if (mounted.current) setPendingTicket(null);
      }
      const full = asStory(result);
      if (full) apply(full);
      else if (selected.current === next.storyId && result?.id) {
        const turn = result as StoryTurn;
        const old = current.current;
        if (old)
          apply({
            ...old,
            turns: [...old.turns.filter((t) => t.id !== turn.id), turn],
          });
      }
      await refresh(next.storyId).catch(() => {
        /* The accepted turn will be polled; never repeat it. */
      });
      return result as StoryTurn | Story;
    } catch (e) {
      mutation.current++;
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        if (ticket.current?.requestId === next.requestId) ticket.current = null;
        storeTicket(null, next.storyId);
        if (mounted.current) setPendingTicket(null);
      }
      if (mounted.current && selected.current === next.storyId)
        setError((e as Error).message);
      await refresh(next.storyId).catch(() => {});
      throw e;
    } finally {
      sending.current = false;
      if (mounted.current) setSubmitting(false);
    }
  };
  const perform = (
    id: string,
    path: string,
    body: Record<string, unknown> = {},
  ) => {
    const requestId = crypto.randomUUID();
    return runTicket({
      storyId: id,
      path,
      body: { ...body, request_id: requestId },
      requestId,
    });
  };
  const submitCreation = async (
    attempt: PendingStoryCreation,
    reconcile: boolean,
  ) => {
    if (sending.current)
      throw new Error("The previous action is still being sent.");
    sending.current = true;
    setSubmitting(true);
    setError("");
    mutation.current++;
    rememberCreation(attempt);
    let posted = false;
    try {
      let incoming: Story | null = null;
      if (reconcile) {
        const result = await api("/stories");
        const found = result?.stories?.find(
          (item: Story) => item.create_request_id === attempt.requestId,
        );
        if (found) {
          incoming = asStory(
            await api(`/stories/${encodeURIComponent(found.id)}`),
          );
          if (!incoming)
            throw new Error(
              "The original story could not be restored. Resume it again after reconnecting.",
            );
        }
      }
      if (!incoming) {
        // Only the explicit resume action may resend this exact saved request.
        posted = true;
        incoming = asStory(await api("/stories", attempt.body));
      }
      if (!incoming)
        throw new Error(
          "The new story was not confirmed. Reconnect before starting it again.",
        );
      return acceptCreation(incoming);
    } catch (e) {
      if (posted && e instanceof ApiError && e.status >= 400 && e.status < 500)
        rememberCreation(null);
      setError((e as Error).message);
      throw e;
    } finally {
      sending.current = false;
      setSubmitting(false);
    }
  };
  const create = async (
    project: Project,
    values: {
      premise: string;
      player_name: string;
      source_run_id?: string;
      settings: StorySettings;
    },
  ) => {
    if (createAttempt.current)
      throw new Error(
        "Resume original creation before starting a different game.",
      );
    const requestId = crypto.randomUUID();
    return submitCreation(
      {
        requestId,
        body: structuredClone({
          project,
          mode: "game",
          ...values,
          request_id: requestId,
        }),
      },
      false,
    );
  };
  const resumeCreation = async () => {
    if (!createAttempt.current)
      throw new Error("There is no unconfirmed game creation to resume.");
    return submitCreation(createAttempt.current, true);
  };
  const patch = async (
    changes: Partial<Pick<Story, "premise" | "player_name" | "settings">>,
  ) => {
    const id = selected.current;
    if (!id || sending.current) return;
    sending.current = true;
    setSubmitting(true);
    mutation.current++;
    try {
      const incoming = asStory(
        await api(
          `/stories/${encodeURIComponent(id)}`,
          changes,
          undefined,
          "PATCH",
        ),
      );
      mutation.current++;
      if (incoming) apply(incoming);
      else await refresh(id);
    } catch (e) {
      setError((e as Error).message);
      throw e;
    } finally {
      sending.current = false;
      setSubmitting(false);
    }
  };
  const sendTurn = async (
    message: string,
    duration?: number,
    planned?: StoryPlan,
    storyId = selected.current,
  ) => {
    if (!storyId || !message.trim())
      throw new Error("Write your next move first.");
    if (
      current.current?.id === storyId &&
      current.current.turns.some(storyTurnPending)
    )
      throw new Error(
        "Finish or cancel the current turn before making another move.",
      );
    return perform(storyId, `/stories/${encodeURIComponent(storyId)}/turns`, {
      message: message.trim(),
      ...(duration ? { duration } : {}),
      ...(planned ? { planned } : {}),
    });
  };
  const turnAction = (
    turn: StoryTurn,
    action: "approve" | "retry" | "cancel" | "reroll",
    plan?: StoryPlan,
  ) =>
    perform(
      selected.current,
      `/stories/${encodeURIComponent(selected.current)}/turns/${encodeURIComponent(turn.id)}/${action}`,
      plan ? { plan } : {},
    );
  const branch = (runId: string) =>
    perform(
      selected.current,
      `/stories/${encodeURIComponent(selected.current)}/branch`,
      { run_id: runId },
    );
  return {
    stories,
    story,
    selectedId,
    loading,
    submitting,
    error,
    pendingTicket,
    pendingCreation,
    generators,
    defaultGenerator,
    selectStory,
    refresh,
    create,
    resumeCreation,
    patch,
    sendTurn,
    turnAction,
    branch,
    resumePending: () =>
      ticket.current ? runTicket(ticket.current) : refresh(),
    clearError: () => setError(""),
  };
}
