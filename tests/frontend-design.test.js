import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../web/index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");

test("mobile design has clear hierarchy for match, statistics, and table", () => {
  assert.match(html, /class="app-shell"/);
  assert.match(html, /\.match-hero/);
  assert.match(html, /\.stat-summary/);
  assert.match(html, /\.table-card/);
  assert.match(app, /match-hero/);
  assert.match(app, /stat-summary/);
  assert.match(app, /table-card/);
});

test("the sources tab explains how to send a protocol from a phone", () => {
  // Safari only: no other iOS browser offers this share option at all.
  assert.match(app, /Tylko Safari/);
  // The three taps that actually work. An iOS Shortcut extracting outerHTML
  // stood here first and never got past 657 kB of page; if these strings go,
  // the instructions have drifted back to something unfollowable.
  assert.match(app, /Kompletna witryna/);
  assert.match(app, /Opcje/);
});

test("a result opens the match behind it", () => {
  // The row carries its index into state.matches: the list is filtered and
  // reversed, so its own position means nothing to renderMatch.
  assert.match(app, /data-match=/);
  assert.match(app, /function renderMatch/);
  // Both sides in one timeline, theirs from the protocol only.
  assert.match(app, /theirGoals/);
  // pushState, so the iOS back swipe leaves the match and not the whole app.
  assert.match(app, /history\.pushState/);
  assert.match(html, /\.event\.theirs/);
});

test("an own goal is shown for the team it counted for, and marked", () => {
  // It reads as a goal by an opponent otherwise, on our side of the timeline.
  assert.match(app, /ownGoals/);
  assert.match(app, /\(sam\.\)/);
});
