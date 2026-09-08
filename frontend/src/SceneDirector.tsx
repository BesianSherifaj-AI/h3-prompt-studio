import { useState } from "react";
import type { Project, Shot } from "./model";
import { getPeople } from "./simple";
import {
  appendSceneAction,
  applyCameraSetup,
  CAMERA_MOVES,
  CAMERA_SETUPS,
  duplicateScene,
  moveScene,
  setDirectorValue,
  setPersonVisibility,
  setSceneDuration,
  SHOT_SIZES,
} from "./shotDirections";
import type { DirectedShot, DirectorPath } from "./shotDirections";
import "./SceneDirector.css";

type Props = {
  project: Project;
  shot: Shot;
  index: number;
  update: (fn: (draft: Project) => void) => void;
};

export function SceneDirector({ project, shot, index, update }: Props) {
  const n = index + 1;
  const [actor, setActor] = useState("");
  const [verb, setVerb] = useState("holds");
  const [objectId, setObjectId] = useState("");
  const [customAction, setCustomAction] = useState("");
  const people = getPeople(project);
  const objects = project.assets.filter(
    (a) => a.enabled && a.semantic_role === "object",
  );
  const locks = (shot as DirectedShot).director_locks || [];
  const set = (path: DirectorPath, value: string) =>
    update((d) => setDirectorValue(d, shot.id, path, value));
  const choice = (
    path: DirectorPath,
    label: string,
    value: string,
    options: readonly (readonly [string, string])[],
  ) => (
    <label className="scene-director-field">
      <span>
        {label}
        {locks.includes(path) && (
          <small title="AI keeps this choice"> · chosen</small>
        )}
      </span>
      <select
        aria-label={`Scene ${n} ${label.toLowerCase()}`}
        value={value || ""}
        onChange={(e) => set(path, e.target.value)}
      >
        <option value="">AI chooses</option>
        {value && !options.some(([key]) => key === value) && (
          <option value={value}>{value}</option>
        )}
        {options.map(([key, title]) => (
          <option key={key} value={key}>
            {title}
          </option>
        ))}
      </select>
    </label>
  );
  const textField = (
    path: DirectorPath,
    label: string,
    value: string,
    placeholder: string,
  ) => (
    <label className="scene-director-field">
      <span>{label}</span>
      <input
        aria-label={`Scene ${n} ${label.toLowerCase()}`}
        value={value || ""}
        placeholder={placeholder}
        onChange={(e) => set(path, e.target.value)}
      />
    </label>
  );

  return (
    <div className="scene-director">
      <div className="scene-director-main">
        {choice(
          "camera.framing",
          "Shot size",
          shot.camera?.framing,
          SHOT_SIZES,
        )}
        {choice(
          "camera.movement",
          "Camera movement",
          shot.camera?.movement,
          CAMERA_MOVES,
        )}
        {index > 0 &&
          choice("transition", "Scene change", shot.transition, [
            ["continuous", "Keep filming · no cut"],
            ["cut", "Cut to a new shot"],
          ])}
      </div>
      <p className="scene-director-hint">
        Choose what matters to you. AI keeps your choices and fills in the rest.
      </p>
      <details className="scene-director-more">
        <summary>
          More scene options <span>timing, people, place & camera</span>
        </summary>
        <div className="scene-director-options">
          <div className="scene-director-timing">
            <label className="scene-director-field">
              <span>Scene length (seconds)</span>
              <input
                aria-label={`Scene ${n} duration`}
                type="number"
                min={0.25}
                max={project.duration}
                step={0.25}
                value={shot.duration}
                disabled={project.shots.length === 1}
                onChange={(e) => {
                  if (e.target.value)
                    update((d) =>
                      setSceneDuration(d, shot.id, Number(e.target.value)),
                    );
                }}
              />
            </label>
            <p>
              {project.shots.length === 1
                ? "Change the video length above for a single scene."
                : `Other scenes adjust to keep the whole video at ${project.duration} seconds.`}
            </p>
          </div>
          <div className="scene-director-buttons">
            <button
              type="button"
              aria-label={`Move scene ${n} earlier`}
              disabled={index === 0}
              onClick={() => update((d) => moveScene(d, shot.id, -1))}
            >
              Move earlier
            </button>
            <button
              type="button"
              aria-label={`Move scene ${n} later`}
              disabled={index === project.shots.length - 1}
              onClick={() => update((d) => moveScene(d, shot.id, 1))}
            >
              Move later
            </button>
            <button
              type="button"
              aria-label={`Duplicate scene ${n}`}
              disabled={project.shots.length >= 6}
              title="Copy this scene's setup without repeating its dialogue"
              onClick={() => update((d) => duplicateScene(d, shot.id))}
            >
              Duplicate setup
            </button>
          </div>
          <p className="scene-director-hint">
            Duplicate copies the setup and splits its time. Spoken lines stay in
            the original scene.
          </p>
          <fieldset className="scene-director-setups">
            <legend>Try a camera setup</legend>
            <div className="scene-director-buttons">
              {CAMERA_SETUPS.map((setup) => (
                <button
                  type="button"
                  aria-label={`Scene ${n}: ${setup.label}`}
                  key={setup.id}
                  onClick={() =>
                    update((d) => applyCameraSetup(d, shot.id, setup.id))
                  }
                >
                  {setup.label}
                </button>
              ))}
            </div>
          </fieldset>
          <div className="scene-director-grid">
            {choice("camera.height", "Camera angle", shot.camera?.height, [
              ["eye level", "Eye level"],
              ["low angle", "Looking up"],
              ["high angle", "Looking down"],
              ["overhead", "From directly above"],
            ])}
            {choice("camera.focus", "Focus", shot.camera?.focus, [
              ["keep the speaking face in focus", "The person speaking"],
              [
                "sharp subject, softly blurred background",
                "Subject sharp · background soft",
              ],
              ["deep focus", "Everything in focus"],
            ])}
            {textField(
              "setting",
              "Place",
              shot.setting,
              "e.g. the café from the background photo",
            )}
            {textField(
              "final_state",
              "How this scene ends",
              shot.final_state,
              "e.g. Nora is holding the gift",
            )}
          </div>
          {!!people.length && (
            <fieldset className="scene-director-people">
              <legend>Who is in this scene?</legend>
              <p className="scene-director-hint">
                Off screen means their voice can be heard without showing them.
              </p>
              {people.map((person) => (
                <label className="scene-director-person" key={person.id}>
                  <span>{person.name || "Unnamed person"}</span>
                  <select
                    aria-label={`Scene ${n} ${person.name} visibility`}
                    value={
                      shot.visible_subject_ids.includes(person.id)
                        ? "visible"
                        : shot.offscreen_subject_ids.includes(person.id)
                          ? "offscreen"
                          : "absent"
                    }
                    onChange={(e) =>
                      update((d) =>
                        setPersonVisibility(
                          d,
                          shot.id,
                          person.id,
                          e.target.value as "visible" | "offscreen" | "absent",
                        ),
                      )
                    }
                  >
                    <option value="visible">Visible in the shot</option>
                    <option value="offscreen">Speaking off screen</option>
                    <option value="absent">Not in this scene</option>
                  </select>
                </label>
              ))}
              {(locks.includes("visible_subject_ids") ||
                locks.includes("offscreen_subject_ids")) && (
                <button
                  className="scene-director-reset"
                  type="button"
                  aria-label={`Scene ${n} let AI choose people`}
                  onClick={() =>
                    update((d) => {
                      setDirectorValue(
                        d,
                        shot.id,
                        "visible_subject_ids",
                        [],
                        false,
                      );
                      setDirectorValue(
                        d,
                        shot.id,
                        "offscreen_subject_ids",
                        [],
                        false,
                      );
                    })
                  }
                >
                  Let AI choose who is visible
                </button>
              )}
            </fieldset>
          )}
          {!!people.length && (
            <details className="scene-director-builder">
              <summary>Help me write an action</summary>
              <div className="scene-director-grid">
                <label className="scene-director-field">
                  <span>Who?</span>
                  <select
                    aria-label={`Scene ${n} action person`}
                    value={actor}
                    onChange={(e) => setActor(e.target.value)}
                  >
                    <option value="">Choose a person…</option>
                    {people.map((person) => (
                      <option key={person.id} value={person.id}>
                        {person.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="scene-director-field">
                  <span>Does what?</span>
                  <select
                    aria-label={`Scene ${n} action type`}
                    value={verb}
                    onChange={(e) => setVerb(e.target.value)}
                  >
                    {[
                      "holds",
                      "picks up",
                      "looks at",
                      "points to",
                      "walks toward",
                      "custom",
                    ].map((v) => (
                      <option key={v} value={v}>
                        {v === "custom" ? "Write my own…" : v}
                      </option>
                    ))}
                  </select>
                </label>
                {verb === "custom" ? (
                  <label className="scene-director-field">
                    <span>Action</span>
                    <input
                      aria-label={`Scene ${n} custom action`}
                      value={customAction}
                      placeholder="e.g. waves, then opens the door"
                      onChange={(e) => setCustomAction(e.target.value)}
                    />
                  </label>
                ) : (
                  <label className="scene-director-field">
                    <span>Which object?</span>
                    <select
                      aria-label={`Scene ${n} action object`}
                      value={objectId}
                      onChange={(e) => setObjectId(e.target.value)}
                    >
                      <option value="">Choose an object photo…</option>
                      {objects.map((object) => (
                        <option key={object.id} value={object.id}>
                          {object.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
              <button
                type="button"
                aria-label={`Add action to scene ${n}`}
                disabled={
                  !actor ||
                  (verb === "custom" ? !customAction.trim() : !objectId)
                }
                onClick={() => {
                  update((d) =>
                    appendSceneAction(
                      d,
                      shot.id,
                      actor,
                      verb === "custom" ? customAction : verb,
                      verb === "custom" ? "" : objectId,
                    ),
                  );
                  setCustomAction("");
                }}
              >
                Add to “What happens”
              </button>
            </details>
          )}
        </div>
      </details>
    </div>
  );
}

export default SceneDirector;
