import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import VideoReview, {
  emptyVideoReview,
  reviewCriteria,
  reviewRequestIsCurrent,
  reviewTimestamp,
} from "./VideoReview";
import ReviewLibrary, { reviewUploadError } from "./ReviewLibrary";

describe("Local video review boundaries", () => {
  it("requires human approval and leaves every criterion unchecked for a new take", () => {
    expect(emptyVideoReview("run", "take-one")).toEqual({
      kind: "run",
      id: "take-one",
      verdict: "unreviewed",
      notes: "",
      checklist: {},
      source_prompt: "",
      ai: null,
    });
    const html = renderToStaticMarkup(
      <VideoReview kind="run" videoId="take-one" />,
    );
    expect(html).toContain("Not reviewed");
    expect(html).toContain("Watch the full clip");
    expect(html).toContain("Audio &amp; dialogue");
    expect(html.match(/value="unchecked" selected=""/g)).toHaveLength(
      reviewCriteria.length,
    );
    expect(html).toContain("AI advice keeps your decision separate.");
    expect(html).toContain(
      "sound and lip sync still need your playback review",
    );
  });
  it("keeps writes and analysis locked until the saved review loads", () => {
    const html = renderToStaticMarkup(
      <VideoReview kind="asset" videoId="upload-one" />,
    );
    expect(html).toContain("Loading saved review");
    expect(html).toContain('<fieldset disabled=""');
    expect(html).toMatch(/disabled=""><svg[^]*?Save review<\/button>/);
    expect(html).toContain("Frame samples");
    expect(html).toContain("Qwen 3.8 27B");
  });
  it("does not attach another video or an unmounted review response", () => {
    expect(reviewRequestIsCurrent("run:first", "run:first")).toBe(true);
    expect(reviewRequestIsCurrent("run:first", "run:second")).toBe(false);
    expect(reviewRequestIsCurrent("run:first", "asset:first")).toBe(false);
    expect(reviewRequestIsCurrent("run:first", "run:first", false)).toBe(false);
  });
  it("formats video timestamps without converting missing or invalid evidence to zero", () => {
    expect(reviewTimestamp(0)).toBe("0:00");
    expect(reviewTimestamp(64.2)).toBe("1:04.2");
    expect(reviewTimestamp(4.1)).toBe("0:04.1");
    for (const time of [-1, NaN, Infinity])
      expect(reviewTimestamp(time)).toBe("Time unknown");
  });
  it("accepts supported local videos up to 128 MB and rejects invalid uploads before posting", () => {
    expect(
      reviewUploadError({ name: "Take.MP4", size: 128 * 1024 * 1024 }),
    ).toBe("");
    expect(reviewUploadError({ name: "motion.webm", size: 12 })).toBe("");
    expect(reviewUploadError({ name: "phone.mov", size: 1024 })).toBe("");
    expect(reviewUploadError({ name: "empty.mp4", size: 0 })).toContain(
      "empty",
    );
    expect(
      reviewUploadError({ name: "big.mp4", size: 128 * 1024 * 1024 + 1 }),
    ).toContain("128 MB");
    expect(reviewUploadError({ name: "photo.png", size: 1024 })).toContain(
      "choose an MP4",
    );
  });
  it("offers an accessible local library with upload, search and verdict filters", () => {
    const html = renderToStaticMarkup(<ReviewLibrary onClose={() => {}} />);
    expect(html).toContain('role="dialog" aria-modal="true"');
    expect(html).toContain("Choose videos");
    expect(html).toContain("128 MB / 60 minutes");
    expect(html).toContain('aria-label="Search video library"');
    expect(html).toContain('aria-label="Filter video reviews"');
    expect(html).toContain("Generated takes also have a review panel");
    expect(
      renderToStaticMarkup(<ReviewLibrary active={false} onClose={() => {}} />),
    ).toBe("");
  });
});
