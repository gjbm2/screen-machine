#!/usr/bin/env python3
"""Fetch and stage the "pop" slice of the MIDI library: pop songs, film / TV / musical / game themes and
modern & songbook standards, as solo-piano MIDI for the test piano.

Sources (all fetched here; licence strings are recorded, never used as a filter):
  PIAST-YT        Hugging Face Hayeonbang/PIAST (MIT): YouTube solo-piano covers transcribed to MIDI (notes,
                  velocity, sustain pedal). One file per YouTube id; titles were looked up by oEmbed during
                  research and are written into SELECTION below. Every file carries a second track "melody"
                  that repeats the piano's own top notes, so it is dropped here (else each melody note sounds
                  twice). Nothing else is changed.
  Pianist8        Zenodo record 5089279 (= github joann8512/Pianist8 @ ab9f541): composers playing their own
                  pieces (Einaudi, Yiruma, Hisaishi, Sakamoto) and Richard Clayderman, transcribed. Unchanged.
  ADL Piano MIDI  github lucasnfe/adl-piano-midi: web-scraped two-hand arrangements, used only to fill gaps
                  (take "arrangement"). Only a malformed key-signature meta event is removed where present, so
                  the file parses; nothing else is changed.

Output (the assembler step cleans the files and builds the catalogue):
  ~/.local/share/screen-machine/midi-library/_staging/pop/files/*.mid
  ~/.local/share/screen-machine/midi-library/_staging/pop/manifest.json
  ~/.local/share/screen-machine/midi-library/_staging/pop/SOURCES.md
Downloads are cached in ~/.cache/screen-machine/midi-library/pop/ (about 45 MB), so re-runs are cheap,
resumable and idempotent (a staged file is rewritten only when its bytes would change).

Usage:  fetch_pop.py [--out DIR] [--cache DIR] [--list] [--only SUBSTRING]
Needs mido (e.g. ~/.venvs/sm-musiclib/bin/python). No GPU, no transcription.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import mido

KEY = "pop"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")

PIAST_URL = "https://huggingface.co/datasets/Hayeonbang/PIAST/resolve/main/piast_yt/midi/{id}.mid"
PIAST_HOME = "https://huggingface.co/datasets/Hayeonbang/PIAST"
PIAST_LICENCE = "MIT"
P8_URLS = [  # same content (411 identical MIDI files); Zenodo first, GitHub snapshot of the same commit second
    "https://zenodo.org/api/records/5089279/files/joann8512/Pianist8-v1.0.0.zip/content",
    "https://github.com/joann8512/Pianist8/archive/ab9f54110cf459559acaf71c59c3aff716ef882a.zip",
]
P8_HOME = "https://zenodo.org/records/5089279"
P8_LICENCE = "other-open (Zenodo record 5089279)"
ADL_URL = "https://raw.githubusercontent.com/lucasnfe/adl-piano-midi/master/midi/adl-piano-midi.zip"
ADL_HOME = "https://github.com/lucasnfe/adl-piano-midi"
ADL_LICENCE = None  # none stated in the repository

MIN_GAP = {"huggingface.co": 0.6, "zenodo.org": 1.5, "github.com": 1.0, "raw.githubusercontent.com": 1.0}

# --------------------------------------------------------------------------------------------------------
# SELECTION. One line per title (one best take each), pipe-separated:
#   src | ref | genre | title | composer | year | rank | performer | artist (original recording) | tags
# src: P = PIAST-YT YouTube id, P8 = Pianist8 path inside the zip, A = ADL path inside the zip.
# rank: 0..1, how well known / standard the piece is (default sort). Composer = songwriter(s).
# PIAST picks prefer faithful covers by strong players (Rousseau, Patrik Pietschmann, Pianella, released
# piano albums on "- Topic" channels, the composer's own upload) over tutorials and reharmonisations;
# SangHeart Play is used for 60s-80s songs nobody else covers; jazz arrangements only when nothing else.
# Einaudi is filed as classical (Contemporary); Yiruma, Clayderman and Sakamoto's songs as pop.
# ADL files with a single flat velocity were rejected (e.g. Divenire, Speak Softly Love, Candle in the Wind).
# --------------------------------------------------------------------------------------------------------
SELECTION = r"""
# ---- pop: PIAST-YT ----
P | Ni3Aa5AwX1Q | pop | Imagine | John Lennon | 1971 | .97 | Rousseau | John Lennon | ballad,70s
P | jtFDklau8o0 | pop | Piano Man | Billy Joel | 1973 | .92 | Rousseau | Billy Joel | piano-song,70s
P | Y4IS549HX5Q | pop | Clocks | Coldplay | 2002 | .85 | Rousseau | Coldplay | piano-riff,2000s
P | l-Z-B8TJMuQ | pop | Hallelujah | Leonard Cohen | 1984 | .95 | Rousseau | Leonard Cohen | ballad,80s
P | b3E6E6hYSSI | pop | All of Me | John Legend & Toby Gad | 2013 | .85 | Rousseau | John Legend | ballad,2010s
P | DGjKDQc8LMM | pop | See You Again | Charlie Puth & Wiz Khalifa | 2015 | .7 | Rousseau | Wiz Khalifa ft. Charlie Puth | ballad,film,2010s
P | LSwXh1Y5thY | pop | Faded | Alan Walker | 2015 | .62 | Rousseau | Alan Walker | electronic,2010s
P | XhQq0aXhEos | pop | lovely | Billie Eilish & Finneas O'Connell | 2018 | .6 | Rousseau | Billie Eilish & Khalid | ballad,2010s
P | CiJjiI2SjQo | pop | Viva la Vida | Coldplay | 2008 | .82 | Pianella Piano | Coldplay | 2000s
P | yr2cLuKJ2a8 | pop | Perfect | Ed Sheeran | 2017 | .8 | Pianella Piano | Ed Sheeran | ballad,wedding,2010s
P | eT5muWj8rKI | pop | Someone You Loved | Lewis Capaldi | 2018 | .72 | Pianella Piano | Lewis Capaldi | ballad,2010s
P | 4pgUj4P7fWk | pop | Let Her Go | Passenger | 2012 | .7 | Pianella Piano | Passenger | 2010s
P | 8r7CUCnNJmQ | pop | Counting Stars | Ryan Tedder | 2013 | .65 | Pianella Piano | OneRepublic | 2010s
P | noNzNyRl2mM | pop | 7 Years | Lukas Graham | 2015 | .6 | Pianella Piano | Lukas Graham | 2010s
P | nWis_uYXANk | pop | Blinding Lights | The Weeknd & Max Martin | 2019 | .72 | Pianella Piano | The Weeknd | synth-pop,2010s
P | tHg0fqzjus0 | pop | Love Story | Taylor Swift | 2008 | .65 | Pianella Piano | Taylor Swift | 2000s
P | aqVscO_FPZY | pop | Believer | Imagine Dragons | 2017 | .6 | Pianella Piano | Imagine Dragons | 2010s
P | xe4Yp9u7nkc | pop | Someone Like You | Adele & Dan Wilson | 2011 | .9 | Henry Smith | Adele | ballad,2010s
P | Av-SOyOSPUo | pop | Easy On Me | Adele & Greg Kurstin | 2021 | .65 | Flying Fingers | Adele | ballad,2020s
P | kqppfXXb8hc | pop | Set Fire to the Rain | Adele & Fraser T. Smith | 2011 | .68 | Flying Fingers | Adele | 2010s
P | THvG_xUyIeI | pop | As It Was | Harry Styles, Kid Harpoon & Tyler Johnson | 2022 | .55 | Flying Fingers | Harry Styles | 2020s
P | tbW0P2z3C8k | pop | Take On Me | a-ha | 1985 | .78 | Henry Smith | a-ha | synth-pop,80s
P | DmKC7zMYT8U | pop | I Wanna Dance with Somebody (Who Loves Me) | George Merrill & Shannon Rubicam | 1987 | .72 | Henry Smith | Whitney Houston | dance,80s
P | bvqc8TeY2Do | pop | Fix You | Coldplay | 2005 | .8 | David Schultz | Coldplay | ballad,2000s
P | N1E21xcTM_M | pop | November Rain | Axl Rose | 1991 | .72 | Daniele Leoni | Guns N' Roses | rock,power-ballad,90s
P | -35Cbdcyfb4 | pop | Tears in Heaven | Eric Clapton & Will Jennings | 1992 | .8 | Luca Ottani | Eric Clapton | ballad,90s
P | qJRSEUWKEn0 | pop | Bohemian Rhapsody | Freddie Mercury | 1975 | .97 | AtinPiano | Queen | rock,queen,70s
P | JxB0NjOHYSc | pop | Love of My Life | Freddie Mercury | 1975 | .7 | Jazzpian.S | Queen | ballad,queen,70s
P | DxeRXRZVVuw | pop | We Are the Champions | Freddie Mercury | 1977 | .8 | Jichan Park | Queen | rock,queen,70s
P | 5ChaQ_3FyOo | pop | The Scientist | Coldplay | 2002 | .8 | GoGoPi | Coldplay | ballad,2000s
P | r_dSbqHcez4 | pop | Yellow | Coldplay | 2000 | .7 | Andrea Carri | Coldplay | 2000s
P | bJ9aRLSO32A | pop | Bridge Over Troubled Water | Paul Simon | 1970 | .9 | SangHeart Play | Simon & Garfunkel | ballad,folk,70s
P | -SWahGvzV3o | pop | Just the Way You Are | Billy Joel | 1977 | .75 | SangHeart Play | Billy Joel | ballad,70s
P | 4D21-zSkCRE | pop | Honesty | Billy Joel | 1978 | .6 | Jacob Koller | Billy Joel | ballad,jazz-arrangement,70s
P | 0kKxfmc9byQ | pop | New York State of Mind | Billy Joel | 1976 | .65 | Jacob Koller | Billy Joel | jazz-arrangement,70s
P | r3xeCZhpixU | pop | Goodbye Yellow Brick Road | Elton John & Bernie Taupin | 1973 | .75 | SangHeart Play | Elton John | easy-arrangement,70s
P | pHKssqFwCUA | pop | Sorry Seems to Be the Hardest Word | Elton John & Bernie Taupin | 1976 | .62 | SangHeart Play | Elton John | ballad,easy-arrangement,70s
P | TcYH7ymKC3c | pop | The Winner Takes It All | Benny Andersson & Björn Ulvaeus | 1980 | .8 | SangHeart Play | ABBA | ballad,abba,80s
P | unpunAovZ7w | pop | Isn't She Lovely | Stevie Wonder | 1976 | .75 | Jacob Koller | Stevie Wonder | soul,jazz-arrangement,70s
P | HEa4-NzLAxA | pop | I Just Called to Say I Love You | Stevie Wonder | 1984 | .7 | SangHeart Play | Stevie Wonder | 80s
P | qm_EFNvPxQY | pop | Top of the World | Richard Carpenter & John Bettis | 1972 | .65 | SangHeart Play | Carpenters | 70s
P | 69CIqGeQ4DE | pop | Desperado | Don Henley & Glenn Frey | 1973 | .65 | Hiroshi Nakamura | Eagles | ballad,jazz-arrangement,70s
P | oFCy3vEIAQk | pop | Can't Help Falling in Love | Hugo Peretti, Luigi Creatore & George David Weiss | 1961 | .9 | Welder Dias | Elvis Presley | ballad,60s
P | Cc3y8oOEEsM | pop | All I Want for Christmas Is You | Mariah Carey & Walter Afanasieff | 1994 | .85 | Jichan Park | Mariah Carey | christmas,90s
P | tMkNg_d_bh8 | pop | Happy Xmas (War Is Over) | John Lennon & Yoko Ono | 1971 | .7 | Music Fellows | John Lennon | christmas,70s
P | SsUaSTN-ZHA | pop | Stand by Me | Ben E. King, Jerry Leiber & Mike Stoller | 1961 | .85 | SangHeart Play | Ben E. King | soul,60s
P | 19_2VnmDxhY | pop | How Deep Is Your Love | Barry, Robin & Maurice Gibb | 1977 | .75 | SangHeart Play | Bee Gees | ballad,70s
P | gc7c7TcpdJE | pop | Englishman in New York | Sting | 1987 | .65 | SangHeart Play | Sting | 80s
P | maeM99EHXD0 | pop | Vincent (Starry, Starry Night) | Don McLean | 1971 | .7 | SangHeart Play | Don McLean | ballad,folk,70s
P | BWrjjOHOdc0 | pop | The Rose | Amanda McBroom | 1979 | .65 | SangHeart Play | Bette Midler | ballad,film,70s
P | 3exdiPRtiQI | pop | Killing Me Softly with His Song | Charles Fox & Norman Gimbel | 1972 | .75 | Piano안현진 | Roberta Flack | soul,70s
P | XIBE3ZtC2go | pop | Annie's Song | John Denver | 1974 | .6 | SangHeart Play | John Denver | ballad,folk,70s
P | ZvI-463fYzE | pop | (Everything I Do) I Do It for You | Bryan Adams, Michael Kamen & Robert John "Mutt" Lange | 1991 | .7 | SangHeart Play | Bryan Adams | ballad,film,90s
P | 5LrCDnfHpik | pop | You Raise Me Up | Rolf Løvland & Brendan Graham | 2002 | .7 | SangHeart Play | Secret Garden / Josh Groban | ballad,2000s
P | 9aPY0jc2jKw | pop | All by Myself | Eric Carmen | 1975 | .65 | SangHeart Play | Eric Carmen / Celine Dion | ballad,70s
P | L8XBcVi0iNU | pop | Endless Love | Lionel Richie | 1981 | .65 | SangHeart Play | Diana Ross & Lionel Richie | ballad,80s
P | pKCXw88fLGY | pop | Say You, Say Me | Lionel Richie | 1985 | .6 | SangHeart Play | Lionel Richie | ballad,80s
P | Ud7A9Ljkp_o | pop | She | Charles Aznavour & Herbert Kretzmer | 1974 | .62 | SangHeart Play | Charles Aznavour / Elvis Costello | ballad,film,notting-hill
P | 69na0K7Jytc | pop | Take Me to Church | Hozier | 2013 | .6 | PianoX | Hozier | 2010s
P | ZuLbiwhehTo | pop | Another Love | Tom Odell | 2012 | .6 | PianoX | Tom Odell | ballad,2010s
P | W5qMQr7P9nA | pop | Shallow | Lady Gaga, Mark Ronson, Andrew Wyatt & Anthony Rossomando | 2018 | .7 | Steve Banks | Lady Gaga & Bradley Cooper | ballad,film,2010s
P | nBO_PWFNrPg | pop | Shape of You | Ed Sheeran | 2017 | .7 | Joe Travini | Ed Sheeran | 2010s
P | 3dbHuxGVwOw | pop | Bad Guy | Billie Eilish & Finneas O'Connell | 2019 | .6 | The Theorist | Billie Eilish | 2010s
P | 4yKeIhxo-ws | pop | In the End | Linkin Park | 2000 | .6 | Flavin Pianista | Linkin Park | rock,2000s
P | ah2oFP8cgOA | pop | Just the Two of Us | Bill Withers, William Salter & Ralph MacDonald | 1980 | .65 | TranscriptionsByPaul | Grover Washington Jr. & Bill Withers | soul,80s
P | k749mOIjMaE | pop | Hey Jude | Paul McCartney | 1968 | .95 | Piano안현진 | The Beatles | beatles,60s
P | XUzt9W3FVMY | pop | Michelle | Paul McCartney | 1965 | .75 | Sabin Todorov | The Beatles | beatles,ballad,60s
P | LyZIyDVehzU | pop | Yesterday | Paul McCartney | 1965 | .97 | Daeyoun Kim | The Beatles | beatles,ballad,jazz-arrangement,60s
P | xchESHG97X0 | pop | Con te partirò (Time to Say Goodbye) | Francesco Sartori & Lucio Quarantotto | 1995 | .72 | Daniele Leoni | Andrea Bocelli | ballad,classical-crossover,90s
# ---- pop: Pianist8 (composer recordings and Richard Clayderman) ----
P8 | midi/Yiruma/Yiruma_River_Flows_in_You.mid | pop | River Flows in You | Yiruma | 2001 | .9 | Yiruma | Yiruma | neoclassical,modern-piano
P8 | midi/Yiruma/Yiruma_Kiss_the_Rain.mid | pop | Kiss the Rain | Yiruma | 2003 | .75 | Yiruma | Yiruma | neoclassical,modern-piano
P8 | midi/Yiruma/Yiruma_Maybe.mid | pop | Maybe | Yiruma | 2001 | .55 | Yiruma | Yiruma | neoclassical,modern-piano
P8 | midi/Ryuichi/Ryuichi_Energy_Flow.mid | pop | Energy Flow | Ryuichi Sakamoto | 1999 | .6 | Ryuichi Sakamoto | Ryuichi Sakamoto | neoclassical,modern-piano
P8 | midi/Clayderman/Clayderman_Ballade_Pour_Adeline.mid | pop | Ballade pour Adeline | Paul de Senneville | 1977 | .8 | Richard Clayderman | Richard Clayderman | easy-listening,70s
P8 | midi/Clayderman/Clayderman_Mariage_D_Amour.mid | pop | Mariage d'amour | Paul de Senneville | 1978 | .7 | Richard Clayderman | Richard Clayderman | easy-listening,70s
P8 | midi/Clayderman/Clayderman_The_Sound_of_Silence.mid | pop | The Sound of Silence | Paul Simon | 1964 | .9 | Richard Clayderman | Simon & Garfunkel | folk,60s
P8 | midi/Clayderman/Clayderman_Yesterday_Once_More.mid | pop | Yesterday Once More | Richard Carpenter & John Bettis | 1973 | .75 | Richard Clayderman | Carpenters | 70s
P8 | midi/Clayderman/Clayderman_I_Have_A_Dream.mid | pop | I Have a Dream | Benny Andersson & Björn Ulvaeus | 1979 | .65 | Richard Clayderman | ABBA | abba,70s
P8 | midi/Clayderman/Clayderman_Hello.mid | pop | Hello | Lionel Richie | 1983 | .75 | Richard Clayderman | Lionel Richie | ballad,80s
P8 | midi/Clayderman/Clayderman_Woman_in_Love.mid | pop | Woman in Love | Barry Gibb & Robin Gibb | 1980 | .65 | Richard Clayderman | Barbra Streisand | ballad,80s
P8 | midi/Clayderman/Clayderman_Nothing’s_Gonna_Change_My_Love_For_You.mid | pop | Nothing's Gonna Change My Love for You | Michael Masser & Gerry Goffin | 1984 | .6 | Richard Clayderman | George Benson | ballad,80s
P8 | midi/Clayderman/Clayderman_Feelings.mid | pop | Feelings | Morris Albert | 1974 | .6 | Richard Clayderman | Morris Albert | ballad,70s
P8 | midi/Clayderman/Clayderman_Will_You_Still_Love_Me_Tomorrow.mid | pop | Will You Love Me Tomorrow | Gerry Goffin & Carole King | 1960 | .6 | Richard Clayderman | The Shirelles | 60s
P8 | midi/Clayderman/Clayderman_Music_Box_Dancer.mid | pop | Music Box Dancer | Frank Mills | 1974 | .55 | Richard Clayderman | Frank Mills | easy-listening,70s
# ---- pop: ADL two-hand arrangements (gap fillers) ----
A | adl-piano-midi/Rock/Psychedelic Rock/Beatles/Let it Be.mid | pop | Let It Be | Paul McCartney | 1970 | .97 | | The Beatles | beatles,ballad,60s
A | adl-piano-midi/Rock/Psychedelic Rock/Beatles/Eleanor Rigby.mid | pop | Eleanor Rigby | Paul McCartney | 1966 | .8 | | The Beatles | beatles,60s
A | adl-piano-midi/Rock/Psychedelic Rock/Beatles/The Long and Winding Road.mid | pop | The Long and Winding Road | Paul McCartney | 1970 | .75 | | The Beatles | beatles,ballad,70s
A | adl-piano-midi/Rock/Psychedelic Rock/The Beatles/Something.mid | pop | Something | George Harrison | 1969 | .75 | | The Beatles | beatles,ballad,60s
A | adl-piano-midi/Rock/Psychedelic Rock/The Beatles/Penny Lane.mid | pop | Penny Lane | Paul McCartney | 1967 | .7 | | The Beatles | beatles,60s
A | adl-piano-midi/Rock/Psychedelic Rock/The Beatles/Blackbird.mid | pop | Blackbird | Paul McCartney | 1968 | .72 | | The Beatles | beatles,60s
A | adl-piano-midi/Rock/Psychedelic Rock/Beatles/Here There and Everywhere.mid | pop | Here, There and Everywhere | Paul McCartney | 1966 | .6 | | The Beatles | beatles,ballad,60s
A | adl-piano-midi/Rock/Psychedelic Rock/Beatles/And I Love Her.mid | pop | And I Love Her | Paul McCartney | 1964 | .62 | | The Beatles | beatles,ballad,60s
A | adl-piano-midi/Unknown/Unknown/Elton John  Ronan Keating/Your Song.mid | pop | Your Song | Elton John & Bernie Taupin | 1970 | .9 | | Elton John | ballad,70s
A | adl-piano-midi/World/Swedish Pop/Abba/Dancing Queen.mid | pop | Dancing Queen | Benny Andersson, Björn Ulvaeus & Stig Anderson | 1976 | .9 | | ABBA | abba,disco,70s
A | adl-piano-midi/World/Swedish Pop/Abba/Fernando.mid | pop | Fernando | Benny Andersson, Björn Ulvaeus & Stig Anderson | 1976 | .7 | | ABBA | abba,70s
A | adl-piano-midi/Rock/Soft Rock/Lionel Richie/Easy.mid | pop | Easy | Lionel Richie | 1977 | .6 | | Commodores | ballad,70s
A | adl-piano-midi/Rock/Soft Rock/Sting/Every Breath You Take.mid | pop | Every Breath You Take | Sting | 1983 | .85 | | The Police | 80s
A | adl-piano-midi/Rock/Soft Rock/Phil Collins/Another Day In Paradise.mid | pop | Another Day in Paradise | Phil Collins | 1989 | .7 | | Phil Collins | 80s
A | adl-piano-midi/Rock/Soft Rock/Phil Collins/In the Air Tonight.mid | pop | In the Air Tonight | Phil Collins | 1981 | .75 | | Phil Collins | 80s
A | adl-piano-midi/Rock/Dance Rock/George Michael/Careless Whisper.mid | pop | Careless Whisper | George Michael & Andrew Ridgeley | 1984 | .85 | | George Michael | ballad,80s
A | adl-piano-midi/Rock/Dance Rock/George Michael/Last Christmas.mid | pop | Last Christmas | George Michael | 1984 | .85 | | Wham! | christmas,80s
A | adl-piano-midi/Rock/Piano Rock/Vanessa Carlton/A Thousand Miles.mid | pop | A Thousand Miles | Vanessa Carlton | 2001 | .65 | | Vanessa Carlton | piano-song,2000s
A | adl-piano-midi/Soul/R&B/Whitney Houston/I will Always Love You.mid | pop | I Will Always Love You | Dolly Parton | 1973 | .9 | | Whitney Houston | ballad,film,90s
# ---- classical (Contemporary): Einaudi ----
P8 | midi/Einaudi/Einaudi_Nuvole_bianche.mid | classical | Nuvole bianche | Ludovico Einaudi | 2004 | .88 | Ludovico Einaudi | Ludovico Einaudi | neoclassical,minimalist,modern-piano
P8 | midi/Einaudi/Einaudi_Una_mattina.mid | classical | Una mattina | Ludovico Einaudi | 2004 | .72 | Ludovico Einaudi | Ludovico Einaudi | neoclassical,minimalist,modern-piano,film
P8 | midi/Einaudi/Einaudi_I_giorni.mid | classical | I giorni | Ludovico Einaudi | 2001 | .7 | Ludovico Einaudi | Ludovico Einaudi | neoclassical,minimalist,modern-piano
P8 | midi/Einaudi/Einaudi_Le_onde.mid | classical | Le onde | Ludovico Einaudi | 1996 | .7 | Ludovico Einaudi | Ludovico Einaudi | neoclassical,minimalist,modern-piano
P | wxR3DkF9I_U | classical | Experience | Ludovico Einaudi | 2013 | .72 | The Flaming Piano | Ludovico Einaudi | neoclassical,minimalist,modern-piano
# ---- film / TV / musicals / games: PIAST-YT ----
P | JT1XJnVmABo | film | My Heart Will Go On (Titanic) | James Horner & Will Jennings | 1997 | .92 | Patrik Pietschmann | Celine Dion | ballad,titanic
P | 4y33h81phKU | film | Interstellar: Main Theme (First Step) | Hans Zimmer | 2014 | .85 | Patrik Pietschmann | Hans Zimmer | soundtrack
P | b8HVQtIoBYU | film | Time (Inception) | Hans Zimmer | 2010 | .8 | Patrik Pietschmann | Hans Zimmer | soundtrack
P | 2aBtDz87UK0 | film | He's a Pirate (Pirates of the Caribbean) | Klaus Badelt & Hans Zimmer | 2003 | .85 | Patrik Pietschmann | Klaus Badelt | soundtrack
P | pZAZ2x-KNf0 | film | Star Wars: Main Title | John Williams | 1977 | .95 | Patrik Pietschmann | John Williams | soundtrack,star-wars
P | 0v2N1ROvEI0 | film | The Imperial March (Star Wars) | John Williams | 1980 | .85 | Patrik Pietschmann | John Williams | soundtrack,star-wars
P | Iq6g_4AwUWs | film | The Force Theme (Star Wars) | John Williams | 1977 | .65 | Patrik Pietschmann | John Williams | soundtrack,star-wars
P | ZdyiXIlKe_Y | film | Across the Stars (Star Wars) | John Williams | 2002 | .6 | Patrik Pietschmann | John Williams | soundtrack,star-wars
P | jTPXwbDtIpA | film | Hedwig's Theme (Harry Potter) | John Williams | 2001 | .9 | Patrik Pietschmann | John Williams | soundtrack,harry-potter
P | sFSWlfZ4CXM | film | Theme from Schindler's List | John Williams | 1993 | .85 | Patrik Pietschmann | John Williams | soundtrack
P | eAn03ILGljM | film | Raiders March & Marion's Theme (Indiana Jones) | John Williams | 1981 | .78 | Matt Craig | John Williams | soundtrack
P | yuboWIBPDvk | film | The Lord of the Rings: Main Theme | Howard Shore | 2001 | .8 | Patrik Pietschmann | Howard Shore | soundtrack
P | yZGBxiNPu3k | film | Game of Thrones: Main Title | Ramin Djawadi | 2011 | .85 | Patrik Pietschmann | Ramin Djawadi | tv
P | iqOujiV-rOQ | film | Let It Go (Frozen) | Kristen Anderson-Lopez & Robert Lopez | 2013 | .85 | Patrik Pietschmann | Idina Menzel | disney,musical
P | oAfU-b2ARXw | film | Love Theme from The Godfather (Speak Softly, Love) | Nino Rota | 1972 | .85 | Jacob Koller | Nino Rota | soundtrack,godfather,tango,jazz-arrangement
P | XfA2KXodrOE | film | No Time to Die | Billie Eilish & Finneas O'Connell | 2020 | .6 | Patrik Pietschmann | Billie Eilish | bond,ballad
P | C7Yjy5vASas | film | Lux Aeterna (Requiem for a Dream) | Clint Mansell | 2000 | .7 | Patrik Pietschmann | Clint Mansell | soundtrack
P | 1QPRzjaq584 | film | Married Life (Up) | Michael Giacchino | 2009 | .68 | Toms Mucenieks | Michael Giacchino | pixar,waltz
P | znfYwABeSZ0 | film | Comptine d'un autre été, l'après-midi (Amélie) | Yann Tiersen | 2001 | .9 | Yann Tiersen | Yann Tiersen | amelie,modern-piano
P | htH5qPqhDro | film | La valse d'Amélie | Yann Tiersen | 2001 | .6 | Dulpe Damel | Yann Tiersen | amelie,waltz
P | 5q6aaauSNgw | film | La vita è bella (Life Is Beautiful) | Nicola Piovani | 1997 | .6 | Dulpe Damel | Nicola Piovani | soundtrack
P | dVXVQmFvOD4 | film | Cinema Paradiso: Love Theme | Andrea Morricone & Ennio Morricone | 1988 | .8 | KenBan | Ennio Morricone | soundtrack,morricone
P | PR_v-yd5vDs | film | Once Upon a Time in the West | Ennio Morricone | 1968 | .75 | Daniele Leoni | Ennio Morricone | soundtrack,morricone,western
P | rWppq-V3WV8 | film | Mia & Sebastian's Theme (La La Land) | Justin Hurwitz | 2016 | .7 | PianiCast | Justin Hurwitz | soundtrack,la-la-land
P | nJSdhvI_ys0 | film | City of Stars (La La Land) | Justin Hurwitz | 2016 | .75 | THIS IS PIANO | Ryan Gosling & Emma Stone | musical,la-la-land
P | yTXLcLdXzk8 | film | A Million Dreams (The Greatest Showman) | Benj Pasek & Justin Paul | 2017 | .65 | PianiCast | Hugh Jackman | musical
P | bDBTawo46mc | film | This Is Me (The Greatest Showman) | Benj Pasek & Justin Paul | 2017 | .6 | GoGoPi | Keala Settle | musical
P | 6AWHKPUMy8k | film | Beauty and the Beast | Alan Menken | 1991 | .85 | kno Piano Music | Angela Lansbury | disney,musical
P | g6o8hI2aIeY | film | A Whole New World (Aladdin) | Alan Menken | 1992 | .85 | kno Piano Music | Peabo Bryson & Regina Belle | disney,musical
P | n0q-Q74F6pc | film | Colors of the Wind (Pocahontas) | Alan Menken | 1995 | .7 | kno Piano Music | Vanessa Williams | disney,musical
P | HVrH2ryv2wo | film | I See the Light (Tangled) | Alan Menken | 2010 | .6 | Emile Pandolfi | Mandy Moore & Zachary Levi | disney,musical
P | tyloC0e-Tqk | film | When You Wish Upon a Star (Pinocchio) | Leigh Harline | 1940 | .85 | kno Piano Music | Cliff Edwards | disney,songbook
P | _HOR7Qosjns | film | Someday My Prince Will Come (Snow White) | Frank Churchill | 1937 | .7 | kno Piano Music | Adriana Caselotti | disney,waltz,songbook
P | 42fwefSeJ58 | film | Remember Me (Coco) | Kristen Anderson-Lopez & Robert Lopez | 2017 | .6 | TranscriptionsByPaul | Benjamin Bratt | pixar,ballad
P | TZXMX1jrXX4 | film | Pure Imagination (Willy Wonka) | Leslie Bricusse & Anthony Newley | 1971 | .7 | Sangah Noona | Gene Wilder | musical,songbook
P | Yl-rp53b3gQ | film | My Favorite Things (The Sound of Music) | Richard Rodgers | 1959 | .85 | TranscriptionsByPaul | Julie Andrews | musical,waltz,songbook
P | YGV3O35TyIc | film | Edelweiss (The Sound of Music) | Richard Rodgers | 1959 | .78 | Sharon Wilson | Christopher Plummer | musical,waltz
P | JOtI-WK-c3k | film | Memory (Cats) | Andrew Lloyd Webber | 1981 | .85 | Ross The Piano Man | Elaine Paige | musical,lloyd-webber
P | lB4PRX737-0 | film | Merry-Go-Round of Life (Howl's Moving Castle) | Joe Hisaishi | 2004 | .85 | Animenz | Joe Hisaishi | ghibli,waltz
P | c5DkT-dOg1k | film | To Zanarkand (Final Fantasy X) | Nobuo Uematsu | 2001 | .6 | PianiCast | Nobuo Uematsu | game
P | 9SeNy3TzA-Y | film | Minecraft: Sweden / Wet Hands | C418 | 2011 | .6 | Rousseau | C418 | game,ambient
P | g5YV82pbLl4 | film | The Pink Panther Theme | Henry Mancini | 1963 | .85 | Piano안현진 | Henry Mancini | soundtrack,mancini
P | Dpy0ReA6agQ | film | You've Got a Friend in Me (Toy Story) | Randy Newman | 1995 | .7 | Erik C 'Piano Man' | Randy Newman | pixar,stride
P | bNl8jDM9Ong | film | Cavatina (The Deer Hunter) | Stanley Myers | 1970 | .65 | SangHeart Play | John Williams (guitar) | soundtrack
# ---- film: Pianist8 (Hisaishi, Sakamoto and Clayderman) ----
P8 | midi/Hisaishi/Hisaishi_One_Summer's_Day.mid | film | One Summer's Day (Spirited Away) | Joe Hisaishi | 2001 | .85 | Joe Hisaishi | Joe Hisaishi | ghibli
P8 | midi/Hisaishi/Hisaishi_Summer.mid | film | Summer (Kikujiro) | Joe Hisaishi | 1999 | .82 | Joe Hisaishi | Joe Hisaishi | soundtrack
P8 | midi/Hisaishi/Hisaishi_Tonari_no_Totoro.mid | film | My Neighbor Totoro | Joe Hisaishi | 1988 | .8 | Joe Hisaishi | Joe Hisaishi | ghibli
P8 | midi/Hisaishi/Hisaishi_Kaze_no_Toori_Michi.mid | film | The Path of the Wind (My Neighbor Totoro) | Joe Hisaishi | 1988 | .65 | Joe Hisaishi | Joe Hisaishi | ghibli
P8 | midi/Hisaishi/Hisaishi_Kimi_wo_Nosete.mid | film | Carrying You (Castle in the Sky) | Joe Hisaishi | 1986 | .65 | Joe Hisaishi | Joe Hisaishi | ghibli
P8 | midi/Hisaishi/Hisaishi_Ashitaka_and_San.mid | film | Ashitaka and San (Princess Mononoke) | Joe Hisaishi | 1997 | .6 | Joe Hisaishi | Joe Hisaishi | ghibli
P8 | midi/Hisaishi/Hisaishi_Inochi_no_Namae.mid | film | The Name of Life (Spirited Away) | Joe Hisaishi | 2001 | .6 | Joe Hisaishi | Joe Hisaishi | ghibli
P8 | midi/Hisaishi/Hisaishi_Itsumo_Nando_Demo.mid | film | Always With Me (Spirited Away) | Youmi Kimura | 2001 | .7 | Joe Hisaishi | Youmi Kimura | ghibli
P8 | midi/Hisaishi/Hisaishi_The_Promise_of_the_World.mid | film | The Promise of the World (Howl's Moving Castle) | Joe Hisaishi | 2004 | .55 | Joe Hisaishi | Joe Hisaishi | ghibli
P8 | midi/Ryuichi/Ryuichi_Merry_Christmas_Mr_Lawrence.mid | film | Merry Christmas, Mr. Lawrence | Ryuichi Sakamoto | 1983 | .88 | Ryuichi Sakamoto | Ryuichi Sakamoto | soundtrack,christmas
P8 | midi/Ryuichi/Ryuichi_The_Last_Emperor.mid | film | The Last Emperor | Ryuichi Sakamoto | 1987 | .65 | Ryuichi Sakamoto | Ryuichi Sakamoto | soundtrack
P8 | midi/Ryuichi/Ryuichi_Rain.mid | film | Rain (The Last Emperor) | Ryuichi Sakamoto | 1987 | .6 | Ryuichi Sakamoto | Ryuichi Sakamoto | soundtrack
P8 | midi/Ryuichi/Ryuichi_The_Sheltering_Sky.mid | film | The Sheltering Sky | Ryuichi Sakamoto | 1990 | .55 | Ryuichi Sakamoto | Ryuichi Sakamoto | soundtrack
P8 | midi/Clayderman/Clayderman_I_dreamed_a_dream.mid | film | I Dreamed a Dream (Les Misérables) | Claude-Michel Schönberg | 1980 | .85 | Richard Clayderman | Les Misérables | musical
P8 | midi/Clayderman/Clayderman_Love_Story.mid | film | Love Story (Where Do I Begin) | Francis Lai | 1970 | .8 | Richard Clayderman | Francis Lai | soundtrack,ballad
P8 | midi/Clayderman/Clayderman_Romeo_and_Juliet.mid | film | Love Theme from Romeo and Juliet (A Time for Us) | Nino Rota | 1968 | .7 | Richard Clayderman | Nino Rota | soundtrack
# ---- film: ADL two-hand arrangements (gap fillers) ----
A | adl-piano-midi/Rock/Cyberpunk/Vangelis/Chariots of Fire.mid | film | Chariots of Fire | Vangelis | 1981 | .85 | | Vangelis | soundtrack
A | adl-piano-midi/Country/Nashville Sound/Floyd Cramer/Can You Feel The Love Tonight (From The Lion King).mid | film | Can You Feel the Love Tonight (The Lion King) | Elton John & Tim Rice | 1994 | .82 | | Elton John | disney,ballad
A | adl-piano-midi/Soundtracks/Disney/Alan Menken/Part of Your World.mid | film | Part of Your World (The Little Mermaid) | Alan Menken | 1989 | .75 | | Jodi Benson | disney,musical
A | adl-piano-midi/Soundtracks/Soundtrack/Alan Silvestri/Forest Gump Theme.mid | film | Forrest Gump: Feather Theme | Alan Silvestri | 1994 | .7 | | Alan Silvestri | soundtrack
A | adl-piano-midi/Soundtracks/British Soundtrack/John Barry/I Had a Farm in Africa.mid | film | I Had a Farm in Africa (Out of Africa) | John Barry | 1985 | .6 | | John Barry | soundtrack
A | adl-piano-midi/Religious/Christian Relaxative/Stan Whitmire/Somewhere In Time.mid | film | Somewhere in Time | John Barry | 1980 | .6 | | John Barry | soundtrack
A | adl-piano-midi/Soundtracks/Broadway/Andrew Lloyd Webber/Angel of Music.mid | film | Angel of Music (The Phantom of the Opera) | Andrew Lloyd Webber | 1986 | .65 | | The Phantom of the Opera | musical,lloyd-webber
# ---- standards: songbook evergreens and modern standards, played as songs ----
P | X3_A9leN7BQ | standards | Moon River | Henry Mancini & Johnny Mercer | 1961 | .95 | Sabin Todorov | Audrey Hepburn / Andy Williams | ballad,film,breakfast-at-tiffanys
P | hkC5sHe46r8 | standards | Over the Rainbow | Harold Arlen & Yip Harburg | 1939 | .97 | TranscriptionsByPaul | Judy Garland | ballad,film,wizard-of-oz
P | kMRh3S4ZYAM | standards | What a Wonderful World | Bob Thiele & George David Weiss | 1967 | .95 | MIWHA | Louis Armstrong | ballad
P | tFoNohJN9NM | standards | Fly Me to the Moon | Bart Howard | 1954 | .9 | Daeyoun Kim | Frank Sinatra | ballad,sinatra
P | mVj5DlVnTyc | standards | My Way | Claude François, Jacques Revaux & Paul Anka | 1969 | .9 | SangHeart Play | Frank Sinatra | ballad,sinatra
P | ss6Ep30_EXo | standards | Autumn Leaves | Joseph Kosma | 1945 | .9 | Mohamad Hikmat Mouaket | Yves Montand / Nat King Cole | ballad,chanson
P | loDlBqsBlgk | standards | As Time Goes By | Herman Hupfeld | 1931 | .85 | Alexandre di Paoli | Dooley Wilson | ballad,film,casablanca
P | 8iYyaNhrkkE | standards | La Vie en rose | Louiguy | 1945 | .88 | MIWHA | Édith Piaf | chanson
P | EYKay3PRdhI | standards | Hymne à l'amour | Marguerite Monnot | 1949 | .65 | SangHeart Play | Édith Piaf | chanson,ballad
P | m44cR5u9SJs | standards | Georgia on My Mind | Hoagy Carmichael | 1930 | .85 | Sabin Todorov | Ray Charles | ballad,blues
P | _vDbNWnZTYM | standards | Smoke Gets in Your Eyes | Jerome Kern | 1933 | .8 | Sabin Todorov | The Platters | ballad
P | 25WoJr5wKUc | standards | Cry Me a River | Arthur Hamilton | 1953 | .75 | Sabin Todorov | Julie London | ballad
P | KZ4uck0lj8E | standards | My Funny Valentine | Richard Rodgers | 1937 | .8 | Sabin Todorov | Chet Baker | ballad,broadway
P | aUw1QKmyi84 | standards | The Girl from Ipanema | Antônio Carlos Jobim | 1962 | .85 | PianoRaccoon | Astrud Gilberto & Stan Getz | bossa-nova
P | wFrxV3sufY4 | standards | Summertime | George Gershwin | 1935 | .9 | Piano안현진 | Porgy and Bess | gershwin,broadway
P | Ogin7kERbco | standards | Singin' in the Rain | Nacio Herb Brown | 1929 | .8 | Piano안현진 | Gene Kelly | film
P | K3j5qOIShxo | standards | Can't Take My Eyes Off You | Bob Crewe & Bob Gaudio | 1967 | .75 | Piano안현진 | Frankie Valli | 60s
P | 9h2W0znzz9Y | standards | Por una cabeza | Carlos Gardel | 1935 | .7 | Piano안현진 | Carlos Gardel | tango,film
P | hGLviF2RDxY | standards | The Look of Love | Burt Bacharach & Hal David | 1967 | .6 | Piano안현진 | Dusty Springfield | bacharach,bossa-nova
P | Ner5bAOSVZc | standards | Raindrops Keep Fallin' on My Head | Burt Bacharach & Hal David | 1969 | .8 | Stephan Schleiner | B.J. Thomas | bacharach,film
P | 3KyaCATkaPQ | standards | (They Long to Be) Close to You | Burt Bacharach & Hal David | 1963 | .75 | Hiroshi Nakamura | Carpenters | bacharach,ballad
P | zfHxSc4T1Hw | standards | Theme from New York, New York | John Kander & Fred Ebb | 1977 | .82 | PianoRaccoon | Frank Sinatra | sinatra,film
P | CtNv4BtdOfY | standards | Smile | Charlie Chaplin | 1936 | .75 | TranscriptionsByPaul | Nat King Cole | ballad,film
P | rA61MOxRcQ4 | standards | Bésame mucho | Consuelo Velázquez | 1940 | .8 | TranscriptionsByPaul | Consuelo Velázquez | bolero,latin
P | Wk9yje0LkcQ | standards | Danny Boy | Traditional (Londonderry Air) | 1913 | .8 | THIS IS PIANO | Traditional | ballad,irish,folk
P | HI36Hiyx9oE | standards | White Christmas | Irving Berlin | 1942 | .9 | TranscriptionsByPaul | Bing Crosby | christmas
P | llMSUfkaYCo | standards | The Christmas Song (Chestnuts Roasting on an Open Fire) | Mel Tormé & Bob Wells | 1945 | .8 | Sabin Todorov | Nat King Cole | christmas,ballad
P | WdkxbFeGtrU | standards | Have Yourself a Merry Little Christmas | Hugh Martin & Ralph Blane | 1944 | .8 | Music Fellows | Judy Garland | christmas,ballad
P | dMbh6k1aFcM | standards | Let It Snow! Let It Snow! Let It Snow! | Jule Styne & Sammy Cahn | 1945 | .75 | TranscriptionsByPaul | Vaughn Monroe | christmas
P | gWh7RJLPlqw | standards | Winter Wonderland | Felix Bernard | 1934 | .75 | Piano안현진 | Richard Himber | christmas
P8 | midi/Yiruma/Yiruma_Dream_a_Little_Dream_of_Me.mid | standards | Dream a Little Dream of Me | Fabian Andre & Wilbur Schwandt | 1931 | .72 | Yiruma | The Mamas & the Papas | ballad
P8 | midi/Clayderman/Clayderman_Unchained_Melody.mid | standards | Unchained Melody | Alex North & Hy Zaret | 1955 | .92 | Richard Clayderman | The Righteous Brothers | ballad,film
P8 | midi/Clayderman/Clayderman_Strangers_in_the_Night.mid | standards | Strangers in the Night | Bert Kaempfert | 1966 | .8 | Richard Clayderman | Frank Sinatra | sinatra,ballad
P8 | midi/Clayderman/Clayderman_Stranger_on_the_shore.mid | standards | Stranger on the Shore | Acker Bilk | 1961 | .55 | Richard Clayderman | Acker Bilk | ballad
"""

FIELDS = ["src", "ref", "genre", "title", "composer", "year", "rank", "performer", "artist", "tags"]


def parse_selection() -> list[dict]:
    out = []
    for ln, line in enumerate(SELECTION.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != len(FIELDS):
            raise SystemExit(f"SELECTION line {ln}: {len(parts)} fields: {line}")
        d = dict(zip(FIELDS, parts))
        d["year"] = int(d["year"]) if d["year"] else None
        d["rank"] = float(d["rank"])
        d["performer"] = d["performer"] or None
        d["artist"] = d["artist"] or None
        d["tags"] = [t for t in d["tags"].split(",") if t]
        out.append(d)
    return out


# --------------------------------------------------------------------------------------------------------
# polite HTTP
# --------------------------------------------------------------------------------------------------------
_last: dict[str, float] = {}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def http_get(url: str, tries: int = 5) -> bytes:
    host = urllib.parse.urlsplit(url).hostname or ""
    gap = MIN_GAP.get(host, 1.0)
    err = None
    for k in range(tries):
        wait = _last.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            err = e
            if e.code in (400, 401, 403, 404, 410):  # not worth retrying (403 = blocked/rate-limited page)
                break
        except Exception as e:  # network hiccup
            err = e
        time.sleep(min(60, 2 * 2 ** k))
    raise RuntimeError(f"GET {url}: {err}")


def cached(path: Path, urls: list[str]) -> Path:
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for u in urls:
        try:
            data = http_get(u)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            log(f"  fetched {u} ({len(data)} bytes)")
            return path
        except Exception as e:
            last = e
            log(f"  failed {u}: {e}")
    raise RuntimeError(f"no source for {path.name}: {last}")


# --------------------------------------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------------------------------------
class Sources:
    def __init__(self, cache: Path):
        self.cache = cache
        self._p8: zipfile.ZipFile | None = None
        self._p8_names: dict[str, str] = {}
        self._adl: zipfile.ZipFile | None = None

    def piast(self, vid: str) -> bytes:
        p = cached(self.cache / "piast" / f"{vid}.mid", [PIAST_URL.format(id=vid)])
        return p.read_bytes()

    def pianist8(self, member: str) -> bytes:
        if self._p8 is None:
            p = cached(self.cache / "Pianist8.zip", P8_URLS)
            self._p8 = zipfile.ZipFile(p)
            # top-level folder differs between the Zenodo and GitHub zips; key by the path below it
            self._p8_names = {n.split("/", 1)[1]: n for n in self._p8.namelist() if "/" in n}
        return self._p8.read(self._p8_names[member])

    def adl(self, member: str) -> bytes:
        if self._adl is None:
            self._adl = zipfile.ZipFile(cached(self.cache / "adl-piano-midi.zip", [ADL_URL]))
        return self._adl.read(member)


# --------------------------------------------------------------------------------------------------------
# MIDI handling
# --------------------------------------------------------------------------------------------------------
def _varlen(b: bytes, i: int) -> tuple[int, int]:
    v = 0
    while True:
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, i


def _enc_varlen(v: int) -> bytes:
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.append(0x80 | (v & 0x7F))
        v >>= 7
    return bytes(reversed(out))


def strip_bad_keysig(data: bytes) -> tuple[bytes, int]:
    """Remove key-signature meta events mido refuses (|sharps/flats| > 7 or mode > 1), carrying their delta
    time onto the next event. Returns (bytes, removed count); bytes are unchanged when nothing is removed."""
    if data[:4] != b"MThd":
        return data, 0
    hlen = int.from_bytes(data[4:8], "big")
    out = bytearray(data[:8 + hlen])
    pos, removed = 8 + hlen, 0
    while pos + 8 <= len(data):
        cid, clen = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "big")
        body = data[pos + 8:pos + 8 + clen]
        pos += 8 + clen
        if cid != b"MTrk":
            out += cid + clen.to_bytes(4, "big") + body
            continue
        new, i, carry, running = bytearray(), 0, 0, None
        while i < len(body):
            delta, i = _varlen(body, i)
            start = i
            st = body[i]
            drop = False
            if st == 0xFF:
                mtype = body[i + 1]
                ln, j = _varlen(body, i + 2)
                payload = body[j:j + ln]
                i = j + ln
                if mtype == 0x59 and (ln != 2 or not -7 <= int.from_bytes(payload[:1], "big", signed=True) <= 7
                                      or payload[1:2] not in (b"\x00", b"\x01")):
                    drop = True
            elif st in (0xF0, 0xF7):
                ln, j = _varlen(body, i + 1)
                i = j + ln
            else:
                if st & 0x80:
                    running = st
                    i += 1
                hi = (running or 0) & 0xF0
                i += 1 if hi in (0xC0, 0xD0) else 2
            if drop:
                carry += delta
                removed += 1
                continue
            new += _enc_varlen(delta + carry) + body[start:i]
            carry = 0
        out += b"MTrk" + len(new).to_bytes(4, "big") + new
    return (bytes(out), removed) if removed else (data, 0)


def drop_melody_track(data: bytes) -> tuple[bytes, int]:
    m = mido.MidiFile(file=io.BytesIO(data))
    keep = [t for t in m.tracks if (t.name or "").strip().lower() != "melody"]
    dropped = len(m.tracks) - len(keep)
    if not dropped:
        return data, 0
    m.tracks[:] = keep
    buf = io.BytesIO()
    m.save(file=buf)
    return buf.getvalue(), dropped


def midi_stats(data: bytes) -> dict:
    m = mido.MidiFile(file=io.BytesIO(data))
    t, last, notes, vels, ped = 0.0, 0.0, 0, set(), 0
    for msg in m:
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            notes += 1
            vels.add(msg.velocity)
            last = max(last, t)
        elif msg.type in ("note_off", "note_on"):
            last = max(last, t)
        elif msg.type == "control_change" and msg.control == 64:
            ped += 1
    return {"duration_s": round(last, 1), "notes": notes, "velocities": len(vels), "pedal_events": ped}


def slug(s: str, n: int = 48) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:n].rstrip("-") or "x"


# --------------------------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------------------------
SOURCE_NAME = {"P": "PIAST-YT", "P8": "Pianist8", "A": "ADL Piano MIDI"}
# PIAST channels that post other pianists' recordings with sheet music: the player is not named
CHANNEL_NOT_PLAYER = {"TranscriptionsByPaul"}
CHANNEL_OF = {"kno Piano Music": "TranscriptionsByPaul", "Emile Pandolfi": "TranscriptionsByPaul",
              "Sangah Noona": "TranscriptionsByPaul"}


def build_item(s: dict, src: Sources) -> tuple[dict, bytes]:
    kind, ref = s["src"], s["ref"]
    extra: dict = {}
    if kind == "P":
        raw = src.piast(ref)
        data, dropped = drop_melody_track(raw)
        if not dropped:
            log(f"  note: no 'melody' track in {ref}")
        source_ref = f"{PIAST_HOME}/blob/main/piast_yt/midi/{ref}.mid"
        extra["video"] = f"https://www.youtube.com/watch?v={ref}"
        extra["channel"] = CHANNEL_OF.get(s["performer"], s["performer"])
        if s["performer"] in CHANNEL_NOT_PLAYER:  # the channel transcribes someone else's recording
            s = {**s, "performer": None}
        licence, take, prefix = PIAST_LICENCE, "transcription", "piast"
    elif kind == "P8":
        data = src.pianist8(ref)
        source_ref = f"{P8_HOME} : Pianist8/{ref}"
        licence, take, prefix = P8_LICENCE, "transcription", "p8"
    elif kind == "A":
        data, removed = strip_bad_keysig(src.adl(ref))
        if removed:
            extra["fixed"] = f"removed {removed} malformed key-signature event(s)"
        source_ref = f"{ADL_HOME} : {ref}"
        licence, take, prefix = ADL_LICENCE, "arrangement", "adl"
    else:
        raise ValueError(kind)
    st = midi_stats(data)  # raises if mido can't parse
    if st["notes"] < 16:
        raise ValueError(f"only {st['notes']} notes")
    name = f"{s['genre']}--{slug(s['composer'].split(',')[0].split('&')[0], 24)}--{slug(s['title'])}--{prefix}"
    if kind == "P":
        name += "-" + re.sub(r"[^A-Za-z0-9_-]", "", ref)
    name += ".mid"
    item = {
        "src": f"files/{name}",
        "title": s["title"],
        "composer": s["composer"],
        "performer": s["performer"],
        "genre": s["genre"],
        "era": "Contemporary" if s["genre"] == "classical" else None,
        "year": s["year"],
        "source": SOURCE_NAME[kind],
        "source_ref": source_ref,
        "licence": licence,
        "take": take,
        "tags": s["tags"],
        "rank": s["rank"],
        "duration_s": st["duration_s"],
        "notes": st["notes"],
        "artist": s["artist"],
        "velocities": st["velocities"],
        "pedal_events": st["pedal_events"],
        "sha256": hashlib.sha256(data).hexdigest(),
        **extra,
    }
    return item, data


def write_if_changed(path: Path, data: bytes) -> bool:
    if path.exists() and path.read_bytes() == data:
        return False
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return True


GAPS = """Wanted but not staged: no expressive take, and the only arrangement found was flat (one velocity),
abridged to under a minute, or a band part without the tune.
- Pop: Tiny Dancer, Rocket Man, Candle in the Wind, Daniel (Elton John); She's Always a Woman, Vienna
  (Billy Joel); Don't Stop Me Now, Somebody to Love, The Show Must Go On (Queen); Mamma Mia, Thank You for
  the Music, Chiquitita (ABBA); Hello, Skyfall, Make You Feel My Love, Rolling in the Deep (Adele);
  Superstition, Sir Duke (Stevie Wonder); Hotel California; Africa; A Whiter Shade of Pale; Somewhere Only
  We Know; When I Was Your Man; Thinking Out Loud; Fields of Gold; In My Life; Lady Madonna; Live and Let
  Die; Heal the World (only an easy version); Billie Jean (only a band part).
- Film / musicals: Gabriel's Oboe; The Heart Asks Pleasure First; Concerning Hobbits (the Lord of the
  Rings main theme is staged); Jurassic Park; E.T.; Now We Are Free (Gladiator); Lara's Theme; The Windmills
  of Your Mind; West Side Story (Somewhere, Maria, Tonight); The Music of the Night, Don't Cry for Me
  Argentina (flat ADL files only); Divenire (Einaudi, flat ADL file only).
- Covered elsewhere on purpose: Canon in D, Clair de lune, Fur Elise, Gymnopedie, The Entertainer and
  Rhapsody in Blue belong to the classical and ragtime sets; jazz-pianist takes of standards to the jazz set.
- The self-transcription route (piano-transcription models on chosen YouTube covers) would fill most of
  these, but needs the GPU, which the live 4K video owns."""


def sources_md(items: list[dict]) -> str:
    def n(src, take=None):
        return sum(1 for i in items if i["source"] == src and (take is None or i["take"] == take))

    by_genre = {}
    for i in items:
        by_genre[i["genre"]] = by_genre.get(i["genre"], 0) + 1
    g = ", ".join(f"{k} {v}" for k, v in sorted(by_genre.items()))
    return f"""# Pop, film and standards: sources

Staged by `tools/music_library/fetch_pop.py` (re-runnable; downloads cached in
`~/.cache/screen-machine/midi-library/pop/`). {len(items)} files: {g}.
Private use in this house only, never redistributed. Licence strings are recorded as published; the
compositions and the original recordings remain their owners'.

## PIAST-YT (PIAST dataset, YouTube subset): {n('PIAST-YT')} files, take "transcription"

- URL: {PIAST_HOME} (`piast_yt/midi/<YouTube id>.mid`), project page https://hayeonbang.github.io/PIAST_dataset/
- Licence: **{PIAST_LICENCE}** (Hugging Face dataset card).
- What: solo-piano covers from YouTube, transcribed to MIDI (notes, velocity, sustain pedal), tempo-mapped
  per beat. The dataset has no titles; titles and channels were looked up with YouTube oEmbed by id and
  checked by hand. One version per title, preferring faithful covers by strong players.
- Change made here: the second track `melody` (a copy of the piano's top notes) is removed; nothing else.
- Each manifest item has `video` (the YouTube source) and `channel`; `performer` is the channel's pianist,
  or null where the channel posts other pianists' recordings without naming them.
- Citation: Hayeon Bang, Eunjin Choi, Megan Finch, Seungheon Doh, Seolhee Lee, Gyeong-Hoon Lee, Juhan Nam,
  "PIAST: A Multimodal Piano Dataset with Audio, Symbolic and Text", NLP4MusA workshop, 2024.

## Pianist8: {n('Pianist8')} files, take "transcription"

- URL: {P8_HOME} (`Pianist8-v1.0.0.zip`); identical copy: https://github.com/joann8512/Pianist8 at commit
  ab9f541 (used when Zenodo refuses the download).
- Licence: **{P8_LICENCE}**.
- What: audio recordings transcribed to MIDI (velocity and pedal): Ludovico Einaudi, Yiruma, Joe Hisaishi
  and Ryuichi Sakamoto playing their own pieces, and Richard Clayderman. Files unchanged.
- Citation: Yi-Hui Chou, I-Chun Chen, Chin-Jui Chang, Joann Ching, Yi-Hsuan Yang, "MidiBERT-Piano:
  Large-scale Pre-training for Symbolic Music Understanding", arXiv:2107.05223, 2021.

## ADL Piano MIDI: {n('ADL Piano MIDI')} files, take "arrangement"

- URL: {ADL_HOME} (`midi/adl-piano-midi.zip`)
- Licence: none stated in the repository (recorded as null).
- What: two-hand piano arrangements scraped from the web (mostly typed in from sheet music: notated
  velocities, little or no pedal, some abridged). Used only for titles no expressive source covers.
  Files unchanged except that a malformed key-signature meta event (which stops mido parsing) is removed
  from the few files that have one (`fixed` in the manifest).
- Citation: Lucas N. Ferreira, Levi H. S. Lelis, Jim Whitehead, "Computer-Generated Music for Tabletop
  Role-Playing Games", AIIDE 2020.

## Gaps (no usable solo-piano take in these sources)

{GAPS}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.expanduser(f"~/.local/share/screen-machine/midi-library/_staging/{KEY}"))
    ap.add_argument("--cache", default=os.path.expanduser(f"~/.cache/screen-machine/midi-library/{KEY}"))
    ap.add_argument("--list", action="store_true", help="print the selection and exit")
    ap.add_argument("--only", help="stage only entries whose title contains this text (manifest still lists all staged)")
    a = ap.parse_args()

    sel = parse_selection()
    titles = [(s["genre"], s["title"].lower()) for s in sel]
    dup = {t for t in titles if titles.count(t) > 1}
    if dup:
        raise SystemExit(f"duplicate titles in SELECTION: {dup}")
    if a.list:
        for s in sel:
            print(f"{s['genre']:9s} {s['rank']:.2f} {s['src']:2s} {s['title']} -- {s['composer']} [{s['performer']}]")
        print(len(sel), "entries")
        return 0

    out, cache = Path(a.out), Path(a.cache)
    files = out / "files"
    files.mkdir(parents=True, exist_ok=True)
    src = Sources(cache)
    man_path = out / "manifest.json"
    previous = {}
    if man_path.exists():
        try:
            previous = {(i["genre"], i["title"]): i for i in json.loads(man_path.read_text())["items"]}
        except Exception:
            previous = {}

    items, failed, written = [], [], 0
    for s in sel:
        if a.only and a.only.lower() not in s["title"].lower():
            if (s["genre"], s["title"]) in previous:
                items.append(previous[(s["genre"], s["title"])])
            continue
        try:
            item, data = build_item(s, src)
        except Exception as e:
            failed.append((s["title"], s["src"], s["ref"], str(e)))
            log(f"FAIL {s['title']} ({s['src']} {s['ref']}): {e}")
            continue
        if write_if_changed(out / item["src"], data):
            written += 1
        items.append(item)

    # drop staged files no longer in the manifest (only our own *.mid in files/)
    keep = {Path(i["src"]).name for i in items}
    for f in files.glob("*.mid"):
        if f.name not in keep:
            f.unlink()

    items.sort(key=lambda i: (i["genre"], -i["rank"], i["title"]))
    manifest = {"v": 1, "key": KEY, "items": items}
    tmp = man_path.with_suffix(".json.part")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, man_path)
    (out / "SOURCES.md").write_text(sources_md(items))

    by = {}
    for i in items:
        by[(i["source"], i["take"])] = by.get((i["source"], i["take"]), 0) + 1
    log(f"staged {len(items)} items ({written} written this run) in {out}")
    for (k, t), v in sorted(by.items()):
        log(f"  {k:16s} {t:13s} {v}")
    if failed:
        log(f"{len(failed)} failed:")
        for f in failed:
            log("  ", *f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
