/**
 * A text-safe version of a team colour: mix toward ink until it clears 4.5:1 on --paper.
 *
 * Input: a team colour as `#rgb` or `#rrggbb` (the leading `#` is optional; case and
 * surrounding whitespace are ignored). Anything unparseable returns `#101114`.
 *
 * Target: WCAG 2.x contrast of at least 4.5:1 against `#f4f3f0`, the value of the
 * `--paper` token in `src/index.css`.
 *
 * Worst case: `#101114` (the `--ink` token), the last blend step toward ink. A blend
 * toward `#ffffff` is also tried as a safety net for a colour that darkening cannot fix
 * (plan R4); whichever direction reaches 4.5:1 first, toward ink or toward white, wins.
 *
 * Pure: no React, no DOM, no side effects.
 */

const PAPER = "#f4f3f0";
const INK = "#101114";
const WHITE = "#ffffff";
const TARGET_RATIO = 4.5;

type Rgb = [number, number, number];

function parseHex(hex: string): Rgb | null {
  const cleaned = String(hex ?? "").trim().replace(/^#/, "").toLowerCase();
  if (!/^([0-9a-f]{3}|[0-9a-f]{6})$/.test(cleaned)) return null;
  const expanded =
    cleaned.length === 3
      ? cleaned.split("").map((c) => c + c).join("")
      : cleaned;
  return [
    parseInt(expanded.slice(0, 2), 16),
    parseInt(expanded.slice(2, 4), 16),
    parseInt(expanded.slice(4, 6), 16),
  ];
}

function toHex([r, g, b]: Rgb): string {
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

/** WCAG 2.x relative luminance of an sRGB colour. */
function luminance([r, g, b]: Rgb): number {
  const channel = (value: number) => {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG 2.x contrast ratio between two colours, 1 to 21. */
function contrast(a: Rgb, b: Rgb): number {
  const la = luminance(a);
  const lb = luminance(b);
  const lighter = Math.max(la, lb);
  const darker = Math.min(la, lb);
  return (lighter + 0.05) / (darker + 0.05);
}

/** Blend `from` toward `to` by `t` (0 to 1), rounding each channel. */
function mix(from: Rgb, to: Rgb, t: number): Rgb {
  return [
    Math.round(from[0] + (to[0] - from[0]) * t),
    Math.round(from[1] + (to[1] - from[1]) * t),
    Math.round(from[2] + (to[2] - from[2]) * t),
  ];
}

/** Index (0-20) of the first 5 % step toward `to` that clears the target, or -1. */
function firstPassingStep(start: Rgb, to: Rgb, paper: Rgb): number {
  for (let step = 1; step <= 20; step += 1) {
    if (contrast(mix(start, to, step / 20), paper) >= TARGET_RATIO) return step;
  }
  return -1;
}

export function accentText(hex: string): string {
  const paper = parseHex(PAPER) as Rgb;
  const start = parseHex(hex);
  if (!start) return INK;

  // Already readable on paper: keep the team colour untouched.
  if (contrast(start, paper) >= TARGET_RATIO) return toHex(start);

  const ink = parseHex(INK) as Rgb;
  const white = parseHex(WHITE) as Rgb;
  const inkStep = firstPassingStep(start, ink, paper);
  const whiteStep = firstPassingStep(start, white, paper);

  // Toward ink normally wins; toward white is the R4 fallback for a colour that
  // darkening cannot fix (and the one that needs the smaller shift if both work).
  if (inkStep === -1) {
    return whiteStep === -1 ? INK : toHex(mix(start, white, whiteStep / 20));
  }
  if (whiteStep !== -1 && whiteStep <= inkStep) {
    return toHex(mix(start, white, whiteStep / 20));
  }
  return toHex(mix(start, ink, inkStep / 20));
}
