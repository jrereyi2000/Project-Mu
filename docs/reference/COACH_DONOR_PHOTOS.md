# Coach Donor Photos (Spike D)

Working reference for `docs/in_progress/COACH_SAVE_WRITES_PLAN.md` §2.7 / §2.8 / R5. It answers two questions: where per-team real NFL **coach** photos come from, and whether the Mu API already serves `static/coaches/`.

Verified 2026-11-10. Every URL below was fetched; status codes and dimensions are what came back, not estimates. Images were downloaded to `/tmp/coach_photos/` only; nothing was added to the repo.

---

## 1. Web serving: `/static/coaches/` works today

**Answer: yes. Nothing new is needed — no route, no manifest, no build step.**

| What | Evidence |
|---|---|
| Static root | `server/api.py:188` — `_STATIC_DIR = <repo>/static` |
| Root is created on startup | `server/api.py:189` — `os.makedirs(_STATIC_DIR, exist_ok=True)` |
| Mount and URL prefix | `server/api.py:190` — `app.mount("/static", RevalidatedStaticFiles(directory=_STATIC_DIR), name="static")` |
| Subdirectories served | `RevalidatedStaticFiles` (`server/api.py:171-176`) subclasses Starlette `StaticFiles`, which resolves each request path under the mount via `lookup_path()` (`starlette/staticfiles.py`, Starlette 1.10.0). It stats the resolved file per request; it does not cache a directory listing. A `static/coaches/` created after the server started is served without a restart. |
| `.webp` response type | `mimetypes.guess_type("x.webp")` → `('image/webp', None)`; Starlette `FileResponse` uses `guess_type(...)[0]` and falls back to `application/octet-stream` only when unknown. So `.webp` goes out as `image/webp`. |
| Cache behavior | `RevalidatedStaticFiles.file_response` adds `Cache-Control: no-cache` (`server/api.py:173-176`), so regenerated portraits revalidate (ETag / 304) instead of serving stale images. |
| No route shadowing | The SPA catch-all is mounted **last**, at `server/api.py:3514` (`app.mount("/", StaticFiles(...))`), after `/static`; `/static/*` always wins. |

So a file written to `static/coaches/{coach_key}.webp` is served at:

```
/static/coaches/{coach_key}.webp
```

Caveat: only the static **root** must exist at mount time (it does, line 189). The `coaches/` subdirectory itself may be created at runtime by `headshots/coach_headshots.py`.

### Git tracking of `static/` and `data/`

- `.gitignore:13` ignores `data/*`, with narrow exceptions only for `data/scouting/`, `data/history/` and `data/gm_voices_2026.json`. Therefore **`data/headshots/coach_donors/` is git-ignored**, exactly like the existing player donor library (`data/headshots/donors/registry.json` is ignored too). The coach donor photos and `registry.json` are local-only and must not be committed.
- `static/` is **not** in `.gitignore`, and the repo already tracks 1,695 files under `static/`, including `static/players/*.webp`. So generated coach WebPs would show as untracked and, by existing convention, would be candidates to commit. No `.gitkeep` is needed for serving; the directory appears when the first file is written.

Whether generated coach WebPs *should* be committed is a JR decision (they are derivatives of NFL photos — §5).

---

## 2. Photo sources

### 2.1 Team official sites — recommended

Every club's coach roster page embeds studio headshots on the NFL clubs CDN (`static.clubs.nfl.com`). These are the best donor material found: media-day framing, team polo / quarter-zip / jacket, chest logo, seamless studio background, high resolution.

**Discovery pattern.** Fetch the team's coach page, then extract image URLs:

```
https://www.{club-domain}/team/coaches/           # roughly half the clubs
https://www.{club-domain}/team/coaches-roster/    # the rest (verified: ATL, CIN, HOU, IND, MIA, TB, TEN, DEN, LV, SF, KC)
```

