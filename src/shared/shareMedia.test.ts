// Run: node --experimental-strip-types --test src/shared/shareMedia.test.ts
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { fileNameOf, shareMedia } from './shareMedia.ts';

const g = globalThis as any;
let shared: any[] = [];
let downloads: string[] = [];
let shareError: string | null = null;
let fetches = 0;

beforeEach(() => {
  shared = []; downloads = []; shareError = null; fetches = 0;
  g.fetch = async (url: string) => {
    fetches += 1;
    const type = url.endsWith('.mp4') ? 'video/mp4' : 'application/octet-stream';
    return new Response(new Blob([new Uint8Array([1, 2, 3])], { type }), { status: url.includes('missing') ? 404 : 200 });
  };
  Object.defineProperty(g, 'navigator', { configurable: true, value: {
    canShare: (d: any) => Array.isArray(d.files) && d.files.length > 0,
    share: async (d: any) => {
      if (shareError) { const e = new Error(shareError); e.name = shareError; throw e; }
      shared.push(d);
    },
  } });
  g.URL.createObjectURL = () => 'blob:x';
  g.URL.revokeObjectURL = () => {};
  g.document = {
    body: { appendChild: () => {} },
    createElement: () => { const a: any = { click: () => downloads.push(a.download), remove: () => {} }; return a; },
  };
});

test('the name comes from the URL, without query or #dup', () => {
  assert.equal(fileNameOf('/output/north-screen/a%20b.mp4?x=1'), 'a b.mp4');
  assert.equal(fileNameOf('/output/north-screen/pic.jpg#dup'), 'pic.jpg');
});

test('shares the file itself, with its type, and nothing else', async () => {
  assert.equal(await shareMedia('/output/b/clip1.mp4'), 'shared');
  assert.equal(shared.length, 1);
  assert.deepEqual(Object.keys(shared[0]), ['files']);
  const f = shared[0].files[0];
  assert.equal(f.name, 'clip1.mp4');
  assert.equal(f.type, 'video/mp4');
});

test('a server octet-stream is typed from the file name', async () => {
  await shareMedia('/output/b/pic2.jpg');
  assert.equal(shared[0].files[0].type, 'image/jpeg');
});

test('a slow fetch that loses the tap asks for another, and the second tap does not fetch again', async () => {
  shareError = 'NotAllowedError';
  assert.equal(await shareMedia('/output/b/clip3.mp4'), 'needs-tap');
  shareError = null;
  assert.equal(await shareMedia('/output/b/clip3.mp4'), 'shared');
  assert.equal(fetches, 1);
});

test('closing the share sheet is not an error', async () => {
  shareError = 'AbortError';
  assert.equal(await shareMedia('/output/b/clip4.mp4'), 'cancelled');
});

test('no share sheet: downloads the file', async () => {
  Object.defineProperty(g, 'navigator', { configurable: true, value: {} });
  assert.equal(await shareMedia('/output/b/pic5.png'), 'downloaded');
  assert.deepEqual(downloads, ['pic5.png']);
});

test('a missing file throws', async () => {
  await assert.rejects(shareMedia('/output/b/missing.png'));
});
