import { useCallback, useEffect, useRef, useState } from "react";
import { api as request, ApiError } from "./api";
import type { Project } from "./model";
import {
  imageGeneratorInventory,
  storyTurnPending,
  validStoryTicket,
  type ImageGeneratorModel,
  type Story,
  type StoryPlan,
  type StorySettings,
  type StoryTicket,
  type StoryTurn,
  type GameIntent,
  type StoryConfiguration,
} from "./storyTypes";

const SELECTED = "h3-game:selected-story";
const CREATE = "h3-game:pending-create";
// These endpoints acknowledge saved work; inference and rendering run on the server.
// A missing acknowledgement must unlock recovery without repeating a mutation.
const api = (path: string, body?: any, form?: FormData, method?: string) =>
  request(path, body, form, method, { timeoutMs: body === undefined && !form ? 15_000 : 30_000 });
const notSubmitted = (message: string) => Object.assign(new Error(message), { notSubmitted: true });
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
    world?: StoryConfiguration["world"];
    guides?: StoryConfiguration["guides"];
    player_character_id?: string;
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
  const [generatorsLoading, setGeneratorsLoading] = useState(true),
    [generatorsChecked, setGeneratorsChecked] = useState(false),
    [generatorErrors, setGeneratorErrors] = useState<string[]>([]);
  const generatorRevision = useRef(0);
  const selected = useRef(""),
    current = useRef<Story | null>(null),
    mutation = useRef(0),
    pollRevision = useRef(0);
  const sending = useRef(false),
    mounted = useRef(true),
    ticket = useRef<StoryTicket | null>(null);
  const createAttempt = useRef<PendingStoryCreation | null>(pendingCreation);
  const refreshGenerators = useCallback(async () => {
    const revision = ++generatorRevision.current;
    setGeneratorsLoading(true);
    try {
      const result = await api("/assets/generators");
      if (!mounted.current || revision !== generatorRevision.current) return;
      setGenerators(imageGeneratorInventory(result));
      setDefaultGenerator(typeof result?.default_model === "string" ? result.default_model : "");
      setGeneratorErrors(Array.isArray(result?.errors) ? result.errors.filter((item: unknown): item is string => typeof item === "string" && !!item.trim()) : []);
    } catch (error) {
      if (!mounted.current || revision !== generatorRevision.current) return;
      setGeneratorErrors([`Image generator inventory could not be checked. ${(error as Error).message || "Check the ComfyUI connection and refresh."}`]);
    } finally {
      if (mounted.current && revision === generatorRevision.current) {
        setGeneratorsLoading(false);
        setGeneratorsChecked(true);
      }
    }
  }, []);
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
      const storiesRequest = api("/stories");
      // Opening a saved game does not need image-generator discovery to finish.
      void refreshGenerators();
      const results = await Promise.allSettled([storiesRequest]);
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
          if (!skipRestore) {
            const linked = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("game") || "" : "";
            saved = /^[0-9a-f-]{36}$/i.test(linked) ? linked : localStorage.getItem(SELECTED) || "";
          }
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
      if (alive) setLoading(false);
    };
    void init();
    return () => {
      alive = false;
      mounted.current = false;
      pollRevision.current++;
      generatorRevision.current++;
    };
  }, [acceptCreation, selectStory, skipRestore, refreshGenerators]);
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
      throw notSubmitted("The previous action is still being sent.");
    if (ticket.current && ticket.current.requestId !== next.requestId)
      throw notSubmitted(
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
      const full = asStory(result);
      const expectedTurn = next.path === `/stories/${encodeURIComponent(next.storyId)}/turns`
        ? next.requestId : next.path.match(/\/turns\/([^/]+)\//)?.[1];
      const acceptedTurn = expectedTurn && result?.id === expectedTurn &&
        typeof result?.status === "string" && typeof result?.request_id === "string";
      if (!(full?.id === next.storyId || acceptedTurn))
        throw new Error("The saved request was not confirmed. Reconnect to check its status before sending another move.");
      mutation.current++;
      if (ticket.current?.requestId === next.requestId) {
        ticket.current = null;
        storeTicket(null, next.storyId);
        if (mounted.current) setPendingTicket(null);
      }
      if (full) apply(full);
      else if (selected.current === next.storyId && acceptedTurn) {
        const turn = result as StoryTurn;
        const old = current.current;
        if (old)
          apply({
            ...old,
            turns: old.turns.some(t => t.id === turn.id)
              ? old.turns.map(t => t.id === turn.id ? turn : t)
              : [...old.turns, turn],
          });
      }
      void refresh(next.storyId).catch(() => {
        /* The accepted turn will be polled; never repeat it. */
      });
      return result as StoryTurn | Story;
    } catch (e) {
      mutation.current++;
      const rejected = e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408;
      if (rejected) {
        Object.assign(e, { notSubmitted: true });
        if (ticket.current?.requestId === next.requestId) {
          ticket.current = null;
          if (mounted.current) setPendingTicket(null);
        }
        storeTicket(null, next.storyId);
      }
      const recovered = await refresh(next.storyId).catch(() => null);
      const accepted = !rejected && next.path === `/stories/${encodeURIComponent(next.storyId)}/turns`
        ? recovered?.turns.find(turn => turn.request_id === next.requestId) : undefined;
      if (accepted) {
        storeTicket(null, next.storyId);
        if (ticket.current?.requestId === next.requestId) {
          ticket.current = null;
          if (mounted.current) setPendingTicket(null);
        }
        return accepted;
      }
      if (mounted.current && selected.current === next.storyId)
        setError((e as Error).message);
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
    stableRequestId?: string,
  ) => {
    const requestId = stableRequestId || crypto.randomUUID();
    return runTicket({
      storyId: id,
      path,
      body: structuredClone({ ...body, request_id: requestId }),
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
      if (posted && e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408)
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
      world?: StoryConfiguration["world"];
      guides?: StoryConfiguration["guides"];
      player_character_id?: string;
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
    changes: Partial<StoryConfiguration> & { expected_configuration_revision?: number },
  ) => {
    const id = selected.current;
    if (!id || sending.current) throw new Error("Wait for the previous request before saving changes.");
    sending.current = true;
    setSubmitting(true);
    mutation.current++;
    try {
      const incoming = asStory(
        await api(
          `/stories/${encodeURIComponent(id)}`,
          { ...changes, expected_configuration_revision: changes.expected_configuration_revision ?? current.current?.configuration_revision },
          undefined,
          "PATCH",
        ),
      );
      mutation.current++;
      if (incoming) { apply(incoming); return incoming; }
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
    intent?: GameIntent,
    stableRequestId?: string,
  ) => {
    if (!storyId || !message.trim())
      throw notSubmitted("Write your next move first.");
    if (
      current.current?.id === storyId &&
      current.current.turns.some(storyTurnPending)
    )
      throw notSubmitted(
        "Finish or cancel the current turn before making another move.",
      );
    return perform(storyId, `/stories/${encodeURIComponent(storyId)}/turns`, {
      message: message.trim(),
      ...(duration ? { duration } : {}),
      ...(planned ? { planned } : {}),
      ...(intent ? { intent } : {}),
      ...(current.current?.id === storyId ? { expected_parent: current.current.active_run_id || null, configuration_revision: current.current.configuration_revision } : {}),
    }, stableRequestId);
  };
  const turnAction = (
    turn: StoryTurn,
    action: "approve" | "retry" | "cancel" | "reroll" | "edit" | "resume" | "retry-inspection" | "accept-intended" | "accept-visible" | "stop-and-apply",
    plan?: StoryPlan,
    extra?: Record<string, unknown>,
  ) =>
    perform(
      selected.current,
      `/stories/${encodeURIComponent(selected.current)}/turns/${encodeURIComponent(turn.id)}/${action}`,
      { ...(plan ? { plan } : {}), ...extra },
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
    generatorsLoading,
    generatorsChecked,
    generatorErrors,
    refreshGenerators,
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