Try both; one of the two returns the roster grid with photos. A few teams serve a thin `/team/coaches/` landing page (no photos) and put the staff on `/team/coaches-roster/`.

Page HTML contains entries of the form (URL may be JSON-escaped as `https:\/\/...` or scheme-relative `//static...`):

```
https://static.clubs.nfl.com/image/{upload|private}/t_headshot_desktop[/t_lazy]/f_auto/{club}/{public_id}.jpg
https://static.clubs.nfl.com/image/{upload|private}/t_person_squared_mobile[/t_lazy]/f_png/{club}/{public_id}.png
```

**Raw high-resolution pattern.** Strip the transform segments (`t_headshot_desktop`, `t_person_squared_mobile`, `t_lazy`; keep the format segment `f_auto` or `f_png`):

```
https://static.clubs.nfl.com/image/{upload|private}/{f_auto|f_png}/{club}/{public_id}.{jpg|png}
```

- Fetchable without a login: **yes**, plain GET, no auth, no API key.
- `image/private/` assets are still publicly fetchable when the full URL is known (verified with PHI below).
- The transformed URL is only the UI thumbnail (typically 400×400 or a 768×768 square). Always fetch the raw form for a donor plate.
- Some team pages render a grayscale/blurred square through `t_person_squared_mobile` (verified on NE); that is a CMS transform, not the source image. Use the raw upload.
- Coverage: all 32 clubs embed `static.clubs.nfl.com` person images on one of the two coach-page forms. A 32-team probe found images on 25 pages at `/team/coaches/`; the other 7 (ATL, CIN, HOU, IND, MIA, TB, TEN) returned hundreds of person-image references at `/team/coaches-roster/`. Extraction must handle JSON-escaped (`https:\/\/...`) and scheme-relative (`//static...`) URL forms; the first naive grep undercounted several pages.
- Names come from the card `alt` text (e.g. `alt="Chidera Uzo-Diribe HS 2026"`) or the surrounding anchor.
- A donor does **not** have to be the head coach. Any staff member photographed in that team's coaching gear is a valid plate; the face is replaced in the inpaint step. This makes 2–3 plates per team trivial to source.

**Image type:** studio media-day portrait, 16:9-ish, seamless grey/blue-grey background, full photo (RGB, not a cut-out). Attire observed: team polo with chest logo (DET, NE), and the same pages carry quarter-zips and jackets. Headsets appear on some sideline photos on team sites, but the studio roster shots generally have none.

**Licence, plain terms:** the club sites are NFL properties. The NFL.com Terms of Use (fetched, 200) state that the NFL owns all right, title and interest in the Services and their contents; outside what the terms expressly allow you may not copy, reproduce, modify, distribute, display or transmit any part of them; use is "solely for your own individual non-commercial and informational purposes"; systematic collection to build a database requires prior written permission. Club marks are separately owned. **No licence is granted for using these photos as generative base images or for redistributing them or derivatives.** For a private, local simulation this is a personal-use grey area; it is not a source you can ship or publish.

### 2.2 ESPN — partial, not recommended

Coach identities are available unauthenticated from ESPN's core API:

```
https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/{season}/teams/{team_id}/coaches
```

Verified: team 6 (DAL) → 200 JSON, `count: 1`, ref to coach id `17530` (Brian Schottenheimer). The response gives the coach ID and a person ref, but no headshot field.

Tested ESPN image paths for real coach IDs:

| Path | Result |
|---|---|
| `https://a.espncdn.com/i/headshots/nfl/coaches/full/{id}.png` | **404** for every ID tested (1832, 17553, 17578, 17533, 2499338, 7157, 4372439, 5173599, 14882, 17762) |
| `https://a.espncdn.com/i/headshots/nfl/players/full/{id}.png` | **200 only for coaches who also have an ESPN athlete record**: Kyle Shanahan `17533` (200, `image/png`, 173,251 B, 600×436 RGBA), Todd Bowles `7157` (200, 267,564 B), Kellen Moore `14882` (200, 161,341 B), Dave Canales `17762` (200, 169,392 B) |
| Same player path for non-player coaches | **404**: Dan Campbell `1832`, Andy Reid `17553`, Mike Tomlin `17578`, Sean McVay `2499338`, Nick Sirianni `4372439`, John Harbaugh `5173599` |
| `https://www.espn.com/nfl/coach/_/id/17530` | **404** |

