import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Project } from "./model";
import VideoWorkspace, { continuationIdeaChoices, continuationSizeLabel, continuationSuggestionIsCurrent, elapsedVideoTime, videoComparisonChoices, videoJobIsPending, videoTakeTitle, videoWorkspaceState, type VideoJob } from "./VideoWorkspace";

const run = (change: Partial<VideoJob> = {}): VideoJob => ({ id: "take-1", project_id: "project-a", status: "succeeded", seed: 42, duration: 5, video_url: "/api/video/runs/take-1/video", continuation_source: "mmh3/take-1.mmh3", has_snapshot: true, ...change });

describe("video workspace action eligibility", () => {
  it("keeps successful takes available for new seeds and actual continuation", () => {
    const job = run(), state = videoWorkspaceState("project-a", [job], job);
    expect(state.playable).toBe(true); expect(state.canReroll).toBe(true); expect(state.canContinue).toBe(true); expect(state.working).toBe(false);
  });
  it("never presents another project's result, even if passed as selected", () => {
    const other = run({ project_id: "other", status: "running" });
    const state = videoWorkspaceState("project-a", [other, run()], other);
    expect(state.takes).toHaveLength(1); expect(state.current).toBeNull(); expect(state.pending).toBeNull(); expect(state.playable).toBeFalsy(); expect(state.canContinue).toBe(false);
  });
  it("uses the refreshed job record and disables actions during pending or uncertain requests", () => {
    const original = run();
    for (const status of ["preparing", "queued", "running", "uncertain"] as const) {
      const refreshed = run({ status }), state = videoWorkspaceState("project-a", [refreshed], original);
      expect(state.current?.status).toBe(status); expect(state.working).toBe(true); expect(state.canContinue).toBe(false); expect(state.canReroll).toBe(false);
    }
    expect(videoWorkspaceState("project-a", [original, run({ id: "new", status: "running" })], original).canReroll).toBe(false);
    expect(videoWorkspaceState("project-a", [original], original, "Making prompt").canContinue).toBe(false);
  });
  it("requires saved motion for Continue and a stored snapshot for either result-based action", () => {
    const noMotion = run({ continuation_source: null });
    expect(videoWorkspaceState("project-a", [noMotion], noMotion).canReroll).toBe(true);
    expect(videoWorkspaceState("project-a", [noMotion], noMotion).canContinue).toBe(false);
    for (const job of [run({ has_snapshot: false }), run({ status: "failed" }), run({ video_url: null })]) {
      const state = videoWorkspaceState("project-a", [job], job);
      expect(state.canReroll).toBe(false); expect(state.canContinue).toBe(false);
    }
  });
  it("deduplicates current project takes without mutating the input", () => {
    const job = run(), list = [job, { ...job }, run({ id: "take-2" })];
    expect(videoWorkspaceState("project-a", list, job).takes.map(item => item.id)).toEqual(["take-1", "take-2"]);
    expect(list).toHaveLength(3);
  });
  it("combines successful saved continuation chains and plays derived films without offering to continue their merged latents", () => {
    const child = run({ parent_run_id: "previous", operation: "continue" });
    expect(videoWorkspaceState("project-a", [child], child).canCombine).toBe(true);
    expect(videoWorkspaceState("project-a", [run()], run()).canCombine).toBe(false);
    const combined = run({ operation: "combine", parent_run_id: "previous" }), state = videoWorkspaceState("project-a", [combined], combined);
    expect(state.playable).toBe(true); expect(state.canContinue).toBe(false); expect(state.canReroll).toBe(false); expect(state.canCombine).toBe(false);
  });
  it("does not offer joining for an original reroll, but preserves joining a rerolled continuation", () => {
    const originalReroll = run({ operation: "reroll", parent_run_id: "original", can_combine: false });
    for (const can_combine of [false, undefined]) {
      const job = { ...originalReroll, can_combine }, state = videoWorkspaceState("project-a", [job], job);
      expect(state.showCombine).toBe(false); expect(state.canCombine).toBe(false);
    }
    const continuationReroll = run({ operation: "reroll", parent_run_id: "previous-continuation", can_combine: true });
    const ready = videoWorkspaceState("project-a", [continuationReroll], continuationReroll);
    expect(ready.showCombine).toBe(true); expect(ready.canCombine).toBe(true);
    const busy = videoWorkspaceState("project-a", [continuationReroll], continuationReroll, "Making prompt");
    expect(busy.showCombine).toBe(true); expect(busy.canCombine).toBe(false);
  });
  it("renders the join explanation only for a real continuation or joined film", () => {
    const project: Project = { schema_version: 1, id: "project-a", title: "Film", mode: "ref2va", duration: 5,
      aspect_ratio: "16:9", profile: "director", authoring_mode: "manual", story: { text: "A scene", locked: true },
      style: {}, assets: [], subjects: [], shots: [], soundscape: "", music: "", custom_instructions: "" };
    const markup = (job: VideoJob) => renderToStaticMarkup(createElement(VideoWorkspace, {
      project, promptReady: true, busy: false, jobs: [job], currentJob: job,
      onSelectJob: () => {}, onGenerate: () => {}, onReroll: () => {}, onContinue: () => {}, onCombine: () => {},
    }));
    const reroll = run({ operation: "reroll", parent_run_id: "original", can_combine: false });
    expect(markup(reroll)).not.toContain("Combine clips");
    expect(markup(reroll)).not.toContain("Join this continuation");
    expect(markup({ ...reroll, can_combine: true })).toContain("Join this continuation");
    expect(markup(run({ operation: "combine", continuation_source: null, can_combine: false }))).toContain("Your joined film is ready");
  });
  it("shows only measured elapsed time and treats uncertainty as pending", () => {
    expect(elapsedVideoTime(undefined)).toBeNull(); expect(elapsedVideoTime(NaN)).toBeNull(); expect(elapsedVideoTime(-1)).toBeNull();
    expect(elapsedVideoTime(0)).toBe("0s"); expect(elapsedVideoTime(65.8)).toBe("1m 05s"); expect(elapsedVideoTime(600)).toBe("10m 00s");
    expect(videoJobIsPending(run({ status: "uncertain" }))).toBe(true); expect(videoJobIsPending(run())).toBe(false);
  });
  it("respects explicit backend capability restrictions even when a successful run has media and saved state", () => {
    const blocked = run({ parent_run_id: "parent", can_reroll: false, can_continue: false, can_combine: false });
    const state = videoWorkspaceState("project-a", [blocked], blocked);
    expect(state.playable).toBe(true); expect(state.canReroll).toBe(false); expect(state.canContinue).toBe(false); expect(state.canCombine).toBe(false);
  });
  it("continues a joined film only through an explicitly verified final individual take",()=>{
    const joined=run({operation:'combine',continuation_source:null,has_snapshot:false,can_continue:true,continue_from_run_id:'verified-final-take'});
    const state=videoWorkspaceState('project-a',[joined],joined);
    expect(state.canContinue).toBe(true);expect(state.canReroll).toBe(false);expect(state.canCombine).toBe(false);
    for(const change of [{can_continue:false},{can_continue:undefined},{continue_from_run_id:null},{continue_from_run_id:''},{continue_from_run_id:joined.id}]){
      const invalid={...joined,...change};expect(videoWorkspaceState('project-a',[invalid],invalid).canContinue).toBe(false);
    }
  });
});

