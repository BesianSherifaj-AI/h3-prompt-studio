import { uid } from "./model";
import type { Project, SceneContract, Shot } from "./model";

/** Merely viewing or normalizing an AI draft must not turn it into a user constraint. */
export function editSceneContract(
  project: Project,
  shotId: string,
  edit: (contract: SceneContract) => void,
): void {
  const shot = project.shots.find((item) => item.id === shotId);
  if (!shot) return;
  const contract = structuredClone(shot.scene_contract || {});
  edit(contract);
  if (JSON.stringify(contract) === JSON.stringify(shot.scene_contract || {}))
    return;
  shot.scene_contract = contract;
  delete shot.scene_contract_source;
  shot.director_locks = [
    ...new Set([...(shot.director_locks || []), "scene_contract"]),
  ];
}

export function clearSceneContract(shot: Shot): void {
  delete shot.scene_contract;
  delete shot.scene_contract_source;
  if (shot.director_locks)
    shot.director_locks = shot.director_locks.filter(
      (path) => path !== "scene_contract",
    );
}

export function addSceneActor(
  project: Project,
  shotId: string,
  subjectId: string,
): void {
  const shot = project.shots.find((item) => item.id === shotId);
  if (
    !shot ||
    !project.subjects.some((person) => person.id === subjectId) ||
    !shot.visible_subject_ids.includes(subjectId) ||
    shot.offscreen_subject_ids.includes(subjectId) ||
    (shot.scene_contract?.actors?.length || 0) >= 32 ||
    shot.scene_contract?.actors?.some((actor) => actor.subject_id === subjectId)
  )
    return;
  editSceneContract(project, shotId, (contract) => {
    (contract.actors ??= []).push({
      subject_id: subjectId,
      activity: "act",
      start: "",
      action: "",
      end: "",
    });
  });
}

export function addSceneObject(
  project: Project,
  shotId: string,
  assetId?: string,
): void {
  const shot = project.shots.find((item) => item.id === shotId);
  const asset = assetId
    ? project.assets.find(
        (item) =>
          item.id === assetId &&
          item.enabled &&
          item.semantic_role === "object",
      )
    : undefined;
  if (
    !shot ||
    (assetId && !asset) ||
    (shot.scene_contract?.objects?.length || 0) >= 24 ||
    (assetId &&
      shot.scene_contract?.objects?.some(
        (object) => object.entity_id === assetId,
      ))
  )
    return;
  editSceneContract(project, shotId, (contract) => {
    const objects = (contract.objects ??= []);
    objects.push({
      entity_id: asset?.id || uid(),
      name: (asset?.name || `Object ${objects.length + 1}`).slice(0, 120),
      description: (asset?.description || "").slice(0, 500),
      count: 1,
      start: "",
      end: "",
    });
  });
}

/** An explicit roster edit cannot leave physical direction for someone now off screen. */
export function pruneSceneActors(project: Project, shotId: string): void {
  const shot = project.shots.find((item) => item.id === shotId);
  if (!shot?.scene_contract?.actors) return;
  const known = new Set(project.subjects.map((person) => person.id));
  editSceneContract(project, shotId, (contract) => {
    contract.actors = contract.actors?.filter(
      (actor) =>
        known.has(actor.subject_id) &&
        shot.visible_subject_ids.includes(actor.subject_id) &&
        !shot.offscreen_subject_ids.includes(actor.subject_id),
    );
  });
}

/** The earlier half of a split no longer owns the old final landing. */
export function clearSceneEnding(project: Project, shotId: string): void {
  editSceneContract(project, shotId, (contract) => {
    for (const actor of contract.actors || []) actor.end = "";
    for (const object of contract.objects || []) object.end = "";
  });
}