The four images that do exist are 600×436 RGBA cut-outs with mixed attire (Shanahan in a suit and white shirt, Canales in a plain black polo). **Verdict:** cannot cover 32 teams, attire is not reliably coaching gear, and the licence is no better than the club sites. Use only as a curiosity, not a source.

### 2.3 Pro-Football-Reference — unusable

- `https://www.pro-football-reference.com/coaches/TomlMi0.htm` → **403** (also 403 with a desktop browser User-Agent), and WebFetch returned 403. `robots.txt` is behind a Cloudflare challenge (403).
- Programmatic access is blocked; Sports Reference's terms restrict scraping and reuse, and any images on the site are third-party licensed.
- Verdict: do not use. (Could not verify coach-page image content because the site would not serve a response.)

### 2.4 Wikimedia Commons — licensed fallback / alternative

Commons is fully scriptable through the MediaWiki API and each file carries explicit licence metadata:

```
https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch={coach}&gsrnamespace=6&gsrlimit=5&prop=imageinfo&iiprop=url|size|extmetadata&format=json
```

Verified results (200 JSON):

| Search | Files and licences |
|---|---|
| Mike Tomlin | CC BY 2.0 (1484×1632, 2464×1632), CC0 (561×760, 860×1075) |
| Dan Campbell | CC BY 3.0 (912×1248, 403×555 crop), CC BY-SA 4.0 (1006×1730), CC BY-SA 2.0 (7008×4672, 730×1006 crop) |
| Sean McVay | CC BY 2.0 (998×869, 833×1234), CC BY-SA 2.0 (4650×3298, 426×524 crop), CC BY-SA 4.0 (1536×2048) |

- Fetchable without login: **yes** (API and `upload.wikimedia.org`).
- Image type: mostly game/sideline/press photos, not studio cut-outs; backgrounds vary and framing is inconsistent. Some are cropped close-ups.
- Licence: per-image; CC BY 2.0/3.0/4.0 and CC BY-SA 2.0/4.0 require attribution (author, licence, source link); CC0 requires none. Share-alike applies to adaptations if distributed.
- Verdict: the only clean-licence path for coach photos, but each plate needs more work (background removal, crop, collar tracing). Coverage per coach/team is uneven. Good fallback, not the primary library.

### 2.5 Other sources checked

| Source | Result | Verdict |
|---|---|---|
| Getty / AP Images | Paid and licensed; no free programme; previews are watermarked. | Not usable |
| NFL.com proper (`/coaches/`, `/coaches/{slug}/`) | Both **404**. No coach index on nfl.com. | Use club sites instead |
| Madden's own portrait library | Out of scope here; R4 / Spike A cover it. | N/A |

### Source summary table

