# Lighting design notes: how designers combine the elements

What working lighting designers say about combining colour, movement, pattern, level and effects, researched so the
show's light scenes can be refined against it (owner, 27 Nov: "creatively researching how other sophisticated lighting
designers combine these types of effects when consciously constructing shows ... that guidance can be used for our
lighting scene design refinement"). It goes with the house style at the top of `config/live/light_scenes.yaml`
(restraint: "a system that feels the music and expresses it well", not one that "flashes like a school disco").

**How to use it.** When writing or changing a scene, an effect, a transition or the director's choice among them,
check it against section (a), the table in (c) and the anti-patterns in (d). The numbers are other people's starting
points, not measurements of this room: tune by watching. Where the sources disagree (e), the room decides.

**Where the engine already follows it** (checked 27 Nov; the director's own choices are not judged here):
- Principle 7, wheels and heads in the dark: a lit head's gobo change, and a colour-wheel change of two filters or
  more, are made dark; far moves travel dark when the transition says so (docs/LIGHTS.md §5.5).
- Principle 8, gobos for the quiet, open beams for the loud: seven slow scenes hold one gobo each; every chorus scene
  has an open beam (a test holds the scene file to it). Not done: pale colours with gobos.
- Movement eases away and in, both axes together, within what a head can do (show_service/motion.py): the "bounce at
  the end" and "crash into place" faults in section 3 are designed out.

## The research

Researched on the web, 27 Nov 2026. Reading notes:
- **(snippet)**: I saw only the search-result excerpt; the page refused the fetch. Unverified.
- **(vendor)**: a retailer or maker blog. Concrete numbers, weak authority.
- **Synthesis**: my inference, not a sourced rule.
- Quotes were extracted by the fetch tool's summariser. Check the wording at the link before reusing a quote.

### (a) Design principles

1. Hold a base look; change it at section boundaries, not on beats. (several independent sources)
2. Keep something in reserve: the full rig appears once, at the song's high point. (several)
3. Two colours at once, three at most; one palette per song. (several)
4. Darkness and stillness are what make the bright, moving sections read. (several)
5. Every change needs a musical reason; a fixture feature is not one. (several)
6. Movers are still or slow most of the time; speed follows the section's energy. (several; the numbers are one vendor's)
7. Reposition and turn wheels in the dark; a visible move is a deliberate feature. (console manuals plus one LD)
8. Gobos are texture for slow, quiet sections; open beams for loud ones; pale colours with gobos. (one article, four designers)
9. Strobes are accents lasting seconds, never a beat counter. (several)
10. Effect lights (derby, laser, mirror ball) hit harder the less they appear; one role per fixture group. (one source each, plus synthesis)
11. Decide per phrase from song structure, anticipate the boundary, and let energy modulate inside the scene. (several: product docs, a vendor blog, two papers)
12. At a boundary change one or two attribute families (say colour and intensity), not all five. (synthesis; no source states it)

### (b) Detail per question

