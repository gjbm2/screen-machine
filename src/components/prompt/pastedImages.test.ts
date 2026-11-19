// Tests for pastedImages (a picture pasted into the generator's prompt). Plain node:
//   node --experimental-strip-types --test src/components/prompt/pastedImages.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { pastedImages } from "./pastedImages.ts";

type Item = { kind: string; type: string; getAsFile: () => File | null };

function clipboard(items: Item[]): DataTransfer {
  return { items } as unknown as DataTransfer;
}

const image = (type = "image/png"): Item => ({ kind: "file", type, getAsFile: () => new File([new Uint8Array([1, 2, 3])], "image.png", { type }) });
const text: Item = { kind: "string", type: "text/plain", getAsFile: () => null };

test("a copied picture is taken, with a name of its own", () => {
  const files = pastedImages(clipboard([image()]), 1000);
  assert.equal(files.length, 1);
  assert.equal(files[0].name, "pasted-1000-0.png");
  assert.equal(files[0].type, "image/png");
  assert.equal(files[0].size, 3);
});

test("two pictures never share a name", () => {
  const names = pastedImages(clipboard([image(), image("image/jpeg")]), 1000).map((f) => f.name);
  assert.deepEqual(names, ["pasted-1000-0.png", "pasted-1000-1.jpeg"]);
});

test("text, or a picture copied with its page's text, pastes only the picture", () => {
  assert.equal(pastedImages(clipboard([text]), 1000).length, 0);
  assert.equal(pastedImages(clipboard([text, image()]), 1000).length, 1);
});

test("an empty or missing clipboard gives nothing", () => {
  assert.equal(pastedImages(clipboard([]), 1000).length, 0);
  assert.equal(pastedImages(null, 1000).length, 0);
  assert.equal(pastedImages(clipboard([{ kind: "file", type: "application/pdf", getAsFile: () => null }]), 1000).length, 0);
});