| Source | URL pattern | Fetchable? | Licence in plain terms | Image type | Verdict |
|---|---|---|---|---|---|
| Team official sites | `static.clubs.nfl.com/image/{upload\|private}/{f_auto\|f_png}/{club}/{id}.{jpg\|png}` (raw); page `/team/coaches/` or `/team/coaches-roster/` | Yes, no login | NFL.com Terms: personal, non-commercial only; no copying, modification or redistribution; club marks owned | Studio media-day photo, team polo/quarter-zip/jacket, seamless bg, 1600×900–2560×1440 | **Best quality and coverage; local personal use only** |
| ESPN | Coach IDs from `sports.core.api.espn.com/.../teams/{id}/coaches`; headshot only via `a.espncdn.com/i/headshots/nfl/players/full/{coach_id}.png` | Partially — only former-player coaches | ESPN terms: personal, non-commercial; no redistribution | 600×436 RGBA cut-out, mixed suit/polo | Not recommended (partial, inconsistent) |
| Pro-Football-Reference | N/A | **No** — Cloudflare 403 | Terms restrict scraping/reuse | Unknown (could not fetch) | Unusable |
| Wikimedia Commons | `commons.wikimedia.org/w/api.php` + `upload.wikimedia.org` | Yes | Per image: CC BY 2.0/3.0/4.0, CC BY-SA 2.0/4.0, CC0; attribution required except CC0 | Game/sideline photos, varied sizes | Licensed fallback / clean alternative |
| Getty / AP | N/A | No (paid) | Commercial licence required | Editorial | Unusable |
| NFL.com coach index | `/coaches/` | No — 404 | — | — | Use club sites |

---

## 3. Worked examples (fetched, not inferred)

Raw URLs are the transform-stripped uploads; dimensions are from the downloaded file.

| Team | Coach page | Raw URL | Status / type | Dimensions | Notes |
|---|---|---|---|---|---|
| DET | `https://www.detroitlions.com/team/coaches/` (200) | `https://static.clubs.nfl.com/image/upload/f_auto/lions/bxt18sbncaorsj4s6g9c.jpg` | 200 `image/jpeg`, 284,250 B | **2560×1440** | Blue team polo with Lions chest logo, grey studio backdrop |
| NE | `https://www.patriots.com/team/coaches/` (200) | `https://static.clubs.nfl.com/image/upload/f_png/patriots/baavub1gduiuir0qouys.png` | 200 `image/png`, 2,692,639 B | **2000×1125** | Navy striped polo with Patriots logo, blue-grey backdrop |
| PHI | `https://www.philadelphiaeagles.com/team/coaches/` (200) | `https://static.clubs.nfl.com/image/private/f_auto/eagles/omolofohcqwqdisgdilu.jpg` | 200 `image/jpeg`, 98,968 B | **1920×1080** | `private/` path is still publicly fetchable |
| KC | `https://www.chiefs.com/team/coaches-roster/` (200) | `https://static.clubs.nfl.com/image/upload/f_auto/chiefs/za8x1xxvglmkq5mzet3n.jpg` | 200 `image/jpeg`, 239,249 B | **1920×1080** | `-roster/` page form |
| ARI | `https://www.azcardinals.com/team/coaches/` (200) | `https://static.clubs.nfl.com/image/upload/f_auto/cardinals/oslbhmncmtr07ff5mnwu.jpg` | 200 `image/jpeg`, 86,189 B | **1600×900** | Smallest raw observed; still adequate after crop |
| GB | `https://www.packers.com/team/coaches/` (200) | `https://static.clubs.nfl.com/image/upload/f_png/packers/a5d5lbdr9nxg44ektpkd.png` | 200 `image/png`, 5,647,415 B | **2560×1440** RGBA | PNG/alpha variant |

Thumbnail-vs-raw evidence: the Lions thumbnail `.../t_headshot_desktop/f_auto/lions/bxt18sbncaorsj4s6g9c.jpg` is 400×400 / 14,423 B; the same asset without transforms is 2560×1440 / 284,250 B. The Patriots `t_person_squared_mobile` thumbnail is 768×768 **mode L** (grayscale), which is a UI treatment and must not be used.

---

## 4. Recommended donor-library plan

### Plates per team

**3 plates per team (minimum 2), 96 plates total.** Weight tiers do not apply. Vary:

1. **Build** — one large/round frame (former lineman type), one average, one lean.
2. **Clothing** — one polo, one quarter-zip, one jacket/softshell.
3. **Age** — mix in at least one 50+ coach with grey hair, since the generator draws ages 32–72 (`appearance(..., role="coach")`).
4. **Headset** — optional. A headset sits in the region being repainted, so it will usually be erased; prefer headset-free studio plates for donors. Keep one sideline/headset plate only if the pipeline is changed to preserve it.