describe("continuation idea safeguards", () => {
  const context = { open: true, projectId: "project-a", runId: "take-1", duration: 5, direction: "Keep the same room" };
  it("keeps requests valid across ordinary polling but rejects another project, take, length or edited direction", () => {
    expect(continuationSuggestionIsCurrent(context, { ...context })).toBe(true);
    for (const change of [{ projectId: "project-b" }, { runId: "take-2" }, { duration: 7 }, { direction: "She leaves" }, { open: false }]) {
      expect(continuationSuggestionIsCurrent(context, { ...context, ...change })).toBe(false);
    }
    expect(continuationSuggestionIsCurrent({ ...context, open: false }, context)).toBe(false);
  });
  it("offers at most three distinct usable ideas without mutating the response", () => {
    const result = { suggestions: [{ title: " Open it ", idea: " She opens the box. " }, { title: "Again", idea: "She opens the box." },
      { title: "Blank", idea: " " }, { title: "Look out", idea: "She looks out of the window." },
      { title: "Listen", idea: "She hears a knock." }, { title: "Leave", idea: "She leaves." }] };
    expect(continuationIdeaChoices(result)).toEqual([{ title: "Open it", idea: "She opens the box." }, { title: "Look out", idea: "She looks out of the window." }, { title: "Listen", idea: "She hears a knock." }]);
    expect(result.suggestions[0].title).toBe(" Open it ");
  });
  it("handles a malformed model suggestion list as empty instead of breaking the editing panel", () => {
    expect(continuationIdeaChoices({ suggestions: [null, { idea: 3 }, "text"] as never })).toEqual([]);
    expect(continuationIdeaChoices({ suggestions: null } as never)).toEqual([]);
  });
});

describe("take review helpers", () => {
  it("labels only known 0.3 MP sources as 0.3 MP and describes inherited geometry honestly", () => {
    expect(continuationSizeLabel(run({width:736,height:416})).presetSize).toBe('0.3 MP');
    expect(continuationSizeLabel(run({width:1280,height:768}))).toEqual({presetSize:'source size',explanation:'Continuation keeps this video’s 1280 × 768 size. Preview presets change steps.'});
    expect(continuationSizeLabel(run({width:320,height:240})).presetSize).toBe('source size');
    expect(continuationSizeLabel(run()).presetSize).toBe('source size');
    expect(continuationSizeLabel(run({width:0,height:416})).presetSize).toBe('source size');
  });
  it("uses a saved name or a stable take-number fallback", () => {
    expect(videoTakeTitle(run({title:'  Gentle camera  '}),2)).toBe('Gentle camera');
    expect(videoTakeTitle(run({title:' '}),2)).toBe('Take 2');
    expect(videoTakeTitle(run())).toBe('Selected take');
  });
  it("compares only distinct successful individual takes from the same project", () => {
    const selected=run(),other=run({id:'take-2'}),list=[selected,other,{...other},run({id:'bad',status:'failed'}),run({id:'foreign',project_id:'elsewhere'}),run({id:'joined',operation:'combine'}),run({id:'missing',video_url:null})];
    expect(videoComparisonChoices('project-a',list,selected)).toEqual([other]);
    expect(videoComparisonChoices('project-a',list,run({operation:'combine'}))).toEqual([]);
    expect(videoComparisonChoices('project-a',list,run({project_id:'elsewhere'}))).toEqual([]);
    expect(videoComparisonChoices('project-a',list,run({status:'running'}))).toEqual([]);
  });
});
