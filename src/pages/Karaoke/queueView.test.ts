// node --experimental-strip-types --test src/pages/Karaoke/queueView.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { dropIndex, insertIndex, moved, parts, playNextIndex, queuedCounts, whereOf, type QueueState } from "./queueView.ts";

const q = (names: string) => names.split("").map((c) => ({ qid: `q${c}`, song_id: `s${c.toLowerCase()}` }));
const state = (names: string, sung: number | undefined, playerState: string, qid: string | null = null, stop?: string): QueueState =>
  ({ queue: q(names), sung, player: { state: playerState, qid, stop } });

test("the three parts come from the server's count", () => {
  assert.deepEqual(parts(state("abcd", 2, "playing")), { sung: 2, on: 2, top: 3 });
  assert.deepEqual(parts(state("abcd", 2, "paused")), { sung: 2, on: 2, top: 3 });
  assert.deepEqual(parts(state("abcd", 2, "stopped")), { sung: 2, on: null, top: 2 });
  assert.deepEqual(parts(state("abcd", 4, "stopped")), { sung: 4, on: null, top: 4 });
  assert.deepEqual(parts(state("", 0, "stopped")), { sung: 0, on: null, top: 0 });
});

test("a count that makes no sense is held inside the queue", () => {
  assert.deepEqual(parts(state("ab", 9, "stopped")), { sung: 2, on: null, top: 2 });
  assert.deepEqual(parts(state("ab", -3, "playing")), { sung: 0, on: 0, top: 1 });
  assert.deepEqual(parts(state("ab", 2, "playing")), { sung: 2, on: null, top: 2 }); // "playing" past the end: none on
});

test("a server from before the count: by the player's entry", () => {
  assert.deepEqual(parts(state("abcd", undefined, "playing", "qb")), { sung: 1, on: 1, top: 2 });
  assert.deepEqual(parts(state("abcd", undefined, "stopped", "qb", "end")), { sung: 2, on: null, top: 2 });
  assert.deepEqual(parts(state("abcd", undefined, "stopped", "qb", "you")), { sung: 2, on: null, top: 2 });
  assert.deepEqual(parts(state("abcd", undefined, "stopped", "qb", "boot")), { sung: 1, on: null, top: 1 });
  // the old bug: an entry the page can't find made every sung song "to come". Nothing sung is the honest reading.
  assert.deepEqual(parts(state("abcd", undefined, "stopped", "gone")), { sung: 0, on: null, top: 0 });
  assert.deepEqual(parts(state("abcd", undefined, "stopped", null)), { sung: 0, on: null, top: 0 });
});

test("which part a row is in", () => {
  const p = parts(state("abcd", 1, "playing"));
  assert.deepEqual([0, 1, 2, 3].map((i) => whereOf(i, p)), ["played", "current", "next", "next"]);
  const s = parts(state("abcd", 2, "stopped"));
  assert.deepEqual([0, 1, 2, 3].map((i) => whereOf(i, s)), ["played", "played", "next", "next"]);
  const done = parts(state("ab", 2, "stopped")); // the queue played out: nothing is to come
  assert.deepEqual([0, 1].map((i) => whereOf(i, done)), ["played", "played"]);
});

test("Play next sends a song to come to the top, and the one already there nowhere", () => {
  const p = parts(state("abcde", 1, "playing")); // top = 2
  assert.equal(playNextIndex(2, p), undefined);
  assert.equal(playNextIndex(3, p), 2);
  assert.equal(playNextIndex(4, p), 2);
  assert.equal(playNextIndex(1, p), undefined); // the one on
  assert.equal(playNextIndex(0, p), undefined); // a sung one (the page adds it again instead)
});

test("a song counts as queued only while it is still to be sung", () => {
  const st: QueueState = { queue: q("abAcA"), sung: 1, player: { state: "playing", qid: "qb" } };
  assert.deepEqual([...queuedCounts(st)], [["sb", 1], ["sa", 2], ["sc", 1]]); // the first a is sung
});

test("a library song dragged in never lands above the top", () => {
  const p = parts(state("abcd", 1, "playing")); // top = 2
  assert.deepEqual([0, 1, 2, 3, 4, 99].map((over) => insertIndex(p, 4, over)), [2, 2, 2, 3, 4, 4]);
  const e = parts(state("", 0, "stopped"));
  assert.equal(insertIndex(e, 0, 0), 0);
});

