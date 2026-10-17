# Tagging rubric: scenes, effects and transitions for the art director

You are re-tagging live-visual presets for a two-TV art installation in a living room / drawing room. A director picks
the scene on screen, the effects layered on it and the transition between scenes, by matching each preset's tags to
the music now playing. Today most tags are regex guesses from shader names and licence text ("ice" in "License" ->
icy; "ray" in "raymarch" -> bright, euphoric; energy a constant [0.2, 0.9]). Your tags replace them. Judge what a
viewer SEES and FEELS, never what the name or the shader's technique suggests.

## How to look
- Open the contact sheet with the Read tool (it is a PNG; you can see it). `variant_sheet`, when set, is this exact
  preset rendered with its own params: use it rather than `sheet` (which shows the plug-in's defaults). For imports
  the default sheet IS the preset's look.
- Sheets: scenes = frames over ~8 s of a synthetic 124 bpm pop song plus knob sweeps; effects = the effect over test
  stills (amount 0.8) plus sweeps; transitions = frames across p = 0..1 (GLSL transitions: often one smoke frame;
  read the shader source to judge the motion).
- Read the source file (`source_file`) enough to understand motion, pace and how the music drives it (the
  docstring and the main render / shader code). Params that differ from the defaults are in `params_vs_default`.
- `current_tags` are a reference only; for imports they are mostly wrong.
- Siblings (presets of the same plug-in) must be told apart where their looks differ (palette, pace, density).

## Fields (all required unless marked)
- `look` (string, <= 30 words): plain description of what is on screen: subject, colours, motion. No praise.
  Effects: what it does to the picture. Transitions: how A becomes B.
- `energy` [lo, hi]: the music MOMENT this suits. The music's energy is RELATIVE loudness within the current song
  (0 = near-silence / an intro or breakdown, 0.5 = a typical verse, 1 = the loudest drop or chorus peak); WHAT KIND
  of music is carried by `genre`, not energy. So most looks should span >= 0.45 and include the middle; only cut an
  end where the look would really jar there (a strobing glitch storm in a quiet intro; a still pastel wash at a
  drop). Examples: still candle-like glow [0.0, 0.6]; slow fluid ink [0.0, 0.75]; mid-paced geometric tunnel
  [0.25, 0.9]; frantic strobing glitch [0.6, 1.0]; neon city fly-through [0.3, 1.0].
- `moods` (1-3 words, most fitting first) from: calm, mellow, dreamy, warm, cool, bright, dark, euphoric, tense,
  playful, hypnotic, aggressive, cosmic, organic, geometric, retro, glitchy, elegant, liquid, fiery, icy.
  Definitions (use them strictly):
  calm = little motion, soft, restful. mellow = relaxed, soft-edged, unhurried warmth. dreamy = soft focus, floaty,
  hazy, surreal. warm = the colour temperature is red/orange/amber/gold (or literally cosy). cool = blue/teal/cyan/
  violet colour temperature. bright = the frame is mostly luminous / high-key. dark = mostly black or very low-key.
  euphoric = ecstatic: saturated, bursting, celebratory (rare: not every colourful thing). tense = uneasy, nervous,
  suspenseful. playful = whimsical, cartoon-like, bouncy, toy-like. hypnotic = repeating, looping, mesmerising
  rhythmic motion (tunnels, kaleidoscopes, rotating symmetric patterns) - NOT a fallback. aggressive = harsh, violent,
  attacking. cosmic = space, stars, nebulae, galaxies. organic = natural or living forms (plants, cells, water,
  smoke, flesh, hand-made). geometric = hard-edged shapes, grids, polyhedra, lines. retro = evokes a past era (CRT,
  8-bit, VHS, 80s neon, film). glitchy = digital corruption, datamosh, broken pixels. elegant = refined, graceful,
  restrained, tasteful. liquid = fluid, flowing, wet. fiery = flames, embers, lava, burning. icy = frost, ice,
  snow, frozen (literal, or a strongly cold white-blue crystalline look).
- `style`: gentle | lively | wild: the motion character. gentle = slow, smooth, soft cuts; lively = clearly moving,
  rhythmic, but not violent; wild = fast, violent, strobing, explosive, chaotic.
- `genre` (map, 0..1 for each of these 10 groups; every group must be given a number):
  ambient (ambient, downtempo, chill), classical (orchestral, chamber, solo piano, film score),
  jazz (jazz, blues), soul (soul, R&B, funk, gospel, disco), pop (pop, indie, singer-songwriter, folk, country),
  rock (rock, metal, punk), house (house, disco, trance, melodic electronic), techno (techno, trance, drum-and-bass,
  dubstep, hard electronic), hiphop (hip-hop), world (latin, afrobeat, reggae, world).
  The number = how well this look would sit on the TV while that kind of music plays, as a thoughtful VJ / art
  director would judge: 1 = a natural fit, 0.7 = good, 0.5 = acceptable, 0.3 = odd, 0 = would jar. Use the whole
  range; most looks should have 2-4 groups >= 0.7 and several <= 0.3. Calibration: a slow ink bloom suits ambient 1,
  classical 0.8, jazz 0.6, techno 0.2, rock 0.1. A hard strobing laser grid suits techno 1, house 0.8, rock 0.5,
  classical 0, ambient 0.1. A warm 70s neon sign alley suits soul 1, jazz 0.8, pop 0.8, house 0.7, classical 0.2.
  Optional `genre_words` {word: 0..1} for specific overrides inside a group (e.g. metal 0.9 while punk 0.5), only
  when it matters; words: pop, rock, indie, metal, punk, electronic, techno, house, trance, drum-and-bass, dubstep,
  ambient, downtempo, hip-hop, rnb, soul, funk, disco, jazz, blues, gospel, reggae, latin, afrobeat, folk, country,
  singer-songwriter, classical, piano, soundtrack, world.
- `valence` 0..1: 0 = sombre, menacing, sad; 0.5 = neutral; 1 = joyful, sunny, uplifting.
- `organic` 0..1: 0 = hard synthetic / digital / mathematical; 1 = natural, hand-made, acoustic-feeling.
- `density` 0..1: 0 = sparse, minimal, lots of empty space; 1 = dense, busy, detail everywhere.
- `harsh` 0..1 (effects and transitions only): 0 = subtle, the picture stays intact; 1 = it wrecks or hides the
  picture (heavy glitch, datamosh, strobe, full inversion).
- `quality` 0..1: how good this is as living-room show art on a 4K TV, for its own kind. 1 = gallery-grade, rich,
  polished; 0.7 = solid and attractive; 0.5 = fine; 0.3 = plain, crude or dated; 0.1 = barely art. For effects
  and transitions: how good the treatment looks, not how dramatic.
- `auto_ok` (bool): false ONLY when this is not art for an audience: a maths / colour-space plot, a debug or
  tutorial diagram, a near-empty test object, a flat screen, readable text / slogans / a real logo or brand, broken
  output (black, garbage, NaN speckle). It stays playable by hand; the director just won't pick it. Give
  `not_auto_why` (<= 12 words) when false.
- `confidence` 0..1: how sure you are (lower when you could not see the variant, or the sheet is ambiguous).

Be decisive and consistent. Do not pad moods. Do not give every look 0.5s.
