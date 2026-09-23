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
  // Both sides shown, theirs from the protocol only.
  assert.match(app, /theirGoals/);
  // pushState, so the iOS back swipe leaves the match and not the whole app.
  assert.match(app, /history\.pushState/);
  // Opponents recede rather than disappear, in every one of the three lists.
  assert.match(html, /\.goal\.theirs/);
  assert.match(html, /\.row\.theirs/);
  assert.match(html, /\.subs\.theirs/);
});

test("an own goal is shown for the team it counted for, and marked", () => {
  // It reads as a goal by an opponent otherwise, on our side of the match.
  assert.match(app, /ownGoals/);
  // The red ball, not the plain one every other goal gets, plus the word -
  // the colour alone is a coin flip on a small screen.
  assert.match(app, /g\.own \? "🔴" : "⚽"/);
  assert.match(app, /samobój/);
});

test("every result is its own tile, openable by tap and by keyboard", () => {
  // One card per match rather than rows inside a single card: the shared card
  // needed rules to fake a border between matches, and the date, both team
  // names and the score fought over one line at phone width.
  assert.match(app, /class="card match-card"/);
  assert.match(html, /\.match-card \{/);
  assert.doesNotMatch(html, /\.results-card/);
  // role="button" and tabindex without a key handler lie to a screen reader:
  // it announces a button that Enter and Space do nothing to.
  assert.match(app, /role="button" tabindex="0" data-match=/);
  assert.match(app, /event\.key !== "Enter" && event\.key !== " "/);
  // Delegated on the container - tiles arrive with every round played.
  assert.match(app, /box\.addEventListener\("click"/);
});

test("goals sit under the score, cards and substitutions below it", () => {
  // Goals belong to the scoreline, so they render inside the hero card and
  // not as a section of their own.
  assert.match(app, /\$\{goalsBlock\(m\)\}\s*<\/div>/);
  assert.match(app, /<h2>Kartki<\/h2>/);
  assert.match(app, /<h2>Zmiany<\/h2>/);
  // The old single "Przebieg" list mixed all three together.
  assert.doesNotMatch(app, /Przebieg/);
});

test("the running score is hidden unless it lands on the final result", () => {
  // Goals come from whichever source carries them, so a match can be stored
  // with fewer than were scored. Half a tally reads as the real thing.
  assert.match(app, /exact: home === match\.homeScore && away === match\.awayScore/);
  assert.match(app, /\$\{exact \? `<span class="run">/);
});

test("a substitution sequence is one minute, not a pair of players", () => {
  // The protocol records who went off and who came on, never who replaced
  // whom - two of each moved at 80' against Grodziszczanka.
  assert.match(app, /function subSequences/);
  assert.match(app, /byMinute/);
  // Walking off on a red card is not a substitution.
  assert.match(app, /sentOff/);
  assert.match(html, /\.seq \{/);
});

test("a broken source says it is broken, not how many times it failed", () => {
  // The cell used to interpolate the retry counter straight into itself. That
  // count only grew because the retries only kept coming, and since when is
  // already in the column beside it. Asserted on the call rather than on the
  // absence of the old string, which also lives in the comment explaining it.
  assert.match(app, /\$\{sourceWarning\(s\)\}/);
  assert.match(app, /nie działa/);
  // A blip is not a breakage - the same threshold run.py alerts on.
  assert.match(app, /const BROKEN_AFTER = 3;/);
  assert.match(html, /\.warn \{/);
});

test("the app can tell it is out of date and says so at the top", () => {
  // A plain integer, because the comparison has to be "newer than" and not
  // "different from": meta.json trails a deploy by a workflow tick.
  assert.match(app, /^const APP_VERSION = \d+;$/m);
  assert.match(app, /latest <= APP_VERSION/);
  // The banner is markup in the page, not built on the fly, so it can be
  // revealed before any render has run.
  assert.match(html, /id="updateBanner"/);
  assert.match(html, /\.update-banner \{/);
  // Sticky: the notice has to reach someone halfway down the results list.
  assert.match(html, /position: sticky/);
  // Coming back to the app is when a new version is most likely waiting.
  assert.match(app, /visibilitychange/);
});

test("the shell is refreshed by clearing its cache, not by a reload flag", () => {
  // location.reload(true) has been a no-op for years.
  assert.match(app, /caches\.keys\(\)/);
  assert.match(app, /startsWith\("dynovia-shell"\)/);
  assert.match(app, /registration\.update\(\)/);
  // Without this the revalidation can be answered from the browser's own HTTP
  // cache and put back the very file it exists to replace.
  const sw = readFileSync(new URL("../web/sw.js", import.meta.url), "utf8");
  assert.match(sw, /cache: "reload"/);
});