test("a song to come dragged above the one playing goes to the top (it used to vanish)", () => {
  const p = parts(state("abcd", 1, "playing")); // a sung, b on, c d to come
  assert.equal(dropIndex(p, 4, 3, 0), 2);
  assert.equal(dropIndex(p, 4, 3, 1), 2);
  assert.equal(dropIndex(p, 4, 3, 2), 2);
  assert.equal(dropIndex(p, 4, 2, 0), null); // already the top
  assert.equal(dropIndex(p, 4, 2, 4), 3); // dropped on the foot: the end
});

test("the song that is on doesn't move, and a row that isn't there moves nothing", () => {
  const p = parts(state("abcd", 1, "playing"));
  for (const over of [0, 1, 2, 3, 4]) assert.equal(dropIndex(p, 4, 1, over), null);
  assert.equal(dropIndex(p, 4, -1, 2), null);
  assert.equal(dropIndex(p, 4, 7, 2), null);
});

test("a sung song dropped among the songs to come is to come again; among the sung it stays sung", () => {
  const p = parts(state("abcde", 2, "playing")); // a b sung, c on, d e to come
  assert.equal(dropIndex(p, 5, 0, 1), 1);
  assert.deepEqual(moved(q("abcde"), p, 0, 1).sung, 2);
  assert.equal(dropIndex(p, 5, 0, 4), 4);
  const m = moved(q("abcde"), p, 0, 4);
  assert.deepEqual([m.queue.map((e) => e.qid).join(""), m.sung], ["qbqcqdqeqa", 1]);
  assert.equal(dropIndex(p, 5, 1, 2), 2); // dropped on the one playing: the top of what is to come
  const n = moved(q("abcde"), p, 1, 2);
  assert.deepEqual([n.queue.map((e) => e.qid).join(""), n.sung], ["qaqcqbqdqe", 1]);
});

// The server's queue_move, said again in a few lines (routes/karaoke/store.py): for every small queue, every row
// and every place to drop it, what the page shows at once is what the server will answer, and the rules hold.
function serverMove(names: string[], sung: number, live: boolean, i: number, index: number) {
  const list = [...names];
  if (live && i === sung) return { list, sung };
  const [e] = list.splice(i, 1);
  const t = Math.max(0, Math.min(list.length, index));
  if (i < sung) {
    if (t < sung) {
      list.splice(t, 0, e);
      return { list, sung };
    }
    sung -= 1;
  }
  list.splice(Math.max(t, sung + (live ? 1 : 0)), 0, e);
  return { list, sung };
}

test("every drop on every small queue: the page and the server agree, and no rule breaks", () => {
  let n = 0;
  for (let len = 0; len <= 6; len++)
    for (let sung = 0; sung <= len; sung++)
      for (const live of [false, true]) {
        if (live && sung === len) continue;
        const names = Array.from({ length: len }, (_, k) => String.fromCharCode(97 + k));
        const st: QueueState = {
          queue: names.map((c) => ({ qid: c, song_id: c })),
          sung,
          player: { state: live ? "playing" : "stopped", qid: null },
        };
        const p = parts(st);
        for (let from = 0; from < len; from++)
          for (let over = 0; over <= len; over++) {
            n++;
            const to = dropIndex(p, len, from, over);
            const was = { sungSet: names.slice(0, sung), on: live ? names[sung] : null };
            if (to === null) {
              // nothing to send: the server, asked anyway for where it would have gone, would change nothing
              const s = serverMove(names, sung, live, from, Math.max(0, Math.min(len - 1, over)));
              if (from !== p.on) assert.deepEqual(s.list, names, `${names} sung ${sung} ${live} ${from}->${over}`);
              continue;
            }
            const mine = moved(names, p, from, to);
            const srv = serverMove(names, sung, live, from, to);
            assert.deepEqual([mine.queue, mine.sung], [srv.list, srv.sung], `${names} sung ${sung} ${live} ${from}->${over}`);
            // the song that is on is still on, in its place
            if (was.on) assert.equal(mine.queue[mine.sung], was.on);
            // a song to come is never among the sung
            for (const x of names.slice(p.top)) assert.ok(mine.queue.indexOf(x) >= mine.sung + (live ? 1 : 0));
            // nothing but the dragged one changes part or order
            const rest = (l: string[]) => l.filter((x) => x !== names[from]);
            assert.deepEqual(rest(mine.queue), rest(names));
          }
      }
  assert.ok(n > 500);
});