The donor's identity is irrelevant; any staff member on the team page works. This is why 3 per team is easy despite many invented head coaches.

### Path layout (as the plan commits)

```
data/headshots/coach_donors/
  ARI_1.png
  ARI_1_mask.png
  ARI_2.png
  ARI_2_mask.png
  ...
  registry.json
```

`data/*` is git-ignored (`.gitignore:13`), so this whole tree is local-only, matching `data/headshots/donors/`. Do not commit it, and do not add a negating rule unless JR explicitly wants the donor photos in git.

Normalization: fetch the raw upload, center-crop to a square around the head/chest, resize to **1024×1024** (the player donor convention), and keep the studio background. The existing player builder pads a 1000×1000 ESPN cut-out onto a 1024 white canvas (`headshots/local/build_donor_library.py:139-144`); coaches need a center-crop instead of a pad, because the source is 16:9 and the head is not centered in a square. A 1600×900 raw yields a ~900 px square crop — mild upscaling to 1024 is acceptable; prefer the 1920×1080+ sources where available. Record the crop box / head anchor so a re-crop is reproducible.

### `registry.json` shape

Mirror the player registry's team-keyed, candidate-list style (`headshots/local/donors.py:242-257`), but with a `plates` list instead of `tiers`:

```json
{
  "version": 1,
  "generated_at": "2026-11-10",
  "ARI": {
    "name": "Arizona Cardinals",
    "abbr": "ARI",
    "plates": [
      {
        "id": "ARI_1",
        "path": "data/headshots/coach_donors/ARI_1.png",
        "mask_path": "data/headshots/coach_donors/ARI_1_mask.png",
        "mask_polygon": [[0, 0], [1024, 0], [1024, 640], [610, 720], [560, 820], [512, 900], [464, 820], [414, 720], [0, 640]],
        "head_anchor": [512, 360],
        "crop_box": [320, 0, 1280, 960],
        "collar_kind": "polo",
        "build": "average",
        "clothing": "polo",
        "headset": false,
        "source": "team_site",
        "source_page": "https://www.azcardinals.com/team/coaches/",
        "source_url": "https://static.clubs.nfl.com/image/upload/f_auto/cardinals/oslbhmncmtr07ff5mnwu.jpg",
        "licence": "NFL.com Terms of Use - personal non-commercial only",
        "attribution": null,
        "credit": "Arizona Cardinals",
        "captured_at": "2026-11-10",
        "active": true,
        "disqualified": []
      }
    ]
  }
}
```

Fields that matter operationally:

- `path`, `mask_path` — consumed by the same config keys the player adapter sets: `base_image_path` / `mask_image_path` (`headshots/local/adapter.py:151-152`).
- `mask_polygon` — normalized 0–1024 coordinates so the mask can be rebuilt or tweaked without re-tracing from scratch.
- `head_anchor`, `crop_box` — makes the 1024 crop reproducible and lets the plate step align the generated face.
- `build`, `clothing`, `headset`, `collar_kind` — the coach analogue of weight tiers; used to pick 2–3 plates for variety.
- `source`, `source_url`, `licence`, `attribution`, `credit`, `captured_at` — provenance and the licence decision trail. Required for any Commons plate with CC BY/SA.
- `disqualified` — same convention as the player registry, so a badly framed plate can be swapped without deleting it.

### Aperture mask for a coach

Use the same convention as `donors.generate_tier_mask` (`headshots/local/donors.py:125-144`): a 1024×1024 `L` image, **white = repaint**, **black = preserve**, with a **2-px Gaussian blur** feather. Do **not** reuse `POLYS_5TIER`: those polygons trace a jersey collar plus shoulder pads and deliberately preserve the metallic NFL shield at the collar. A coaching top has no shoulder pads and no NFL shield at the collar; its shape is a soft polo collar/placket, a stand collar with a short zip, or jacket lapels.

