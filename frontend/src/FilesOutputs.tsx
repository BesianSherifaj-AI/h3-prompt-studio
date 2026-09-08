import { useEffect, useState } from "react";
import { FolderOpen, LoaderCircle } from "lucide-react";
import { api } from "./api";

type Location = {
  id: string;
  title: string;
  path: string;
  description: string;
  available: boolean;
};

export default function FilesOutputs() {
  const [locations, setLocations] = useState<Location[] | null>(null);
  const [opening, setOpening] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api("/files")
      .then((data) => active && setLocations(data.locations))
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, []);
  async function openFolder(location: Location) {
    setOpening(location.id);
    setError("");
    setMessage("");
    try {
      await api("/files/open", { id: location.id });
      setMessage(`${location.title} opened in Windows File Explorer.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setOpening("");
    }
  }
  return (
    <div className="files-outputs">
      <p className="files-intro">
        Your scene plan, image descriptions and dialogue save inside your
        project. The finished text appears in <strong>H3 prompt</strong> below
        the scene editor. Use <strong>Copy prompt</strong> or{" "}
        <strong>Save prompt</strong> to take it to ComfyUI. Video files appear
        only after a ComfyUI render.
      </p>
      {error && (
        <p className="files-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="files-status" role="status">
          {message}
        </p>
      )}
      {!locations && !error && <p role="status">Finding your folders…</p>}
      {locations?.map((location) => (
        <section className="files-location" key={location.id}>
          <div>
            <h3>{location.title}</h3>
            <p>{location.description}</p>
            <code>{location.path}</code>
          </div>
          <button
            disabled={!location.available || !!opening}
            aria-label={`Open ${location.title.toLowerCase()} folder`}
            onClick={() => openFolder(location)}
          >
            {opening === location.id ? (
              <LoaderCircle size={14} />
            ) : (
              <FolderOpen size={14} />
            )}
            {location.available ? "Open folder" : "Not on this computer"}
          </button>
        </section>
      ))}
      <p className="help">
        Browser downloads may open a save dialog or go to Downloads, depending
        on your browser settings.
      </p>
    </div>
  );
}
