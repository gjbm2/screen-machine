# Test music: sources and licences

Real recorded music for show_service's **test audio** (`POST /show/api/v1/test {"source": "audio"}`): a track is
decoded and sent as the VBAN stream `test-audio`, so the whole show (listener, director, lights, screens) can be
exercised without playing anything in the room (show_service/README.md, S3.10). The files are not in git.
`tools/show/fetch_test_music.py` downloads them unchanged into `~/.cache/screen-machine/test-music/` (about 40 MB),
checks each against the sha256 pinned in its `SOURCES` list, and writes `index.json` there, which the test-audio
picker reads for labels.

Every licence was checked by hand on the page linked below on 16 Oct 2026. Only CC0, CC BY, CC BY-SA and public
domain were taken: nothing NonCommercial or NoDerivatives, and nothing whose licence was unclear (for example,
Internet Archive uploads marked "public domain" by someone other than the rights holder were skipped).

How they are used: played in this house, and, only while AI listening is on and the mode listens, as 24 s clips sent
to OpenAI for the listener's verdict (show_service/README.md S3.12; nothing is published). CC BY and CC BY-SA allow
both. If any of it is ever redistributed or shown publicly, give the credit line as written.

| Genre | Title | Artist | Licence | Source page | File |
|---|---|---|---|---|---|
| jazz | 1973 | Doxent Zsigmond (doxent) | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) | [ccMixter 41411](https://ccmixter.org/files/doxent/41411) | `jazz-doxent-1973.mp3` |
| techno | Storm | Mr.Dee | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) | [Internet Archive shoki005g](https://archive.org/details/shoki005g) (Shoki Recordings, Grey EP) | `techno-mr-dee-storm.ogg` |
| house | Memorias | Galería Disco | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) | [Internet Archive MNF015](https://archive.org/details/MNF015_Galeria_Disco--Memorias_EP) (Monofónicos Netlabel, Memorias EP) | `house-galeria-disco-memorias.ogg` |
| ambient | Decompress | Lee Rosevere | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [Internet Archive](https://archive.org/details/leerosevere_musicforpodcasts_5) (Music For Podcasts 5) | `ambient-lee-rosevere-decompress.ogg` |
| classical | Mendelssohn, Symphony No. 4 "Italian", IV. Saltarello: Presto | Musopen Symphony (Musopen, 2012) | [Public domain](https://creativecommons.org/publicdomain/mark/1.0/) (recording released by Musopen; composition PD) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Mendelssohn_-_Symphony_No._4_in_A_major,_Op._90_%27Italian%27_-_IV._Saltarello._Presto_(Musopen_Symphony).flac) | `classical-mendelssohn-saltarello.ogg` |
| rock | Airplane Mode | Josh Woodward | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) on the artist's own page (CC BY 3.0 as released on Jamendo) | **Artist:** [joshwoodward.com/song/AirplaneMode](https://www.joshwoodward.com/song/AirplaneMode) ("This work is licensed under a Creative Commons Attribution 4.0 License", read 16 Oct). Jamendo: [track 1057411](https://www.jamendo.com/track/1057411). File: [Internet Archive jamendo-124697](https://archive.org/details/jamendo-124697) (a mirror) | `rock-josh-woodward-airplane-mode.mp3` |
| pop | Play the Game | Lilly Wolf | CC BY 3.0 per the mirror ([licenseurl](https://archive.org/metadata/jamendo-130349), License.txt); **the artist's own Jamendo page is still to be read by a person** | Jamendo: [track 1093616](https://www.jamendo.com/track/1093616) (its licence is drawn in the browser; a script can't read it). File: [Internet Archive jamendo-130349](https://archive.org/details/jamendo-130349) (a mirror, album Play Loud) | `pop-lilly-wolf-play-the-game.mp3` |
| soul | PeaceLoveSoul | VJ_Memes (Jeris), vocals by KungFuFrijters (Joel Frijters) | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) | [ccMixter 35859](https://ccmixter.org/files/VJ_Memes/35859) (vocals: [ccMixter 35754](https://ccmixter.org/files/KungFuFrijters/35754), CC BY 3.0) | `soul-vj-memes-peacelovesoul.mp3` |
| soul (funk) | Light Me On Fire | Admiral Bob (admiralbob77), sax samples by stefsax | [CC BY 2.5](https://creativecommons.org/licenses/by/2.5/) | [ccMixter 30046](https://ccmixter.org/files/admiralbob77/30046) (samples: [ccMixter 7786](https://ccmixter.org/files/stefsax/7786), CC BY 2.5) | `soul-admiral-bob-light-me-on-fire.mp3` |

## Credit lines

- "1973" by Doxent Zsigmond (doxent), ccMixter, CC BY 3.0.
- "Storm" by Mr.Dee, from Grey EP [shoki005g], Shoki Recordings, CC BY 3.0.
- "Memorias" by Galería Disco, from Memorias EP [MNF015], Monofónicos Netlabel, CC BY-SA 3.0.
- "Decompress" by Lee Rosevere, from Music For Podcasts 5, CC BY 4.0.
- Mendelssohn, Symphony No. 4, IV (Musopen Symphony recording), public domain, via Wikimedia Commons.
- "Airplane Mode" by Josh Woodward, CC BY 4.0 (joshwoodward.com).
- "Play the Game" by Lilly Wolf, from Play Loud, CC BY 3.0.
- "PeaceLoveSoul" by VJ_Memes (Jeris) feat. KungFuFrijters (Joel Frijters), ccMixter, CC BY 3.0.
- "Light Me On Fire" by Admiral Bob (admiralbob77) feat. stefsax, ccMixter, CC BY 2.5.

## Notes

- **The two Jamendo tracks.** The files come from Internet Archive "jamendo-*" mirrors, third-party uploads, so the
  mirror's License.txt is not the rights holder's word. Josh Woodward's own site licenses "Airplane Mode" CC BY 4.0
  (read 16 Oct), which settles that one. For "Play the Game", open
  [jamendo.com/track/1093616](https://www.jamendo.com/track/1093616) in a browser and check the licence shown there
  is CC BY (not NC or ND); if it isn't, drop the track from `SOURCES` in tools/show/fetch_test_music.py and pick
  another pop track.

- The two ccMixter tracks marked "feat." are remixes built on other ccMixter uploads; their sources (linked above)
  carry the same or a more permissive licence, which ccMixter enforces.
- The classical file is Wikimedia Commons' own Ogg Vorbis transcode of the Musopen FLAC. If Commons ever re-encodes
  it, the checksum changes and the fetch refuses it until the page has been checked again (`--no-verify` takes it).
- The vocab genres (config/live/vocab.yaml) are all covered: pop, techno, house, jazz, ambient, classical, rock,
  soul.
- Add a track by appending to `SOURCES` in tools/show/fetch_test_music.py (with its licence page) and a row here.
  Any audio file dropped into the test-music folder also appears in the picker, labelled by its file name.