Trace per plate, because framing and collar position vary:

- Start at the top edge, full width, down the outside of the shoulders to roughly the collarbone line.
- Follow the **skin-to-fabric boundary**: down the sides of the neck, around the collar points, and into the collar opening / placket V. Stop 3–5 px inside the fabric edge, exactly the player rule.
- Preserve the **chest logo** below the collar; do not let the polygon reach it.
- Keep the 2-px feather for contact-shadow blending.

Suggested collar archetypes (store as `collar_kind`):

| Kind | Trace |
|---|---|
| `polo` | Soft collar points; V or short button placket to the top button |
| `quarter_zip` | Higher stand collar with a short zip; opening narrower and higher |
| `jacket` | Lapels / full zip; opening is a wider V |

Because the plant puts all plates at 1024×1024 with a recorded head anchor, one polygon template per `collar_kind` can seed the trace, then be nudged per plate. Save the final polygon in the registry.

### Integration with the existing local pipeline

`headshots/coach_headshots.py` should resolve a plate and pass:

```python
cfg["donor_plate"] = plate
cfg["base_image_path"] = plate["path"]
cfg["mask_image_path"] = plate["mask_path"]
```

matching the adapter's existing contract. The `generate_coach_headshot(profile, team)` function otherwise follows the rookie flow: prompt with "NFL coach, team-colour coaching polo/quarter-zip", negative prompt carrying "jersey, shoulder pads, helmet, number", `rembg`, 700×508 WebP to `static/coaches/{coach_key}.webp`, 512×512 BC3 DDS to `data/headshots/dds/coaches/{coach_key}.dds`.

### Fallback when a team has no usable plate

Team sites cover all 32, so the fallback should be rare. In order:

1. Pick another staff member from the same team page (build/clothing variety is the only constraint).
2. Use a Wikimedia Commons photo of any coach in that team's gear (CC BY / CC0), with attribution recorded in the registry; expect background removal and a less studio-like plate.
3. Recolour a generic coaching-polo plate to the team's primary/secondary colours and add the team wordmark/logo in the plate step (the plan's R5 fallback). This is the only option that does not need a real photo, but adding official marks is a trademark matter (§5).
4. Last resort: the fully generated outfit JR passed on (D6).

---

## 5. Open questions / what JR must decide

1. **Team-site licence.** The club photos are the best material but grant no reuse licence: personal, non-commercial viewing only, no copying/modification/redistribution. Acceptable for a private local simulation at JR's discretion; not publishable. Does JR accept local-only use of club photos, or restrict the donor library to Commons/CC0 so it can ever be shared? This is the single decision that changes the whole library.
2. **Committing generated WebPs.** `static/` is tracked and the repo already commits player WebPs, but coach WebPs are derivatives of NFL photos. Recommend keeping `static/coaches/` out of git (which would need a new `.gitignore` entry — a separate change) or committing only if JR is comfortable publishing the derivatives.
3. **Mod distribution.** The DDS portrait pack contains clothing, colours and logos taken from real photos. Is the pack for JR's machine only, or might it be shared? If shared, the licence problem moves from the donor library to the mod.
4. **Official logos.** Preserve team/NFL marks on the donor polo (best realism) or blur/replace them? Preserving is fine locally; distribution raises trademark issues.
5. **Attribution.** If Commons plates are used, CC BY/SA requires author + licence + source. The registry is git-ignored, so attribution would not travel with any shared output. Decide where attribution lives if anything is ever shared.
6. **Headsets.** Keep a headset plate (and accept that the headset is repainted away) or source headset-free studio plates only? Recommend headset-free for donors.
7. **Inpainting gap (implementation, not licence).** On this worktree's base (`411f79c`) the adapter sets `base_image_path` / `mask_image_path` (`headshots/local/adapter.py:151-152, 216-217`), but no local backend reads them: `headshots/local/backends.py` uses `ref_image` only as IP-Adapter conditioning and has no `AutoPipelineForInpainting` / init-image / mask path, and `generator.py` forwards only prompt/ref_image/config. `headshots/local/jersey_plate.py` exists for the two-stage fallback. U4 must wire the donor-plate inpainting (or use `jersey_plate.py`) before the coach donor masks have any effect. This does not change the donor-source recommendation, but it is a prerequisite for §2.7's generation step, and it contradicts the spec's §2.20 description of direct plate inpainting as already working.

