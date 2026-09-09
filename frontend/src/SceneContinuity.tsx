import type { Project, SceneContract, Shot } from "./model";
import {
  addSceneActor,
  addSceneObject,
  clearSceneContract,
  editSceneContract,
} from "./sceneContinuityState";
import "./SceneContinuity.css";

type Props = {
  project: Project;
  shot: Shot;
  index: number;
  update: (fn: (draft: Project) => void) => void;
};

/** Opening this panel is local; only an explicit edit takes ownership of the AI draft. */
export default function SceneContinuity({
  project,
  shot,
  index,
  update,
}: Props) {
  const prefix = `Scene ${index + 1}`;
  const contract = shot.scene_contract;
  const actors = contract?.actors || [];
  const objects = contract?.objects || [];
  const availablePeople = project.subjects.filter(
    (person) =>
      shot.visible_subject_ids.includes(person.id) &&
      !shot.offscreen_subject_ids.includes(person.id) &&
      !actors.some((actor) => actor.subject_id === person.id),
  );
  const availableObjects = project.assets.filter(
    (asset) =>
      asset.enabled &&
      asset.semantic_role === "object" &&
      !objects.some((object) => object.entity_id === asset.id),
  );
  const edit = (fn: (draft: SceneContract) => void) =>
    update((p) => editSceneContract(p, shot.id, fn));
  const field = (
    label: string,
    value: string | undefined,
    onChange: (value: string) => void,
    placeholder: string,
    maxLength = 500,
  ) => (
    <label className="scene-continuity-field">
      <span>{label}</span>
      <textarea
        aria-label={`${prefix} ${label}`}
        value={value || ""}
        rows={2}
        maxLength={maxLength}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );

  return (
    <details className="scene-continuity">
      <summary>
        Scene continuity{" "}
        <span>
          {contract
            ? shot.scene_contract_source === "generated"
              ? "AI draft"
              : "Your direction"
            : "people, objects & surroundings"}
        </span>
      </summary>
      <div className="scene-continuity-content">
        <p className="scene-continuity-help">
          Say where everyone starts, who acts and what stays still. Keep each
          object’s appearance and count consistent.
          {shot.scene_contract_source === "generated"
            ? " Editing any field keeps these directions for the next AI rewrite."
            : contract
              ? " AI keeps these directions when rewriting this scene."
              : " These optional directions guide the scene and its ending."}
        </p>
        <section
          className="scene-continuity-section"
          aria-label={`${prefix} actor continuity`}
        >
          <h4>People in view</h4>
          {actors.map((actor) => {
            const name =
              project.subjects.find((person) => person.id === actor.subject_id)
                ?.name || "Unavailable character";
            return (
              <fieldset
                className="scene-continuity-card"
                key={actor.subject_id}
              >
                <legend>{name}</legend>
                <div className="scene-continuity-card-heading">
                  <label className="scene-continuity-field">
                    <span>Role in this action</span>
                    <select
                      aria-label={`${prefix} ${name} activity`}
                      value={actor.activity}
                      onChange={(e) =>
                        edit((d) => {
                          const row = d.actors?.find(
                            (item) => item.subject_id === actor.subject_id,
                          );
                          if (row)
                            row.activity = e.target.value as "act" | "hold";
                        })
                      }
                    >
                      <option value="act">Performs the action</option>
                      <option value="hold">Stay in place</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    aria-label={`${prefix} remove ${name} continuity`}
                    onClick={() =>
                      edit((d) => {
                        d.actors = d.actors?.filter(
                          (item) => item.subject_id !== actor.subject_id,
                        );
                      })
                    }
                  >
                    Remove direction
                  </button>
                </div>
                <div className="scene-continuity-grid">
                  {field(
                    `${name} starts`,
                    actor.start,
                    (value) =>
                      edit((d) => {
                        const row = d.actors?.find(
                          (item) => item.subject_id === actor.subject_id,
                        );
                        if (row) row.start = value;
                      }),
                    "e.g. seated at the left of the table, hands on knees",
                  )}
                  {field(
                    `${name} ${actor.activity === "hold" ? "allowed small movement" : "action"}`,
                    actor.action,
                    (value) =>
                      edit((d) => {
                        const row = d.actors?.find(
                          (item) => item.subject_id === actor.subject_id,
                        );
                        if (row) row.action = value;
                      }),
                    actor.activity === "hold"
                      ? "e.g. watches quietly; hands and feet remain still"
                      : "e.g. lifts the single red cup with her right hand",
                  )}
                  {field(
                    `${name} ends`,
                    actor.end,
                    (value) =>
                      edit((d) => {
                        const row = d.actors?.find(
                          (item) => item.subject_id === actor.subject_id,
                        );
                        if (row) row.end = value;
                      }),
                    "e.g. still seated, holding the cup near her chest",
                  )}
                </div>
              </fieldset>
            );
          })}
          <label className="scene-continuity-field">
            <span>Add a person’s direction</span>
            <select
              aria-label={`${prefix} add actor continuity`}
              value=""
              disabled={!availablePeople.length || actors.length >= 32}
              onChange={(e) => {
                if (e.target.value)
                  update((p) => addSceneActor(p, shot.id, e.target.value));
              }}
            >
              <option value="">Choose a visible person…</option>
              {availablePeople.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name || "Unnamed person"}
                </option>
              ))}
            </select>
          </label>
          {!actors.length && !availablePeople.length && (
            <p className="scene-continuity-help">
              Make a named person visible in this scene to describe their
              movement. You can still direct objects and surroundings below.
            </p>
          )}
        </section>

        <section
          className="scene-continuity-section"
          aria-label={`${prefix} object continuity`}
        >
          <h4>Objects to keep consistent</h4>
          {objects.map((object, objectIndex) => {
            const label = `Object ${objectIndex + 1}`;
            return (
              <fieldset
                className="scene-continuity-card"
                key={object.entity_id}
              >
                <legend>{object.name || label}</legend>
                <div className="scene-continuity-grid">
                  <label className="scene-continuity-field">
                    <span>Name</span>
                    <input
                      aria-label={`${prefix} ${label} name`}
                      maxLength={120}
                      value={object.name}
                      onChange={(e) => {
                        const value = e.target.value || label;
                        edit((d) => {
                          const row = d.objects?.find(
                            (item) => item.entity_id === object.entity_id,
                          );
                          if (row) row.name = value;
                        });
                      }}
                    />
                  </label>
                  <label className="scene-continuity-field">
                    <span>Exact count in view</span>
                    <input
                      aria-label={`${prefix} ${label} count`}
                      type="number"
                      min={1}
                      max={100}
                      step={1}
                      value={object.count}
                      onChange={(e) => {
                        const value = Number(e.target.value);
                        if (
                          e.target.value &&
                          Number.isInteger(value) &&
                          value >= 1 &&
                          value <= 100
                        )
                          edit((d) => {
                            const row = d.objects?.find(
                              (item) => item.entity_id === object.entity_id,
                            );
                            if (row) row.count = value;
                          });
                      }}
                    />
                  </label>
                  {field(
                    `${label} appearance`,
                    object.description,
                    (value) =>
                      edit((d) => {
                        const row = d.objects?.find(
                          (item) => item.entity_id === object.entity_id,
                        );
                        if (row) row.description = value;
                      }),
                    "e.g. small red ceramic cup with one white stripe",
                  )}
                  {field(
                    `${label} starts`,
                    object.start,
                    (value) =>
                      edit((d) => {
                        const row = d.objects?.find(
                          (item) => item.entity_id === object.entity_id,
                        );
                        if (row) row.start = value;
                      }),
                    "e.g. on the table in front of Mira",
                  )}
                  {field(
                    `${label} ends`,
                    object.end,
                    (value) =>
                      edit((d) => {
                        const row = d.objects?.find(
                          (item) => item.entity_id === object.entity_id,
                        );
                        if (row) row.end = value;
                      }),
                    "e.g. held in Mira’s right hand; no second cup appears",
                  )}
                </div>
                <button
                  type="button"
                  aria-label={`${prefix} remove ${label} continuity`}
                  onClick={() =>
                    edit((d) => {
                      d.objects = d.objects?.filter(
                        (item) => item.entity_id !== object.entity_id,
                      );
                    })
                  }
                >
                  Remove object direction
                </button>
              </fieldset>
            );
          })}
          <div className="scene-continuity-card-heading">
            <button
              type="button"
              aria-label={`${prefix} add object continuity`}
              disabled={objects.length >= 24}
              onClick={() => update((p) => addSceneObject(p, shot.id))}
            >
              Add an object
            </button>
            {!!availableObjects.length && (
              <label className="scene-continuity-field">
                <span>Or use an object reference</span>
                <select
                  aria-label={`${prefix} add referenced object continuity`}
                  value=""
                  disabled={objects.length >= 24}
                  onChange={(e) => {
                    if (e.target.value)
                      update((p) => addSceneObject(p, shot.id, e.target.value));
                  }}
                >
                  <option value="">Choose a reference…</option>
                  {availableObjects.map((asset) => (
                    <option key={asset.id} value={asset.id}>
                      {asset.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        </section>
        <div className="scene-continuity-grid">
          {field(
            "Environment",
            contract?.environment,
            (value) =>
              edit((d) => {
                d.environment = value;
              }),
            "e.g. the same café, warm light, table and doorway fixed in place",
            1000,
          )}
          {field(
            "Background activity",
            contract?.background_activity,
            (value) =>
              edit((d) => {
                d.background_activity = value;
              }),
            "e.g. distant pedestrians keep walking; no one joins the foreground action",
          )}
        </div>
        {!!contract && (
          <button
            className="scene-continuity-reset"
            type="button"
            aria-label={`${prefix} clear scene continuity`}
            onClick={() =>
              update((p) => {
                const target = p.shots.find((item) => item.id === shot.id);
                if (target) clearSceneContract(target);
              })
            }
          >
            Clear continuity directions · let AI choose
          </button>
        )}
      </div>
    </details>
  );
}
