import { describe, expect, it } from "vitest";
import { modelPickerOptions } from "./ModelPicker";

describe("prompt assistant choices", () => {
  it("keeps all installed text, vision and unknown models with honest capability and loaded labels", () => {
    const options = modelPickerOptions([
      { id: "vision", name: "Small vision", vision: true, loaded: true },
      { id: "text", name: "Writer", vision: false },
      { id: "unknown", name: "Unreported", vision: null },
    ], "vision");
    expect(options.map(option => option.id)).toEqual(["vision", "text", "unknown"]);
    expect(options[0].label).toBe("Small vision · Reads photos · loaded");
    expect(options[1].label).toBe("Writer · Text only");
    expect(options[2].label).toBe("Unreported · Photo support unknown");
  });
  it("preserves an unavailable saved selection and removes duplicate discovery entries", () => {
    const options = modelPickerOptions([{ id: "one" }, { id: "one", name: "Duplicate" }], "saved-model");
    expect(options.map(option => option.id)).toEqual(["saved-model", "one"]);
    expect(options[0].missing).toBe(true);
    expect(options[0].label).toContain("not listed");
    expect(modelPickerOptions(undefined, "saved")[0].id).toBe("saved");
    expect(modelPickerOptions()).toEqual([]);
  });
});