---

## 6. Fetch log

| Fetch | Status |
|---|---|
| `server/api.py` reads (static mount) | file evidence, lines 171-190 and 3511-3514 |
| `mimetypes.guess_type("x.webp")` | `('image/webp', None)` |
| `starlette.staticfiles.StaticFiles.lookup_path` | source read; per-request path resolution |
| `https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2026/teams/6/coaches` | 200 JSON |
| `https://a.espncdn.com/i/headshots/nfl/coaches/full/{id}.png` | 404 (10 IDs) |
| `https://a.espncdn.com/i/headshots/nfl/players/full/{id}.png` | 200 for 17533/7157/14882/17762; 404 for 1832/17553/17578/2499338/4372439/5173599 |
| `https://www.espn.com/nfl/coach/_/id/17530` | 404 |
| `https://www.pro-football-reference.com/coaches/TomlMi0.htm` | 403 (curl and WebFetch) |
| `https://www.pro-football-reference.com/robots.txt` | 403 (Cloudflare challenge) |
| `https://commons.wikimedia.org/w/api.php` (Tomlin/Campbell/McVay) | 200 JSON with per-image licences |
| `https://www.detroitlions.com/team/coaches/` | 200 |
| Lions raw `.../upload/f_auto/lions/bxt18sbncaorsj4s6g9c.jpg` | 200 `image/jpeg`, 2560×1440 |
| Lions thumb `.../t_headshot_desktop/f_auto/lions/...jpg` | 200 `image/jpeg`, 400×400 |
| `https://www.patriots.com/team/coaches/` | 200 |
| Patriots raw `.../upload/f_png/patriots/baavub1gduiuir0qouys.png` | 200 `image/png`, 2000×1125 |
| Patriots thumb `.../t_person_squared_mobile/...png` | 200 `image/png`, 768×768 grayscale |
| `https://www.philadelphiaeagles.com/team/coaches/` | 200 |
| Eagles raw `.../private/f_auto/eagles/omolofohcqwqdisgdilu.jpg` | 200 `image/jpeg`, 1920×1080 |
| `https://www.chiefs.com/team/coaches-roster/` | 200 |
| Chiefs raw `.../upload/f_auto/chiefs/za8x1xxvglmkq5mzet3n.jpg` | 200 `image/jpeg`, 1920×1080 |
| `https://www.azcardinals.com/team/coaches/` | 200 |
| Cardinals raw `.../upload/f_auto/cardinals/oslbhmncmtr07ff5mnwu.jpg` | 200 `image/jpeg`, 1600×900 |
| Packers raw `.../upload/f_png/packers/a5d5lbdr9nxg44ektpkd.png` | 200 `image/png`, 2560×1440 RGBA |
| `https://www.nfl.com/legal/terms/` | 200; terms summarized in §2.1 |
| 32-team coach-page probe | images at `/team/coaches/` on 25 clubs; the other 7 (ATL, CIN, HOU, IND, MIA, TB, TEN) at `/team/coaches-roster/` → 32/32 coverage |
| Second pass on ATL/CIN/HOU/IND/MIA/TB/TEN `/team/coaches-roster/` | all 200, 190–310 person-image refs each |
