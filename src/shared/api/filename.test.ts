// No call to the API names a bucket file by the grid's `#dup` copy id (owner, 22 Nov: publishing and favouriting a
// favourite from its date group failed with "File not found" / "failed to toggle favourite"). Bundled, then plain node:
//   npx esbuild src/shared/api/filename.test.ts --bundle --platform=node --format=esm --tsconfig=tsconfig.app.json \
//     --define:import.meta.env={} --outfile=/tmp/filename.test.mjs && node --test /tmp/filename.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import * as buckets from "./buckets";
import * as publish from "./publish";

const DUP = "20261122-133124-c5375f6a.jpg#dup";
const REAL = "20261122-133124-c5375f6a.jpg";

/** Every request the call makes: its URL (fragment included, as written) and body. */
async function sent(call: () => Promise<unknown>): Promise<string[]> {
  const seen: string[] = [];
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    seen.push(`${url} ${init?.body ?? ""}`);
    return new Response(JSON.stringify({ status: "favorited", success: true }), { status: 200 });
  }) as typeof fetch;
  await call();
  return seen;
}

const calls: Record<string, () => Promise<unknown>> = {
  animateStill: () => buckets.animateStill("north-screen", DUP),
  chooseClip: () => buckets.chooseClip("north-screen", DUP),
  detachClip: () => buckets.detachClip("north-screen", DUP),
  deleteFromBucket: () => buckets.deleteFromBucket("north-screen", DUP),
  moveToPosition: () => buckets.moveToPosition("north-screen", DUP, DUP),
  getImageComplete: () => buckets.getImageComplete("north-screen", DUP),
  toggleFavorite: () => buckets.toggleFavorite("north-screen", DUP, true),
  unFavorite: () => buckets.toggleFavorite("north-screen", DUP, false),
  deleteImage: () => buckets.deleteImage("north-screen", DUP),
  copyImageToBucket: () => buckets.copyImageToBucket("north-screen", "south-screen", DUP),
  publishImageUnified: () => publish.publishImageUnified({ dest_bucket_id: "north-screen", src_bucket_id: "north-screen", filename: DUP }),
  publishBucketFile: () => publish.publishBucketFile("north-screen", "north-screen", DUP),
};

for (const [name, call] of Object.entries(calls)) {
  test(`${name} sends the real filename`, async () => {
    const requests = await sent(call);
    assert.ok(requests.length > 0, "made no request");
    for (const r of requests) {
      assert.ok(!/#dup|%23dup/.test(r), `sent the copy id: ${r}`);
    }
    assert.ok(requests.some((r) => r.includes(REAL)), `never named ${REAL}: ${requests.join(" | ")}`);
  });
}