#### 1. Layering and restraint
- "Not every technological capability needs to be utilized at once"; programme "black space" ([Greil](https://chauvetprofessional.com/news/roland-greil-5-lessons-in-light/)). "Every lighting change must be motivated" ([Graham](https://chauvetprofessional.com/zen-and-the-art-of-entertainment-lighting-part-3/)).
- A minority of the rig makes the base look (example: 50 of 200 movers); the rest is "reserved for punctuating accents" ([Ticket Fairy](https://www.ticketfairy.com/blog/kinetic-festival-lighting-without-chaos-movement-cues-for-drops)).
- Reserve: keep "something left in your bag of tricks for the ending" ([Moody](https://www.prosoundweb.com/pro-production-approaches-to-cueing-the-music-with-concert-lighting/)); don't change every light at a song's start, or "there is nowhere to go" ([Pattak](https://chauvetprofessional.com/news/the-complexities-of-color/)); Dexter holds the movers until the fifth song ([Live Design](https://www.livedesignonline.com/think-before-you-light), snippet).
- Colours: "Don't use more than two colors on stage at the same time" ([USITT/Edwards](https://www.usitt.org/news/luke-edwards-breaks-rules-jacob-colliers-djesse-world-tour-chauvet-professional)); "two to three colors per song and sticking to them" ([Lose, PLSN](https://plsn.com/?p=279968)); never "more than three colors at a time" ([Quinn](https://chauvetprofessional.com/?p=41633)).
- "If you emphasize everything, you emphasize nothing" ([BSMG](https://bsmg.wiki/mapping/basic-lighting.html); a rhythm-game lighting guide, not stage practice).

#### 2. Song structure
- Four or five looks per song (opening, verse, chorus, solo, ending); "Translate the song into a primary color"; the high point "may not be the end"; cues are "musical punctuation not just flashing lights"; no repeated palette in adjacent songs ([Moody p.2](https://www.prosoundweb.com/pro-production-approaches-to-cueing-the-music-with-concert-lighting/2/)).
- A worked arc ([Sessler](https://www.prosoundweb.com/story-telling-production-lighting-to-match-the-arc-of-a-song/)): intro backlit, no front light for 16 bars; verse static ("Nothing is moving"); colour added into the chorus; chorus brighter; verse 2 holds the chorus look; bridge drops colour ("not a time for animation, color chases or anything else"); final chorus brightest; outro winds down.
- Calm look after a drop; a breakdown is dim backlight or a slow gobo with most movers off; a short blackout or stillness before one flash on the drop ([Ticket Fairy](https://www.ticketfairy.com/blog/kinetic-festival-lighting-without-chaos-movement-cues-for-drops)).
- One dominant colour, a secondary for accents; swap them at section changes ([BSMG](https://bsmg.wiki/mapping/intermediate-lighting.html)).
- Levels: intro pars 20%, movers parked; verse 50%, movers 30% and slow; chorus 80%; drop 100% white ([Shehds](https://shehds.com/blogs/news/how-to-sync-stage-lights-with-music-a-complete-guide), vendor).
- Synthesis: palette, position family and gobo stay fixed through a song; intensity, groups in use and movement speed escalate. Each chorus adds one layer.

#### 3. Movement
- The named fault: LDs "move the lights for the sake of movement" and "substitute movement for design" ([Persinger](https://chauvetprofessional.com/news/church-lighting-is-different-a-talk-with-greg-persinger/)); heads "moving way too much or way too fast for the song" ([Cadena](https://chauvetprofessional.com/news/richard-cadena-technology-takes-center-stage/)).
- One movement, rate-scaled: "slowly for a ballad, or fast for a big rocking chorus" ([Schiller](https://plsn.com/?p=266574)).
- Slow (5-20% speed) for about 70% of the show, fast only on drops ([Betopper](https://betopperdj.com/blogs/news/the-7-most-common-lighting-mistakes-djs-make-and-how-to-fix-them-like-a-pro-ld), vendor).
- Move in black: consoles "look ahead and preposition attributes of fixtures that are fading the dimmer in from zero", as soon as dark (Early) or just before relight (Late) ([grandMA3](https://help.malighting.com/grandMA3/2.4/HTML/cue_mib.html)). Eos turns the mark off per cue to allow a "live move" (ETC manual, snippet).
- "10 positions go a long way"; fade them over 3 s "so they don't crash into place" ([OSL programming](https://onstagelighting.co.uk/band-lighting/concert-lighting-programming/)).
- Ease: the fastest mode can "bounce at the end"; a 0.7 s move time cures it ([Schiller](https://plsn.com/?p=245611)). Ease-in/out by default is my synthesis.
- "A line of four moving spots is 'bigger' than if they were scattered" ([OSL design](https://onstagelighting.co.uk/lighting-design/lighting-design-moving-lights/)); "90, maybe even 95-percent of my show is symmetrical" ([Quinn](https://chauvetprofessional.com/?p=41633)).
- Gap: no source gave effect sizes in degrees or rates in bars.

#### 4. Gobos
From four designers in [Written in Gobos](https://chauvetprofessional.com/?p=25854) unless noted.
- "It's really about the tempo and mood of the song": static in-focus gobos "for ballads or mid-tempo songs", fast gobo effects "mostly for up tempo songs" (Caporale).
- For beams, a "nice, big open gobo that allows nice symmetrical beams to breathe" (Sasso).
- "Deep saturated colors don't tend to read well with gobos" (Kitchenman); keep the gobo lighter than the wash behind it (Howard).
- A gobo may appear "only once in an entire show" (Kitchenman).
- Without haze the aerial effect "is non-existant" ([OSL design](https://onstagelighting.co.uk/lighting-design/lighting-design-moving-lights/)).
- Lit or dark changes: no LD addressed it. Move-in-black covers every non-intensity attribute, so dark is the console default ([grandMA3](https://help.malighting.com/grandMA3/2.4/HTML/cue_mib.html)).

#### 5. Colour
- "6 - 8 colour palettes is plenty" ([OSL programming](https://onstagelighting.co.uk/band-lighting/concert-lighting-programming/)); a limited library "helps pull the show together" ([Pattak](https://chauvetprofessional.com/news/the-complexities-of-color/)).
- Complementary pairs give a "powerful mix of emotions"; analogous is "typically pleasing to the eye" ([Illuminated Integration](https://illuminated-integration.com/blog/stage-lighting-color-scheme/)). Stock pairs: blue/orange, yellow/purple, red/cyan, or white plus one colour ([Harman](https://pro.harman.com/insights/av/color-theory-for-concert-lighting-design), snippet).
- Mood: happy is yellow/orange, melancholy blue/lavender, fiery red ([Moody](https://www.prosoundweb.com/pro-production-approaches-to-cueing-the-music-with-concert-lighting/)).
- White is added "to make it more distinct and not a repeated look" ([Quinn](https://chauvetprofessional.com/?p=41633)).
- Faces: "I rarely have saturated front light" ([Pattak](https://chauvetprofessional.com/news/the-complexities-of-color/)); even front light on the performer, colour and movement behind ([Porcaro](https://www.chauvetdj.com/?p=67574)).
- Wheel colours sit "in the saturated part of the spectrum", so pastels are hard to match ([OSL design](https://onstagelighting.co.uk/lighting-design/lighting-design-moving-lights/)). Synthesis: choose the song's pair from the wheel, match the pars to it, snap the wheel at a boundary or turn it dark, and let the pars do the fades.

#### 6. Intensity and timing
- "Are the cues going to be bumps or slow fades? It does not matter", if each is punctuation ([Moody p.2](https://www.prosoundweb.com/pro-production-approaches-to-cueing-the-music-with-concert-lighting/2/)).
- "2-4 seconds for smooth transitions, 0.2 seconds for dramatic cuts" ([Shehds](https://shehds.com/blogs/news/how-to-sync-stage-lights-with-music-a-complete-guide), vendor). Gap: no source gives fades in beats or bars. One or two bars for a mood change and a snap on the downbeat for a chorus is my synthesis.
- LEDs "pop on too soon"; a slow-bottom curve spends 20% of the fade getting above 10% ([ETC](https://blog.etcconnect.com/2017/06/out-of-control-cue-transitions-leds)).
- Washes 20-40%, beams 30-50%, full only at peaks ([Betopper](https://betopperdj.com/blogs/news/the-7-most-common-lighting-mistakes-djs-make-and-how-to-fix-them-like-a-pro-ld), vendor).
- Strobes "as accents, not as a BPM counter" ([Lose, PLSN](https://plsn.com/?p=279968)); blinders "only for a few seconds at a time" ([TheatreFX](https://theatrefx.com/funfacts15.html), snippet); UK HSE recommends 4 flashes a second or fewer ([Epilepsy Society](https://epilepsysociety.org.uk/sites/default/files/2025-04/PhotosensitiveepilepsyApril2025.pdf), snippet).
- A total blackout before a key moment increases its impact ([BSMG](https://bsmg.wiki/mapping/intermediate-lighting.html)).

#### 7. Effect lights
- Mirror ball: "trying to use them less and less during the show, to make them even more impactful"; tight beam, no spill past the ball ([Warren](https://chauvetprofessional.com/news/ed-warren-mirror-ball-magic/)).
- Lasers: "I prefer to bring out the lights and laser at different points", more of them near the end ([Ho](https://chauvetprofessional.com/news/nick-ho-lighting-the-broad-canvas/)).
- Derby: no professional source. Nearest: an effect light left in sound-active mode all night causes visual fatigue ([Porcaro](https://www.chauvetdj.com/?p=67574)). Synthesis: it is the energy layer, on for choruses and drops only, in the song's colours.

#### 8. Small rigs
- Four movers are "four new light sources", not a spectacle; don't go "stuffing every cue with different moving spot wonders" ([OSL design](https://onstagelighting.co.uk/lighting-design/lighting-design-moving-lights/)).
- Three or four distinct looks, not 100 cues; vary dark/bright, warm/cool, still/moving, narrow/wide; three layers (wash, texture, energy), one role per fixture ([Betopper](https://betopperdj.com/blogs/news/the-7-most-common-lighting-mistakes-djs-make-and-how-to-fix-them-like-a-pro-ld), vendor).
- Between songs, a static neutral "punt look" ([Graham](https://chauvetprofessional.com/tech-talk-how-to-master-the-art-of-busking/)).

#### 9. Automated systems
- Sound-active "triggers on volume, not musical structure"; "Program on phrases (every 4-8 bars), not every beat" ([Shehds](https://shehds.com/blogs/news/how-to-sync-stage-lights-with-music-a-complete-guide), vendor).
- rekordbox links scenes to analysed phrases and sets a HIGH/MID/LOW mood from "tempo, rhythm, kick drum and sound density" ([rekordbox](https://rekordbox.com/en/support/faq/lighting-6/)).
- SoundSwitch: loops of 8 to 128 bars on the beat grid; "keep things simple"; matching the beat precisely "isn't always the best approach" ([SoundSwitch](https://inmusicsupport.freshdesk.com/en/support/solutions/articles/69000847100-autoloops-explained)).
- MaestroDMX: music energy modulates "Excitement" (faster, flashier, more sudden) and mover speed; a "Background" floor makes it less flashy; music-based blackouts are off by default ([MaestroDMX](https://maestrodmx.freshdesk.com/support/solutions/articles/153000070521-maestro-pattern-parameters)).
- A system mapping sections, five energy levels, and kick/snare to colour flashes rated level with hand-made shows in a small study (32 raters, 3 songs), but drew the comments "repetitive", "predictable" and "too explicit" ([arXiv 2602.08838](https://arxiv.org/html/2602.08838v1)).
- Emotion-to-colour rules scored 2.67/7 against 4.51 for human-made shows; the humans' clearest lead over the best model was on "surprise" ([arXiv 2506.01482](https://arxiv.org/html/2506.01482v2)).
- "Anticipation is 90% of my job" ([Spadaro](https://chauvetprofessional.com/news/improvising-light/)).
- Gap: nothing published on hysteresis. Synthesis: hold each scene at least one phrase.

### (c) Song section table for this rig

My synthesis from the sources above.

| Section | Leads | Supports | Rests |
|---|---|---|---|
| Between songs | Pars, static neutral; house lights part up | - | Everything else |
| Intro | Pars low in the song's primary colour | Movers dark, or one static texture | Movement, derby, strobe, laser |
| Verse | Pars; pale light on the singer | Movers static, optional gobo | Derby, strobe, laser, movement |
| Pre-chorus / build | Intensity ramp; one teaser (slow tilt rise) | Second colour arrives on pars | Strobe until the last bar; laser |
| Chorus | Movers: open beams, new symmetric position, slow sweep | Pars swap dominant and accent; derby from chorus 2 | Laser; strobe, except one accent hit |
| Verse 2 | As verse, one step brighter | Movers static, new position | Derby, strobe, laser |
| Bridge / breakdown | One colour, dim; static gobo; mirror ball if a ballad | House lights low | Movement, derby, strobe |
| Instrumental / solo | Movers: one visible live move | Pars hold | Strobe, laser |
| Drop / final chorus | Everything: faster movers, derby, laser, white accent | Strobe hits of 1-2 s | Nothing |
| Outro | Pars fade back to the intro look | Movers fade, then reposition dark | All effects |

### (d) "School disco" anti-patterns

| Anti-pattern | Professional alternative |
|---|---|
| Flash on every beat | Cue on section and phrase boundaries; beat accents only in peaks |
| Everything on, full, all night | Washes 20-40%; full only at peaks; a look made of few groups |
| Rainbow of saturated colours | Two per song from a small library; swap roles at the chorus |
| Movers always sweeping, fast | Static or slow about 70% of the time; speed follows energy |
| Wheels and heads changing while lit | Change in the dark; one deliberate live move per song |
| Strobe as a metronome | A hit of seconds at the drop, 4 Hz or under |
| Derby and laser left in sound mode | Scripted energy layer, choruses and drops only |
| Mirror ball always lit | One song or one section a night |
| Same look every song | New palette each song |
| No darkness | A deliberate dim or dark bar before the big moment |

### (e) Open disagreements

- **Two colours or three.** Edwards says two; Lose and Quinn allow three; Edwards used four at once when the concept called for it.
- **Gobos rotate or sit still.** Kitchenman: "it should almost always be rotating". Caporale: static in-focus gobos for ballads. This rig cannot rotate, so only Caporale's use applies.
- **Dance floor or performance.** [Porcaro](https://www.chauvetdj.com/?p=67574): a full floor wants "a darkened room with dynamic, moving effect lighting". The restraint sources are concert and church LDs. A karaoke room is both: a performer to light and a floor to energise.
- **Beat-mapped accents.** Moody and Lose reject flashing to every drum hit; the arXiv system does it and rated level with humans.
- **Mood to colour.** Moody gives a fixed mapping; the Skip-BART paper finds working engineers break it.
- **Symmetry.** Quinn is 90-95% symmetrical; Graham likes "odd angles coming in from at least two positions".
- **Blackout.** BSMG and Ticket Fairy use it for impact; Graham says "make sure the stage won't go black", and MaestroDMX ships with it off.

### Sources

Opened and read:
- Moody, Concert Lighting excerpt, ProSoundWeb: https://www.prosoundweb.com/pro-production-approaches-to-cueing-the-music-with-concert-lighting/ (also pages /2/ and /3/)
- Sessler, ProSoundWeb: https://www.prosoundweb.com/story-telling-production-lighting-to-match-the-arc-of-a-song/
- Lose, "Thank You for Using Restraint", PLSN: https://plsn.com/?p=279968
- Schiller, PLSN: https://plsn.com/?p=266574 , https://plsn.com/?p=245611 , https://plsn.com/?p=270346 , https://plsn.com/archives/february-2018/the-genre-series-concerts
- Chauvet Professional interviews:
  - Greil: https://chauvetprofessional.com/news/roland-greil-5-lessons-in-light/
  - Graham: https://chauvetprofessional.com/zen-and-the-art-of-entertainment-lighting-part-3/ , https://chauvetprofessional.com/tech-talk-how-to-master-the-art-of-busking/
  - Pattak: https://chauvetprofessional.com/news/the-complexities-of-color/
  - Larivée: https://chauvetprofessional.com/?p=41581
  - Ravitz: https://chauvetprofessional.com/news/5-lessons-in-light-jeff-ravitz/
  - Quinn: https://chauvetprofessional.com/?p=41633
  - Written in Gobos: https://chauvetprofessional.com/?p=25854
  - Persinger: https://chauvetprofessional.com/news/church-lighting-is-different-a-talk-with-greg-persinger/
  - Cadena: https://chauvetprofessional.com/news/richard-cadena-technology-takes-center-stage/
  - Spadaro: https://chauvetprofessional.com/news/improvising-light/
  - Warren: https://chauvetprofessional.com/news/ed-warren-mirror-ball-magic/
  - Ho: https://chauvetprofessional.com/news/nick-ho-lighting-the-broad-canvas/
- Edwards, USITT: https://www.usitt.org/news/luke-edwards-breaks-rules-jacob-colliers-djesse-world-tour-chauvet-professional
- Porcaro, Chauvet DJ: https://www.chauvetdj.com/?p=67574
- On Stage Lighting: https://onstagelighting.co.uk/lighting-design/lighting-design-moving-lights/ , https://onstagelighting.co.uk/band-lighting/concert-lighting-programming/ , https://onstagelighting.co.uk/console-programming/lighting-cue-timing/
- grandMA3 manual, Move In Black: https://help.malighting.com/grandMA3/2.4/HTML/cue_mib.html
- ETC: https://blog.etcconnect.com/2017/06/out-of-control-cue-transitions-leds , https://www.etcconnect.com/WebDocs/Controls/EosFamilyOnlineHelp/en/Content/12_Cues_and_Cue_Lists/06_Mark/01_AutoMark/About_AutoMark.htm
- Ticket Fairy: https://www.ticketfairy.com/blog/kinetic-festival-lighting-without-chaos-movement-cues-for-drops , https://www.ticketfairy.com/blog/lighting-for-bpm-festivals-timecode-vs-busking
- BSMG wiki: https://bsmg.wiki/mapping/basic-lighting.html , https://bsmg.wiki/mapping/intermediate-lighting.html
- Automated products: https://rekordbox.com/en/support/faq/lighting-6/ , https://inmusicsupport.freshdesk.com/en/support/solutions/articles/69000847100-autoloops-explained , https://maestrodmx.freshdesk.com/support/solutions/articles/153000070521-maestro-pattern-parameters , https://maestrodmx.freshdesk.com/support/solutions/articles/153000144494-core-features
- Papers: https://arxiv.org/html/2602.08838v1 , https://arxiv.org/html/2506.01482v2
- Vendor blogs: https://betopperdj.com/blogs/news/the-7-most-common-lighting-mistakes-djs-make-and-how-to-fix-them-like-a-pro-ld , https://shehds.com/blogs/news/how-to-sync-stage-lights-with-music-a-complete-guide , https://illuminated-integration.com/blog/stage-lighting-color-scheme/

Search snippet only (unverified):
- Dexter interview, Live Design: https://www.livedesignonline.com/think-before-you-light
- Harman colour theory: https://pro.harman.com/insights/av/color-theory-for-concert-lighting-design
- Epilepsy Society leaflet citing HSE's 4 Hz: https://epilepsysociety.org.uk/sites/default/files/2025-04/PhotosensitiveepilepsyApril2025.pdf
- TheatreFX on blinders: https://theatrefx.com/funfacts15.html
- ETC Eos manual on allowing a live move: https://www.manualsdir.com/manuals/559115/etc-eos-v13.html?page=186

Not found: movement effect sizes or rates as numbers; fade times in beats or bars; professional guidance on derby effects; anything published on hysteresis in music-driven lighting.
