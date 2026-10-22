#!/usr/bin/env python3
"""Fetch, select and stage the jazz shelf of the MIDI library (jazz standards on solo piano).

Sources (see SOURCES.md written next to the manifest):
  * PiJAMA (Zenodo 8354955, midi_kong.zip + pijama.csv from the project's GitHub): solo jazz piano
    recordings by 120 pianists, transcribed to MIDI with notes and sustain pedal.
  * Doug McKenzie's jazz piano MIDI (bushgrafts.com): standards hand-played on a MIDI keyboard.
  * PIAST-YT (Hugging Face Hayeonbang/PIAST): YouTube solo-piano videos transcribed to MIDI; a curated
    handful (PIAST_TABLE) for standards the others lack and for transcriptions of named pianists.

For every standard in STANDARDS_TABLE (canonical title, composer, year, rank) the best take is chosen:
the most famous pianist's PiJAMA performance (a composer playing their own tune ranks first),
else Doug McKenzie's solo take, else a lesser-known pianist, else a pianist-teacher's arrangement
(PIAST-YT), else a McKenzie trio take made one piano (piano + bass line, no drums). One take per
title. Titles already in config/live/test-piano are skipped.

Output: ~/.local/share/screen-machine/midi-library/_staging/jazz/{files/*.mid,manifest.json,SOURCES.md}
Cache:  ~/.local/share/screen-machine/midi-library/_cache/jazz/ (downloads; re-runs reuse it)

Re-runnable and idempotent: downloads are cached (and resumed), files are rewritten only when their
bytes change, and the manifest is rebuilt from scratch each run.

Usage:  nice -n 19 ~/.venvs/sm-musiclib/bin/python -I tools/music_library/fetch_jazz.py [--report]
Needs:  mido (pip install mido).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
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

KEY = "jazz"
ROOT = Path(os.path.expanduser("~/.local/share/screen-machine/midi-library"))
STAGE = ROOT / "_staging" / KEY
CACHE = ROOT / "_cache" / KEY
REPO = Path(__file__).resolve().parents[2]
TEST_PIANO = REPO / "config" / "live" / "test-piano"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

PIJAMA_ZIP_URL = "https://zenodo.org/api/records/8354955/files/midi_kong.zip/content"
PIJAMA_ZIP_MD5 = "9920ab88ec11757ca0c138478ef27653"
PIJAMA_ZIP_SIZE = 36435319
PIJAMA_CSV_URL = "https://raw.githubusercontent.com/almostimplemented/PiJAMA/main/pijama.csv"
PIJAMA_LICENCE = "CC BY-NC 4.0"
MCK_INDEX_URL = "https://bushgrafts.com/jazz/"
MCK_PAGE_URL = "https://bushgrafts.com/midi/"

# ---------------------------------------------------------------------------------------------
# The standards to look for: title | composer | year | rank (0..1, how well known) | tags | aliases
# Aliases are alternative titles (matched after normalisation, like the title itself).
# ---------------------------------------------------------------------------------------------
STANDARDS_TABLE = r"""
Autumn Leaves | Joseph Kosma | 1945 | 0.99 | ballad,songbook | Les feuilles mortes
All the Things You Are | Jerome Kern | 1939 | 0.97 | songbook,broadway |
Body and Soul | Johnny Green | 1930 | 0.97 | ballad,songbook |
'Round Midnight | Thelonious Monk | 1944 | 0.97 | ballad,monk | Round About Midnight; Round Midnight
Misty | Erroll Garner | 1954 | 0.97 | ballad |
My Funny Valentine | Richard Rodgers | 1937 | 0.97 | ballad,broadway |
Summertime | George Gershwin | 1935 | 0.98 | porgy and bess,gershwin |
Take Five | Paul Desmond | 1959 | 0.98 | 5/4,cool jazz |
Fly Me to the Moon | Bart Howard | 1954 | 0.98 | songbook | In Other Words
The Girl from Ipanema | Antônio Carlos Jobim | 1962 | 0.97 | bossa nova | Garota de Ipanema; Girl from Ipanema
Georgia on My Mind | Hoagy Carmichael | 1930 | 0.96 | ballad,songbook | Georgia
Over the Rainbow | Harold Arlen | 1939 | 0.97 | ballad,film | Somewhere Over the Rainbow
Satin Doll | Duke Ellington | 1953 | 0.95 | swing,ellington |
Stella by Starlight | Victor Young | 1944 | 0.94 | ballad,film |
In a Sentimental Mood | Duke Ellington | 1935 | 0.94 | ballad,ellington |
Blue in Green | Miles Davis | 1959 | 0.93 | ballad,modal,kind of blue |
Waltz for Debby | Bill Evans | 1956 | 0.93 | waltz | Waltz for Debbie
Someday My Prince Will Come | Frank Churchill | 1937 | 0.92 | waltz,film | Some Day My Prince Will Come
So What | Miles Davis | 1959 | 0.94 | modal,kind of blue |
Giant Steps | John Coltrane | 1959 | 0.93 | up-tempo |
Maiden Voyage | Herbie Hancock | 1965 | 0.92 | modal |
Lush Life | Billy Strayhorn | 1949 | 0.9 | ballad,ellington |
Night and Day | Cole Porter | 1932 | 0.93 | songbook,porter |
Cheek to Cheek | Irving Berlin | 1935 | 0.92 | songbook,film |
I Got Rhythm | George Gershwin | 1930 | 0.92 | songbook,gershwin,rhythm changes | I've Got Rhythm
Embraceable You | George Gershwin | 1930 | 0.9 | ballad,gershwin |
The Man I Love | George Gershwin | 1924 | 0.9 | ballad,gershwin | Man I Love
Moonlight in Vermont | Karl Suessdorf | 1944 | 0.86 | ballad |
Days of Wine and Roses | Henry Mancini | 1962 | 0.9 | film | The Days of Wine and Roses
There Will Never Be Another You | Harry Warren | 1942 | 0.88 | songbook |
Solar | Miles Davis | 1954 | 0.85 | bebop |
Nardis | Miles Davis | 1958 | 0.86 | modal |
Peace Piece | Bill Evans | 1958 | 0.88 | improvisation |
Alone Together | Arthur Schwartz | 1932 | 0.86 | songbook,minor |
Willow Weep for Me | Ann Ronell | 1932 | 0.87 | ballad,blues |
Darn That Dream | Jimmy Van Heusen | 1939 | 0.84 | ballad |
What Is This Thing Called Love? | Cole Porter | 1929 | 0.87 | songbook,porter | What Is This Thing Called Love
Have You Met Miss Jones? | Richard Rodgers | 1937 | 0.85 | songbook | Have You Met Miss Jones
Just Friends | John Klenner | 1931 | 0.82 | songbook |
Cherokee | Ray Noble | 1938 | 0.87 | up-tempo,bebop |
Confirmation | Charlie Parker | 1946 | 0.86 | bebop |
Straight, No Chaser | Thelonious Monk | 1951 | 0.9 | blues,monk | Straight No Chaser
Well, You Needn't | Thelonious Monk | 1944 | 0.88 | monk | Well You Needn't; Well You Neednt
Ruby, My Dear | Thelonious Monk | 1945 | 0.86 | ballad,monk | Ruby My Dear
Caravan | Juan Tizol | 1936 | 0.92 | latin,ellington |
Sophisticated Lady | Duke Ellington | 1932 | 0.9 | ballad,ellington |
Mood Indigo | Duke Ellington | 1930 | 0.91 | ballad,ellington |
Prelude to a Kiss | Duke Ellington | 1938 | 0.86 | ballad,ellington |
Yesterdays | Jerome Kern | 1933 | 0.84 | minor,broadway |
Sweet and Lovely | Gus Arnheim | 1931 | 0.72 | ballad |
Someone to Watch Over Me | George Gershwin | 1926 | 0.92 | ballad,gershwin |
Don't Blame Me | Jimmy McHugh | 1932 | 0.74 | ballad |
I Should Care | Axel Stordahl | 1944 | 0.78 | ballad |
Everything Happens to Me | Matt Dennis | 1940 | 0.78 | ballad |
Jitterbug Waltz | Fats Waller | 1942 | 0.8 | waltz,stride | The Jitterbug Waltz
In Walked Bud | Thelonious Monk | 1947 | 0.8 | monk |
Come Sunday | Duke Ellington | 1943 | 0.8 | ballad,ellington,spiritual |
You Don't Know What Love Is | Gene de Paul | 1941 | 0.8 | ballad,minor |
Stompin' at the Savoy | Edgar Sampson | 1934 | 0.82 | swing | Stomping at the Savoy
Easy to Love | Cole Porter | 1936 | 0.76 | songbook,porter |
Ain't Misbehavin' | Fats Waller | 1929 | 0.88 | stride,songbook | Aint Misbehavin
Tea for Two | Vincent Youmans | 1925 | 0.88 | songbook,broadway |
I Can't Get Started | Vernon Duke | 1936 | 0.82 | ballad | I Can't Get Started With You
Memories of You | Eubie Blake | 1930 | 0.76 | ballad |
Con Alma | Dizzy Gillespie | 1954 | 0.76 | latin,bebop |
After You've Gone | Turner Layton | 1918 | 0.76 | swing |
On Green Dolphin Street | Bronisław Kaper | 1947 | 0.88 | film,latin | Green Dolphin Street
Here's That Rainy Day | Jimmy Van Heusen | 1953 | 0.84 | ballad,broadway |
Spring Is Here | Richard Rodgers | 1938 | 0.76 | ballad,broadway |
Django | John Lewis | 1954 | 0.76 | ballad,mjq |
It Never Entered My Mind | Richard Rodgers | 1940 | 0.78 | ballad,broadway |
When Your Lover Has Gone | Einar Aaron Swan | 1931 | 0.68 | ballad |
How High the Moon | Morgan Lewis | 1940 | 0.84 | up-tempo,broadway |
It Might as Well Be Spring | Richard Rodgers | 1945 | 0.76 | ballad,film |
Love for Sale | Cole Porter | 1930 | 0.84 | latin,porter |
Ill Wind | Harold Arlen | 1934 | 0.68 | ballad |
Honeysuckle Rose | Fats Waller | 1929 | 0.86 | stride,swing |
Monk's Mood | Thelonious Monk | 1946 | 0.72 | ballad,monk |
They Can't Take That Away from Me | George Gershwin | 1937 | 0.86 | gershwin,film | They Can't Take That Away
I'm Getting Sentimental Over You | George Bassman | 1932 | 0.72 | ballad,swing |
Out of Nowhere | Johnny Green | 1931 | 0.78 | songbook |
The Way You Look Tonight | Jerome Kern | 1936 | 0.92 | songbook,film | Way You Look Tonight
Skylark | Hoagy Carmichael | 1941 | 0.8 | ballad |
Evidence | Thelonious Monk | 1948 | 0.7 | monk |
Somebody Loves Me | George Gershwin | 1924 | 0.74 | gershwin |
Old Folks | Willard Robison | 1938 | 0.7 | ballad |
I Got It Bad (and That Ain't Good) | Duke Ellington | 1941 | 0.78 | ballad,ellington | I Got It Bad; I Got It Bad and That Ain't Good
If I Had You | Ted Shapiro | 1928 | 0.68 | songbook |
I've Got the World on a String | Harold Arlen | 1932 | 0.76 | songbook | Ive Got the World on a String
Ask Me Now | Thelonious Monk | 1951 | 0.7 | ballad,monk |
Oleo | Sonny Rollins | 1954 | 0.76 | bebop,rhythm changes |
Smoke Gets in Your Eyes | Jerome Kern | 1933 | 0.9 | ballad,broadway |
A Child Is Born | Thad Jones | 1969 | 0.72 | ballad,waltz | Child Is Born
Sweet Georgia Brown | Maceo Pinkard | 1925 | 0.86 | swing |
You've Changed | Carl Fischer | 1941 | 0.72 | ballad | You Have Changed
The Very Thought of You | Ray Noble | 1934 | 0.8 | ballad | Very Thought of You
If You Could See Me Now | Tadd Dameron | 1946 | 0.72 | ballad,bebop |
My One and Only Love | Guy Wood | 1953 | 0.8 | ballad |
Polka Dots and Moonbeams | Jimmy Van Heusen | 1940 | 0.78 | ballad |
I Didn't Know What Time It Was | Richard Rodgers | 1939 | 0.7 | broadway |
Just One of Those Things | Cole Porter | 1935 | 0.8 | up-tempo,porter |
The Touch of Your Lips | Ray Noble | 1936 | 0.68 | ballad |
Gone with the Wind | Allie Wrubel | 1937 | 0.7 | songbook |
Begin the Beguine | Cole Porter | 1935 | 0.84 | latin,porter |
Speak Low | Kurt Weill | 1943 | 0.78 | latin,broadway |
Whisper Not | Benny Golson | 1956 | 0.74 | hard bop |
Trinkle Tinkle | Thelonious Monk | 1952 | 0.66 | monk |
I Hear a Rhapsody | George Fragos | 1940 | 0.68 | songbook |
The Nearness of You | Hoagy Carmichael | 1938 | 0.84 | ballad | Nearness of You
Misterioso | Thelonious Monk | 1948 | 0.68 | blues,monk |
I'm Confessin' (That I Love You) | Doc Daugherty | 1929 | 0.68 | songbook | I'm Confessin'; I'm Confessin That I Love You
It Could Happen to You | Jimmy Van Heusen | 1944 | 0.74 | songbook |
I Loves You, Porgy | George Gershwin | 1935 | 0.8 | ballad,porgy and bess,gershwin | I Loves You Porgy; I Love You Porgy
How Deep Is the Ocean? | Irving Berlin | 1932 | 0.76 | ballad | How Deep Is the Ocean
I'm Old Fashioned | Jerome Kern | 1942 | 0.7 | ballad,film |
St. Louis Blues | W. C. Handy | 1914 | 0.84 | blues | Saint Louis Blues; St Louis Blues
Where or When | Richard Rodgers | 1937 | 0.72 | ballad,broadway |
Reflections | Thelonious Monk | 1952 | 0.62 | ballad,monk |
Don't Explain | Billie Holiday | 1944 | 0.66 | ballad |
My Foolish Heart | Victor Young | 1949 | 0.82 | ballad,film |
Dancing in the Dark | Arthur Schwartz | 1931 | 0.76 | songbook |
Warm Valley | Duke Ellington | 1940 | 0.62 | ballad,ellington |
In the Still of the Night | Cole Porter | 1937 | 0.72 | porter |
These Foolish Things | Jack Strachey | 1936 | 0.76 | ballad | These Foolish Things Remind Me of You
My Heart Stood Still | Richard Rodgers | 1927 | 0.68 | broadway |
Bewitched, Bothered and Bewildered | Richard Rodgers | 1940 | 0.8 | ballad,broadway | Bewitched
It's Only a Paper Moon | Harold Arlen | 1933 | 0.78 | songbook | Paper Moon
Stardust | Hoagy Carmichael | 1927 | 0.92 | ballad,songbook | Star Dust
Lover | Richard Rodgers | 1932 | 0.72 | waltz,film |
Three Little Words | Harry Ruby | 1930 | 0.66 | songbook |
What's New? | Bob Haggart | 1939 | 0.74 | ballad | What's New
I Cover the Waterfront | Johnny Green | 1933 | 0.66 | ballad |
When I Fall in Love | Victor Young | 1952 | 0.86 | ballad,film |
Thou Swell | Richard Rodgers | 1927 | 0.62 | broadway |
Isn't It Romantic? | Richard Rodgers | 1932 | 0.72 | film | Isn't It Romantic
Basin Street Blues | Spencer Williams | 1928 | 0.74 | blues,new orleans |
Little Girl Blue | Richard Rodgers | 1935 | 0.7 | ballad,broadway |
Get Happy | Harold Arlen | 1930 | 0.7 | songbook |
I Get a Kick Out of You | Cole Porter | 1934 | 0.82 | porter,broadway |
I Surrender, Dear | Harry Barris | 1931 | 0.64 | ballad | I Surrender Dear
September Song | Kurt Weill | 1938 | 0.78 | ballad,broadway |
It Had to Be You | Isham Jones | 1924 | 0.84 | songbook |
If I Loved You | Richard Rodgers | 1945 | 0.66 | ballad,broadway |
On the Street Where You Live | Frederick Loewe | 1956 | 0.78 | broadway |
Rhythm-a-Ning | Thelonious Monk | 1957 | 0.66 | monk,rhythm changes | Rhythm a Ning; Rhythm-a-ning
As Time Goes By | Herman Hupfeld | 1931 | 0.92 | ballad,film |
Angel Eyes | Matt Dennis | 1946 | 0.76 | ballad,minor |
The Star-Crossed Lovers | Billy Strayhorn | 1957 | 0.62 | ballad,ellington | Star Crossed Lovers
I Remember You | Victor Schertzinger | 1941 | 0.72 | songbook |
Solitude | Duke Ellington | 1934 | 0.8 | ballad,ellington | In My Solitude
Blue Skies | Irving Berlin | 1926 | 0.82 | songbook |
All of You | Cole Porter | 1954 | 0.74 | porter,broadway |
This Can't Be Love | Richard Rodgers | 1938 | 0.68 | broadway |
My Favorite Things | Richard Rodgers | 1959 | 0.9 | waltz,broadway |
Corcovado (Quiet Nights of Quiet Stars) | Antônio Carlos Jobim | 1960 | 0.84 | bossa nova | Corcovado; Quiet Nights of Quiet Stars; Quiet Nights
Softly, as in a Morning Sunrise | Sigmund Romberg | 1928 | 0.76 | minor | Softly As in a Morning Sunrise
The Song Is You | Jerome Kern | 1932 | 0.68 | broadway | Song Is You
Things Ain't What They Used to Be | Mercer Ellington | 1942 | 0.7 | blues,ellington |
Monk's Dream | Thelonious Monk | 1952 | 0.66 | monk |
Drop Me Off in Harlem | Duke Ellington | 1933 | 0.6 | ellington |
Indian Summer | Victor Herbert | 1919 | 0.62 | songbook |
Day Dream | Billy Strayhorn | 1940 | 0.66 | ballad,ellington | Daydream
Time Remembered | Bill Evans | 1962 | 0.66 | ballad |
You Do Something to Me | Cole Porter | 1929 | 0.66 | porter |
Two Sleepy People | Hoagy Carmichael | 1938 | 0.62 | songbook |
Too Marvelous for Words | Richard Whiting | 1937 | 0.7 | songbook | Too Marvellous for Words
You Are Too Beautiful | Richard Rodgers | 1932 | 0.62 | ballad |
Bye-Ya | Thelonious Monk | 1952 | 0.6 | monk,latin | Bye Ya
Taking a Chance on Love | Vernon Duke | 1940 | 0.68 | songbook | Takin' a Chance on Love
That Old Feeling | Sammy Fain | 1937 | 0.66 | songbook |
I Let a Song Go Out of My Heart | Duke Ellington | 1938 | 0.66 | ellington |
People Will Say We're in Love | Richard Rodgers | 1943 | 0.64 | broadway |
Thanks for the Memory | Ralph Rainger | 1938 | 0.7 | film |
I Mean You | Thelonious Monk | 1946 | 0.62 | monk |
I'll Remember April | Gene de Paul | 1941 | 0.76 | songbook |
Dolphin Dance | Herbie Hancock | 1965 | 0.72 | modal |
April in Paris | Vernon Duke | 1932 | 0.82 | ballad |
Bluesette | Toots Thielemans | 1962 | 0.72 | waltz |
Hello, Young Lovers | Richard Rodgers | 1951 | 0.66 | waltz,broadway | Hello Young Lovers
Turn Out the Stars | Bill Evans | 1966 | 0.64 | ballad |
Just in Time | Jule Styne | 1956 | 0.7 | broadway |
C Jam Blues | Duke Ellington | 1942 | 0.76 | blues,ellington |
I Don't Stand a Ghost of a Chance with You | Victor Young | 1932 | 0.66 | ballad | Ghost of a Chance; I Don't Stand a Ghost of a Chance
If I Should Lose You | Ralph Rainger | 1935 | 0.68 | ballad,minor |
I Only Have Eyes for You | Harry Warren | 1934 | 0.8 | songbook,film |
Love Walked In | George Gershwin | 1938 | 0.64 | gershwin,film |
Bye Bye Blackbird | Ray Henderson | 1926 | 0.84 | songbook |
Manhattan | Richard Rodgers | 1925 | 0.66 | broadway |
Indiana | James F. Hanley | 1917 | 0.66 | swing | Back Home Again in Indiana
Lullaby of Birdland | George Shearing | 1952 | 0.86 | bebop |
A Foggy Day | George Gershwin | 1937 | 0.8 | gershwin,film | Foggy Day; A Foggy Day in London Town
But Not for Me | George Gershwin | 1930 | 0.82 | gershwin,broadway |
Beautiful Love | Victor Young | 1931 | 0.72 | minor,waltz |
Blue Bossa | Kenny Dorham | 1963 | 0.84 | bossa nova,latin |
Chelsea Bridge | Billy Strayhorn | 1941 | 0.7 | ballad,ellington |
Laura | David Raksin | 1944 | 0.84 | ballad,film |
Moon River | Henry Mancini | 1961 | 0.92 | waltz,film |
Emily | Johnny Mandel | 1964 | 0.7 | waltz,film |
Estate | Bruno Martino | 1960 | 0.62 | ballad,bossa nova |
My Romance | Richard Rodgers | 1935 | 0.72 | ballad,broadway |
My Ship | Kurt Weill | 1941 | 0.66 | ballad,broadway |
My Shining Hour | Harold Arlen | 1943 | 0.66 | film |
Nancy (with the Laughing Face) | Jimmy Van Heusen | 1942 | 0.64 | ballad | Nancy; Nancy With the Laughing Face
In Your Own Sweet Way | Dave Brubeck | 1955 | 0.74 | ballad |
Tenderly | Walter Gross | 1946 | 0.84 | ballad,waltz |
Secret Love | Sammy Fain | 1953 | 0.72 | film |
Time After Time | Jule Styne | 1947 | 0.7 | ballad,film |
Poor Butterfly | Raymond Hubbell | 1916 | 0.6 | ballad |
Like Someone in Love | Jimmy Van Heusen | 1944 | 0.76 | ballad,film |
Love Letters | Victor Young | 1945 | 0.66 | ballad,film |
Lover Man | Jimmy Davis | 1941 | 0.74 | ballad | Lover Man (Oh, Where Can You Be?)
Makin' Whoopee | Walter Donaldson | 1928 | 0.72 | songbook | Making Whoopee
Mean to Me | Fred E. Ahlert | 1929 | 0.64 | songbook |
Mona Lisa | Jay Livingston | 1950 | 0.8 | ballad,film |
My Old Flame | Arthur Johnston | 1934 | 0.66 | ballad |
Shiny Stockings | Frank Foster | 1955 | 0.66 | swing,basie |
Smile | Charlie Chaplin | 1936 | 0.84 | ballad,film |
Send in the Clowns | Stephen Sondheim | 1973 | 0.84 | ballad,broadway |
Unforgettable | Irving Gordon | 1951 | 0.86 | ballad |
When You Wish Upon a Star | Leigh Harline | 1940 | 0.88 | ballad,film,disney |
Who Can I Turn To? | Anthony Newley | 1964 | 0.66 | ballad,broadway | Who Can I Turn To
Witchcraft | Cy Coleman | 1957 | 0.74 | songbook |
Young and Foolish | Albert Hague | 1954 | 0.58 | ballad,broadway |
You Took Advantage of Me | Richard Rodgers | 1928 | 0.62 | broadway |
You Go to My Head | J. Fred Coots | 1938 | 0.72 | ballad |
You'd Be So Nice to Come Home To | Cole Porter | 1943 | 0.74 | porter,film |
Pure Imagination | Leslie Bricusse | 1971 | 0.84 | film |
The Shadow of Your Smile | Johnny Mandel | 1965 | 0.8 | bossa nova,film | Shadow of Your Smile
Moonlight Serenade | Glenn Miller | 1939 | 0.86 | ballad,swing |
Stormy Weather | Harold Arlen | 1933 | 0.82 | ballad,blues |
Sweet Lorraine | Cliff Burwell | 1928 | 0.72 | ballad |
The More I See You | Harry Warren | 1945 | 0.72 | film | More I See You
Cry Me a River | Arthur Hamilton | 1953 | 0.84 | ballad |
Desafinado | Antônio Carlos Jobim | 1959 | 0.84 | bossa nova |
Manhã de Carnaval (Black Orpheus) | Luiz Bonfá | 1959 | 0.84 | bossa nova,film | Manha de Carnaval; Black Orpheus; Carnival; A Day in the Life of a Fool
Milestones | Miles Davis | 1958 | 0.76 | modal |
Spain | Chick Corea | 1971 | 0.84 | latin,fusion |
I Remember Clifford | Benny Golson | 1957 | 0.74 | ballad,hard bop |
Good Bait | Tadd Dameron | 1944 | 0.64 | bebop |
Soul Eyes | Mal Waldron | 1957 | 0.68 | ballad |
The Peacocks | Jimmy Rowles | 1975 | 0.66 | ballad | Peacocks
Very Early | Bill Evans | 1962 | 0.66 | waltz |
Moon and Sand | Alec Wilder | 1941 | 0.58 | bossa nova |
Some Other Time | Leonard Bernstein | 1944 | 0.66 | ballad,broadway |
Recorda Me | Joe Henderson | 1963 | 0.68 | latin,hard bop | Recordame
Dearly Beloved | Jerome Kern | 1942 | 0.66 | film |
If I Were a Bell | Frank Loesser | 1950 | 0.72 | broadway |
Easy Living | Ralph Rainger | 1937 | 0.68 | ballad,film |
There Is No Greater Love | Isham Jones | 1936 | 0.72 | songbook |
I Fall in Love Too Easily | Jule Styne | 1944 | 0.7 | ballad,film |
I Thought About You | Jimmy Van Heusen | 1939 | 0.68 | songbook |
You Stepped Out of a Dream | Nacio Herb Brown | 1940 | 0.66 | film |
It's Easy to Remember | Richard Rodgers | 1935 | 0.6 | ballad,film |
Love Is Here to Stay | George Gershwin | 1938 | 0.8 | gershwin,film | Our Love Is Here to Stay
Pent-Up House | Sonny Rollins | 1956 | 0.58 | hard bop | Pent Up House
Up Jumped Spring | Freddie Hubbard | 1962 | 0.6 | waltz |
Isfahan | Billy Strayhorn | 1964 | 0.62 | ballad,ellington |
Blame It on My Youth | Oscar Levant | 1934 | 0.62 | ballad |
Dancing on the Ceiling | Richard Rodgers | 1930 | 0.6 | broadway |
Deep Purple | Peter DeRose | 1933 | 0.66 | ballad |
Exactly Like You | Jimmy McHugh | 1930 | 0.64 | swing |
I Could Write a Book | Richard Rodgers | 1940 | 0.62 | broadway |
I Concentrate on You | Cole Porter | 1939 | 0.62 | porter |
It's the Talk of the Town | Jerry Livingston | 1933 | 0.6 | ballad | Talk of the Town
I've Grown Accustomed to Her Face | Frederick Loewe | 1956 | 0.66 | ballad,broadway | I've Grown Accustomed to Your Face
Just You, Just Me | Jesse Greer | 1929 | 0.64 | swing | Just You Just Me
Oh, Lady Be Good! | George Gershwin | 1924 | 0.78 | gershwin,swing | Lady Be Good; Oh Lady Be Good
Almost Like Being in Love | Frederick Loewe | 1947 | 0.68 | broadway | It's Almost Like Being in Love
Ol' Man River | Jerome Kern | 1927 | 0.8 | broadway | Old Man River
Sonnymoon for Two | Sonny Rollins | 1958 | 0.58 | blues |
Star Eyes | Gene de Paul | 1943 | 0.62 | latin,bebop |
The End of a Love Affair | Edward Redding | 1950 | 0.58 | ballad |
The Masquerade Is Over | Allie Wrubel | 1938 | 0.58 | ballad |
The Summer Wind | Henry Mayer | 1965 | 0.7 | ballad |
Try a Little Tenderness | Harry Woods | 1932 | 0.76 | ballad |
What Is There to Say? | Vernon Duke | 1933 | 0.56 | ballad | What Is There to Say
While We're Young | Alec Wilder | 1943 | 0.56 | ballad |
Hymn to Freedom | Oscar Peterson | 1962 | 0.72 | gospel |
Cast Your Fate to the Wind | Vince Guaraldi | 1962 | 0.7 | |
The Folks Who Live on the Hill | Jerome Kern | 1937 | 0.56 | ballad,film |
A Sleepin' Bee | Harold Arlen | 1954 | 0.58 | broadway | A Sleeping Bee; Sleepin' Bee
A Fine Romance | Jerome Kern | 1936 | 0.72 | film |
A Nightingale Sang in Berkeley Square | Manning Sherwin | 1939 | 0.72 | ballad |
Alfie | Burt Bacharach | 1966 | 0.72 | ballad,film |
A Beautiful Friendship | Donald Kahn | 1956 | 0.6 | songbook |
It Don't Mean a Thing (If It Ain't Got That Swing) | Duke Ellington | 1931 | 0.84 | swing,ellington | It Don't Mean a Thing
Autumn in New York | Vernon Duke | 1934 | 0.84 | ballad |
I Love You | Cole Porter | 1944 | 0.62 | porter,broadway |
All the Way | Jimmy Van Heusen | 1957 | 0.66 | ballad,film |
Mad About the Boy | Noël Coward | 1932 | 0.64 | ballad |
My Blue Heaven | Walter Donaldson | 1927 | 0.62 | songbook |
Rosetta | Earl Hines | 1933 | 0.6 | swing |
Squeeze Me | Fats Waller | 1925 | 0.6 | stride |
Tangerine | Victor Schertzinger | 1941 | 0.62 | film |
Teach Me Tonight | Gene de Paul | 1953 | 0.66 | ballad |
The Party's Over | Jule Styne | 1956 | 0.6 | ballad,broadway |
The Thrill Is Gone | Ray Henderson | 1931 | 0.56 | ballad |
What a Difference a Day Made | María Grever | 1934 | 0.74 | ballad,latin | What a Diff'rence a Day Makes; What a Difference a Day Makes
When Lights Are Low | Benny Carter | 1936 | 0.56 | swing |
Wrap Your Troubles in Dreams | Harry Barris | 1931 | 0.56 | songbook |
You Must Believe in Spring | Michel Legrand | 1967 | 0.66 | ballad,film |
Goodbye | Gordon Jenkins | 1935 | 0.62 | ballad |
Lulu's Back in Town | Harry Warren | 1935 | 0.6 | swing |
Don't Get Around Much Anymore | Duke Ellington | 1940 | 0.84 | swing,ellington |
In a Mellow Tone | Duke Ellington | 1939 | 0.72 | swing,ellington | In a Mellotone
Perdido | Juan Tizol | 1941 | 0.72 | swing,ellington |
Cotton Tail | Duke Ellington | 1940 | 0.64 | ellington,rhythm changes |
Do Nothin' Till You Hear from Me | Duke Ellington | 1943 | 0.7 | ellington | Do Nothing Till You Hear from Me
Black and Tan Fantasy | Duke Ellington | 1927 | 0.6 | blues,ellington |
Creole Love Call | Duke Ellington | 1927 | 0.58 | ellington |
Single Petal of a Rose | Duke Ellington | 1959 | 0.56 | ballad,ellington | A Single Petal of a Rose
Melancholia | Duke Ellington | 1953 | 0.54 | ballad,ellington |
Reflections in D | Duke Ellington | 1953 | 0.56 | ballad,ellington |
Azure | Duke Ellington | 1937 | 0.54 | ballad,ellington |
Upper Manhattan Medical Group | Billy Strayhorn | 1956 | 0.56 | ellington |
Passion Flower | Billy Strayhorn | 1944 | 0.6 | ballad,ellington |
Johnny Come Lately | Billy Strayhorn | 1942 | 0.54 | ellington |
Lotus Blossom | Billy Strayhorn | 1946 | 0.58 | ballad,ellington |
Blood Count | Billy Strayhorn | 1967 | 0.54 | ballad,ellington |
Ornithology | Charlie Parker | 1946 | 0.78 | bebop |
Anthropology | Charlie Parker | 1946 | 0.7 | bebop,rhythm changes |
Donna Lee | Charlie Parker | 1947 | 0.76 | bebop |
Scrapple from the Apple | Charlie Parker | 1947 | 0.66 | bebop |
Billie's Bounce | Charlie Parker | 1945 | 0.7 | blues,bebop |
Now's the Time | Charlie Parker | 1945 | 0.7 | blues,bebop |
Yardbird Suite | Charlie Parker | 1946 | 0.7 | bebop |
Au Privave | Charlie Parker | 1951 | 0.6 | blues,bebop |
Blues for Alice | Charlie Parker | 1951 | 0.66 | blues,bebop |
Parker's Mood | Charlie Parker | 1948 | 0.62 | blues,bebop |
A Night in Tunisia | Dizzy Gillespie | 1942 | 0.88 | bebop,latin | Night in Tunisia
Groovin' High | Dizzy Gillespie | 1945 | 0.66 | bebop |
Bags' Groove | Milt Jackson | 1952 | 0.66 | blues |
Footprints | Wayne Shorter | 1966 | 0.82 | modal,waltz |
Speak No Evil | Wayne Shorter | 1964 | 0.64 | hard bop |
Infant Eyes | Wayne Shorter | 1964 | 0.62 | ballad |
Moment's Notice | John Coltrane | 1957 | 0.68 | hard bop |
Impressions | John Coltrane | 1961 | 0.7 | modal |
Mr. P.C. | John Coltrane | 1959 | 0.7 | blues | Mr PC
Equinox | John Coltrane | 1960 | 0.62 | blues,modal |
Lazy Bird | John Coltrane | 1957 | 0.6 | hard bop |
Central Park West | John Coltrane | 1960 | 0.6 | ballad |
Lonnie's Lament | John Coltrane | 1964 | 0.54 | modal |
Cantaloupe Island | Herbie Hancock | 1964 | 0.84 | modal,funk |
Watermelon Man | Herbie Hancock | 1962 | 0.84 | funk,blues |
Speak Like a Child | Herbie Hancock | 1968 | 0.62 | |
Song for My Father | Horace Silver | 1964 | 0.86 | latin,hard bop |
Peace | Horace Silver | 1959 | 0.62 | ballad |
Nica's Dream | Horace Silver | 1956 | 0.64 | latin,hard bop |
Doodlin' | Horace Silver | 1954 | 0.6 | blues |
Señor Blues | Horace Silver | 1956 | 0.62 | blues,latin |
St. Thomas | Sonny Rollins | 1956 | 0.84 | calypso | Saint Thomas; St Thomas
Doxy | Sonny Rollins | 1954 | 0.66 | hard bop |
Airegin | Sonny Rollins | 1954 | 0.64 | hard bop |
Four | Miles Davis | 1954 | 0.68 | bebop |
Tune Up | Miles Davis | 1953 | 0.64 | bebop |
All Blues | Miles Davis | 1959 | 0.84 | blues,modal,waltz,kind of blue |
Freddie Freeloader | Miles Davis | 1959 | 0.72 | blues,kind of blue |
Moanin' | Bobby Timmons | 1958 | 0.82 | hard bop,gospel | Moanin
Dat Dere | Bobby Timmons | 1960 | 0.58 | hard bop |
Work Song | Nat Adderley | 1960 | 0.7 | hard bop |
Mercy, Mercy, Mercy | Joe Zawinul | 1966 | 0.74 | soul jazz | Mercy Mercy Mercy
Jordu | Duke Jordan | 1953 | 0.62 | bebop |
Un Poco Loco | Bud Powell | 1951 | 0.66 | bebop,latin |
Tempus Fugit | Bud Powell | 1949 | 0.6 | bebop | Tempus Fugue-it
Parisian Thoroughfare | Bud Powell | 1951 | 0.6 | bebop |
Celia | Bud Powell | 1949 | 0.54 | bebop |
Epistrophy | Thelonious Monk | 1941 | 0.74 | monk |
Crepuscule with Nellie | Thelonious Monk | 1957 | 0.66 | ballad,monk |
Ugly Beauty | Thelonious Monk | 1967 | 0.58 | waltz,monk |
Criss Cross | Thelonious Monk | 1951 | 0.56 | monk |
Brilliant Corners | Thelonious Monk | 1956 | 0.6 | monk |
Off Minor | Thelonious Monk | 1947 | 0.6 | monk |
Bemsha Swing | Thelonious Monk | 1952 | 0.62 | monk |
Hackensack | Thelonious Monk | 1954 | 0.56 | monk |
Nutty | Thelonious Monk | 1954 | 0.58 | monk |
Light Blue | Thelonious Monk | 1958 | 0.54 | monk |
Little Rootie Tootie | Thelonious Monk | 1952 | 0.56 | monk |
Jackie-ing | Thelonious Monk | 1959 | 0.54 | monk | Jackieing; Jackie-ing
Blue Rondo à la Turk | Dave Brubeck | 1959 | 0.8 | 9/8 |
The Duke | Dave Brubeck | 1955 | 0.56 | |
Killer Joe | Benny Golson | 1960 | 0.66 | hard bop |
Stablemates | Benny Golson | 1955 | 0.6 | hard bop |
Along Came Betty | Benny Golson | 1958 | 0.6 | hard bop |
Lady Bird | Tadd Dameron | 1939 | 0.6 | bebop |
Hot House | Tadd Dameron | 1945 | 0.6 | bebop |
Bolivia | Cedar Walton | 1973 | 0.62 | hard bop |
Firm Roots | Cedar Walton | 1974 | 0.56 | hard bop |
Mosaic | Cedar Walton | 1961 | 0.56 | hard bop |
Ojos de Rojo | Cedar Walton | 1976 | 0.56 | latin |
Fantasy in D | Cedar Walton | 1963 | 0.56 | hard bop | Ugetsu
Wave | Antônio Carlos Jobim | 1967 | 0.82 | bossa nova |
How Insensitive | Antônio Carlos Jobim | 1963 | 0.8 | bossa nova | Insensatez
Triste | Antônio Carlos Jobim | 1967 | 0.62 | bossa nova |
One Note Samba | Antônio Carlos Jobim | 1960 | 0.76 | bossa nova | Samba de Uma Nota So
Meditation | Antônio Carlos Jobim | 1960 | 0.7 | bossa nova | Meditacao
Once I Loved | Antônio Carlos Jobim | 1960 | 0.62 | bossa nova | O Amor em Paz
Dindi | Antônio Carlos Jobim | 1965 | 0.6 | bossa nova |
Chega de Saudade | Antônio Carlos Jobim | 1958 | 0.72 | bossa nova | No More Blues
Água de Beber | Antônio Carlos Jobim | 1961 | 0.66 | bossa nova |
Zingaro | Antônio Carlos Jobim | 1965 | 0.56 | bossa nova | Retrato em Branco e Preto
Armando's Rumba | Chick Corea | 1976 | 0.62 | latin |
Windows | Chick Corea | 1966 | 0.6 | waltz |
Crystal Silence | Chick Corea | 1972 | 0.6 | ballad |
La Fiesta | Chick Corea | 1972 | 0.6 | latin |
500 Miles High | Chick Corea | 1972 | 0.6 | |
My Song | Keith Jarrett | 1978 | 0.66 | ballad |
Passion Dance | McCoy Tyner | 1967 | 0.58 | modal |
Contemplation | McCoy Tyner | 1967 | 0.54 | modal |
Search for Peace | McCoy Tyner | 1967 | 0.56 | ballad |
Effendi | McCoy Tyner | 1962 | 0.54 | modal |
Mannenberg | Abdullah Ibrahim | 1974 | 0.6 | cape jazz | Manenberg
The Wedding | Abdullah Ibrahim | 1980 | 0.56 | ballad,cape jazz | Wedding
Looking Up | Michel Petrucciani | 1989 | 0.54 | |
Les Grelots | Michel Petrucciani | 1984 | 0.5 | |
The Windmills of Your Mind | Michel Legrand | 1968 | 0.78 | film | Windmills of Your Mind
What Are You Doing the Rest of Your Life? | Michel Legrand | 1969 | 0.66 | ballad,film | What Are You Doing the Rest of Your Life
Watch What Happens | Michel Legrand | 1964 | 0.62 | bossa nova,film |
I Will Wait for You | Michel Legrand | 1964 | 0.66 | ballad,film |
A Time for Love | Johnny Mandel | 1966 | 0.62 | ballad,film | Time for Love
A House Is Not a Home | Burt Bacharach | 1964 | 0.62 | ballad |
The Look of Love | Burt Bacharach | 1967 | 0.72 | bossa nova,film |
Linus and Lucy | Vince Guaraldi | 1964 | 0.86 | tv |
Danny Boy | Traditional | 1913 | 0.8 | ballad,folk | Londonderry Air
Shenandoah | Traditional | 1820 | 0.62 | folk |
Love Is a Many-Splendored Thing | Sammy Fain | 1955 | 0.62 | ballad,film | Love Is a Many Splendored Thing
Everything I Have Is Yours | Burton Lane | 1933 | 0.56 | ballad |
I'll Be Seeing You | Sammy Fain | 1938 | 0.8 | ballad | Ill Be Seeing You
Nice Work If You Can Get It | George Gershwin | 1937 | 0.74 | gershwin,film | Nice Work
'S Wonderful | George Gershwin | 1927 | 0.78 | gershwin,broadway | S Wonderful
Fascinating Rhythm | George Gershwin | 1924 | 0.72 | gershwin,broadway |
How Long Has This Been Going On? | George Gershwin | 1927 | 0.62 | ballad,gershwin | How Long Has This Been Going On
Liza | George Gershwin | 1929 | 0.6 | gershwin |
Bess, You Is My Woman Now | George Gershwin | 1935 | 0.66 | porgy and bess,gershwin | Bess You Is My Woman; Bess You Is My Woman Now
It Ain't Necessarily So | George Gershwin | 1935 | 0.72 | porgy and bess,gershwin | It Ain't Necessarily So
My Man's Gone Now | George Gershwin | 1935 | 0.6 | porgy and bess,gershwin |
Every Time We Say Goodbye | Cole Porter | 1944 | 0.76 | ballad,porter | Ev'ry Time We Say Goodbye; Everytime We Say Goodbye
So in Love | Cole Porter | 1948 | 0.64 | porter,broadway |
It's All Right with Me | Cole Porter | 1953 | 0.64 | porter,broadway |
From This Moment On | Cole Porter | 1950 | 0.62 | porter |
Love Me or Leave Me | Walter Donaldson | 1928 | 0.66 | songbook |
Ain't She Sweet | Milton Ager | 1927 | 0.66 | songbook |
Sweet Sue, Just You | Victor Young | 1928 | 0.54 | songbook | Sweet Sue
I'm Beginning to See the Light | Duke Ellington | 1944 | 0.66 | swing,ellington |
I'm in the Mood for Love | Jimmy McHugh | 1935 | 0.82 | ballad | Im in the Mood for Love
Moonglow | Will Hudson | 1933 | 0.72 | ballad | Moon Glow
Sentimental Journey | Les Brown | 1944 | 0.7 | swing |
Lady Is a Tramp | Richard Rodgers | 1937 | 0.76 | broadway | The Lady Is a Tramp
Blue Moon | Richard Rodgers | 1934 | 0.86 | ballad |
Falling in Love with Love | Richard Rodgers | 1938 | 0.66 | waltz,broadway |
It's You or No One | Jule Styne | 1948 | 0.56 | film |
Everything Must Change | Benard Ighner | 1974 | 0.56 | ballad |
God Bless the Child | Billie Holiday | 1941 | 0.74 | ballad,blues |
Good Morning Heartache | Irene Higginbotham | 1946 | 0.64 | ballad |
Lullaby of the Leaves | Bernice Petkere | 1932 | 0.54 | |
I'll Close My Eyes | Billy Reid | 1945 | 0.56 | ballad |
Ballad of the Sad Young Men | Tommy Wolf | 1959 | 0.54 | ballad | The Ballad of the Sad Young Men
Spring Can Really Hang You Up the Most | Tommy Wolf | 1955 | 0.58 | ballad |
Detour Ahead | Herb Ellis | 1947 | 0.56 | ballad |
Gravy Waltz | Ray Brown | 1963 | 0.52 | waltz |
Strollin' | Horace Silver | 1960 | 0.56 | hard bop | Strollin
Almost Blue | Elvis Costello | 1982 | 0.6 | ballad |
Chances Are | Robert Allen | 1957 | 0.6 | ballad |
Girl Talk | Neal Hefti | 1965 | 0.6 | swing |
Unchain My Heart | Bobby Sharp | 1961 | 0.6 | |
Nature Boy | Eden Ahbez | 1947 | 0.74 | ballad |
Imagination | Jimmy Van Heusen | 1940 | 0.62 | ballad |
Here's to Life | Artie Butler | 1992 | 0.6 | ballad |
How Do You Keep the Music Playing? | Michel Legrand | 1982 | 0.56 | ballad,film | How Do You Keep the Music Playing
For Once in My Life | Ron Miller | 1965 | 0.66 | |
Wild Is the Wind | Dimitri Tiomkin | 1957 | 0.6 | ballad,film |
Two for the Road | Henry Mancini | 1967 | 0.58 | film |
Sometime Ago | Sergio Mihanovich | 1962 | 0.52 | waltz |
Since I Fell for You | Buddy Johnson | 1945 | 0.6 | ballad,blues |
Exit Music (For a Film) | Radiohead | 1997 | 0.66 | modern standard | Exit Music
River Man | Nick Drake | 1969 | 0.58 | modern standard |
Knives Out | Radiohead | 2001 | 0.56 | modern standard |
Pastime Paradise | Stevie Wonder | 1976 | 0.6 | modern standard |
Isn't She Lovely | Stevie Wonder | 1976 | 0.72 | modern standard |
Sir Duke | Stevie Wonder | 1976 | 0.66 | modern standard |
You Are the Sunshine of My Life | Stevie Wonder | 1972 | 0.72 | modern standard |
Here, There and Everywhere | John Lennon and Paul McCartney | 1966 | 0.7 | modern standard | Here There and Everywhere
Yesterday | John Lennon and Paul McCartney | 1965 | 0.86 | modern standard |
Eleanor Rigby | John Lennon and Paul McCartney | 1966 | 0.78 | modern standard |
Michelle | John Lennon and Paul McCartney | 1965 | 0.7 | modern standard |
And I Love Her | John Lennon and Paul McCartney | 1964 | 0.7 | modern standard |
Both Sides Now | Joni Mitchell | 1967 | 0.7 | modern standard |
A Case of You | Joni Mitchell | 1971 | 0.6 | modern standard |
New York State of Mind | Billy Joel | 1976 | 0.7 | modern standard |
Smells Like Teen Spirit | Nirvana | 1991 | 0.6 | modern standard |
But Beautiful | Jimmy Van Heusen | 1947 | 0.72 | ballad,film |
Call Me Irresponsible | Jimmy Van Heusen | 1962 | 0.64 | film |
Can't We Be Friends? | Kay Swift | 1929 | 0.56 | songbook | Can't We Be Friends
East of the Sun (and West of the Moon) | Brooks Bowman | 1934 | 0.66 | songbook | East of the Sun
Fools Rush In | Rube Bloom | 1940 | 0.64 | ballad |
For All We Know | J. Fred Coots | 1934 | 0.64 | ballad |
Getting to Know You | Richard Rodgers | 1951 | 0.6 | broadway |
I'll Be Around | Alec Wilder | 1942 | 0.56 | ballad |
I'm a Fool to Want You | Jack Wolf | 1951 | 0.64 | ballad |
In the Wee Small Hours of the Morning | David Mann | 1955 | 0.68 | ballad | In the Wee Small Hours
Isn't It a Pity? | George Gershwin | 1933 | 0.52 | gershwin | Isn't It a Pity
Isn't This a Lovely Day? | Irving Berlin | 1935 | 0.6 | film | Isn't This a Lovely Day
I've Got a Crush on You | George Gershwin | 1928 | 0.68 | gershwin,ballad |
I've Never Been in Love Before | Frank Loesser | 1950 | 0.56 | broadway |
Just a Gigolo | Leonello Casucci | 1929 | 0.62 | songbook |
Let's Fall in Love | Harold Arlen | 1933 | 0.62 | film |
Long Ago (and Far Away) | Jerome Kern | 1944 | 0.62 | film | Long Ago and Far Away
Look for the Silver Lining | Jerome Kern | 1920 | 0.6 | broadway |
Love You Madly | Duke Ellington | 1950 | 0.5 | ellington |
On a Clear Day (You Can See Forever) | Burton Lane | 1965 | 0.64 | broadway | On a Clear Day
One for My Baby (and One More for the Road) | Harold Arlen | 1943 | 0.7 | ballad,film | One for My Baby
People | Jule Styne | 1964 | 0.64 | broadway |
September in the Rain | Harry Warren | 1937 | 0.62 | film |
She's Funny That Way | Neil Moret | 1928 | 0.54 | songbook |
Stairway to the Stars | Matty Malneck | 1935 | 0.56 | ballad |
Stars Fell on Alabama | Frank Perkins | 1934 | 0.66 | ballad |
On the Sunny Side of the Street | Jimmy McHugh | 1930 | 0.8 | swing | Sunny Side of the Street; Sunny Side
That's All | Bob Haymes | 1952 | 0.6 | ballad |
There's a Small Hotel | Richard Rodgers | 1936 | 0.6 | broadway |
Till There Was You | Meredith Willson | 1957 | 0.66 | broadway |
Too Late Now | Burton Lane | 1951 | 0.52 | ballad,film |
Try to Remember | Harvey Schmidt | 1960 | 0.64 | ballad,broadway |
We'll Be Together Again | Carl Fischer | 1945 | 0.56 | ballad |
What Kind of Fool Am I? | Anthony Newley | 1961 | 0.62 | broadway | What Kind of Fool Am I
Where Is Love? | Lionel Bart | 1960 | 0.6 | ballad,broadway | Where Is Love
Why Do I Love You? | Jerome Kern | 1927 | 0.5 | broadway | Why Do I Love You
With a Song in My Heart | Richard Rodgers | 1929 | 0.62 | broadway |
You and the Night and the Music | Arthur Schwartz | 1934 | 0.62 | broadway |
You Don't Know Me | Cindy Walker | 1955 | 0.64 | ballad |
You'll Never Know | Harry Warren | 1943 | 0.62 | ballad,film |
Younger Than Springtime | Richard Rodgers | 1949 | 0.56 | ballad,broadway |
Alice in Wonderland | Sammy Fain | 1951 | 0.6 | waltz,film,disney |
A Sunday Kind of Love | Barbara Belle | 1946 | 0.56 | ballad |
A Kiss to Build a Dream On | Harry Ruby | 1935 | 0.62 | ballad,film | Kiss to Build a Dream On
An Affair to Remember | Harry Warren | 1957 | 0.6 | ballad,film |
Answer Me, My Love | Gerhard Winkler | 1953 | 0.5 | ballad | Answer Me My Love; Answer Me
Bésame Mucho | Consuelo Velázquez | 1940 | 0.78 | bolero,latin |
The Boy Next Door | Hugh Martin | 1944 | 0.56 | film | Boy Next Door
Close Enough for Love | Johnny Mandel | 1979 | 0.5 | ballad,film |
Crazy He Calls Me | Carl Sigman | 1949 | 0.5 | ballad |
Do You Know What It Means to Miss New Orleans? | Louis Alter | 1946 | 0.6 | new orleans | Do You Know What It Means to Miss New Orleans
Easy Does It | Sy Oliver | 1940 | 0.48 | swing |
Flamingo | Ted Grouya | 1941 | 0.56 | ballad |
For Sentimental Reasons | William Best | 1945 | 0.58 | ballad |
Guess I'll Hang My Tears Out to Dry | Jule Styne | 1944 | 0.5 | ballad |
The Heather on the Hill | Frederick Loewe | 1947 | 0.48 | broadway | Heather on the Hill
Hi-Lili, Hi-Lo | Bronisław Kaper | 1953 | 0.5 | waltz,film | Hi Lili Hi Lo; Hi Lili
I Didn't Know About You | Duke Ellington | 1944 | 0.5 | ballad,ellington |
I Guess I'll Have to Change My Plan | Arthur Schwartz | 1929 | 0.52 | broadway |
I Have Dreamed | Richard Rodgers | 1951 | 0.5 | ballad,broadway |
I Must Have That Man | Jimmy McHugh | 1928 | 0.48 | songbook |
I Want to Be Happy | Vincent Youmans | 1924 | 0.6 | broadway |
I Wish I Knew | Harry Warren | 1945 | 0.56 | ballad |
I Wish You Love | Charles Trenet | 1942 | 0.66 | ballad | Que reste-t-il de nos amours
If Ever I Would Leave You | Frederick Loewe | 1960 | 0.58 | ballad,broadway | If Ever I Should Leave You
I'm Glad There Is You | Jimmy Dorsey | 1941 | 0.52 | ballad |
I'm Through with Love | Matty Malneck | 1931 | 0.52 | ballad |
New Orleans | Hoagy Carmichael | 1932 | 0.52 | new orleans |
Night Mist Blues | Ahmad Jamal | 1965 | 0.45 | blues |
Once in a While | Michael Edwards | 1937 | 0.54 | ballad |
Remember | Irving Berlin | 1925 | 0.58 | waltz |
Save Your Love for Me | Buddy Johnson | 1956 | 0.5 | ballad,blues |
Serenata | Leroy Anderson | 1947 | 0.5 | latin |
Something Cool | Billy Barnes | 1954 | 0.42 | ballad |
Strange Meadowlark | Dave Brubeck | 1959 | 0.5 | ballad | Strange Meadow Lark
The Surrey with the Fringe on Top | Richard Rodgers | 1943 | 0.6 | broadway | Surrey with the Fringe on Top; The Surrey
The Lies of Handsome Men | Cy Coleman | 1964 | 0.4 | ballad |
The Second Time Around | Jimmy Van Heusen | 1960 | 0.54 | ballad,film |
The Shining Sea | Johnny Mandel | 1966 | 0.48 | ballad,film |
The Things We Did Last Summer | Jule Styne | 1946 | 0.56 | ballad |
There's a Boat That's Leavin' Soon for New York | George Gershwin | 1935 | 0.5 | porgy and bess,gershwin | There's a Boat That's Leaving
They Didn't Believe Me | Jerome Kern | 1914 | 0.52 | broadway |
They Say It's Wonderful | Irving Berlin | 1946 | 0.56 | broadway |
This Nearly Was Mine | Richard Rodgers | 1949 | 0.52 | waltz,broadway |
This Time the Dream's on Me | Harold Arlen | 1941 | 0.56 | film |
Too Young to Go Steady | Jimmy McHugh | 1955 | 0.48 | ballad |
Yours Is My Heart Alone | Franz Lehár | 1929 | 0.5 | ballad |
Give Me the Simple Life | Rube Bloom | 1945 | 0.56 | swing | The Simple Life
Come Rain or Come Shine | Harold Arlen | 1946 | 0.76 | ballad,broadway |
By Myself | Arthur Schwartz | 1937 | 0.58 | broadway |
Close Your Eyes | Bernice Petkere | 1933 | 0.54 | ballad |
Dreamsville | Henry Mancini | 1959 | 0.5 | ballad,tv |
Falling Grace | Steve Swallow | 1962 | 0.5 | |
I Never Knew | Ted Fio Rito | 1925 | 0.52 | swing |
Make Someone Happy | Jule Styne | 1960 | 0.56 | broadway |
Once Upon a Summertime | Michel Legrand | 1954 | 0.56 | ballad,film |
Penthouse Serenade | Will Jason | 1931 | 0.5 | ballad |
Lover, Come Back to Me | Sigmund Romberg | 1928 | 0.66 | broadway | Lover Come Back to Me; Lover Come Back
Manteca | Dizzy Gillespie | 1947 | 0.62 | latin,bebop |
That Old Devil Called Love | Doris Fisher | 1944 | 0.52 | ballad |
The Man That Got Away | Harold Arlen | 1954 | 0.62 | ballad,film | Man That Got Away
Up with the Lark | Jerome Kern | 1946 | 0.42 | film |
Soon | George Gershwin | 1929 | 0.54 | gershwin |
Something Wonderful | Richard Rodgers | 1951 | 0.48 | broadway |
Cubano Chant | Ray Bryant | 1956 | 0.52 | latin |
Blue Room | Richard Rodgers | 1926 | 0.54 | broadway | The Blue Room
Somewhere | Leonard Bernstein | 1957 | 0.8 | ballad,broadway,west side story |
Ladies in Mercedes | Steve Swallow | 1979 | 0.4 | |
Cinema Paradiso | Ennio Morricone | 1988 | 0.6 | film |
Ave Maria (Bach-Gounod) | Charles Gounod | 1859 | 0.6 | sacred | Ave Maria
Baubles, Bangles and Beads | Robert Wright and George Forrest | 1953 | 0.56 | broadway | Baubles Bangles and Beads; Baubles
Dinah | Harry Akst | 1925 | 0.64 | songbook |
I Hadn't Anyone Till You | Ray Noble | 1938 | 0.56 | ballad |
Pennies from Heaven | Arthur Johnston | 1936 | 0.72 | songbook,film |
Without a Song | Vincent Youmans | 1929 | 0.66 | songbook |
I Can't Give You Anything but Love | Jimmy McHugh | 1928 | 0.74 | songbook | I Can't Give You Anything but Love, Baby
More Than You Know | Vincent Youmans | 1929 | 0.66 | ballad,broadway |
That Old Black Magic | Harold Arlen | 1942 | 0.74 | film |
Flying Home | Benny Goodman | 1939 | 0.62 | swing |
One O'Clock Jump | Count Basie | 1937 | 0.66 | swing,blues,basie |
Li'l Darlin' | Neal Hefti | 1958 | 0.6 | ballad,basie | Lil Darlin; Little Darlin
Humoresque | Antonín Dvořák | 1894 | 0.62 | classical theme |
St. James Infirmary | Traditional | 1928 | 0.66 | blues,new orleans | Saint James Infirmary
On a Slow Boat to China | Frank Loesser | 1948 | 0.58 | songbook | Slow Boat to China
I'll Never Smile Again | Ruth Lowe | 1939 | 0.56 | ballad |
Sleepy Lagoon | Eric Coates | 1930 | 0.52 | ballad |
Is You Is or Is You Ain't My Baby | Louis Jordan | 1944 | 0.58 | swing |
Crazy Rhythm | Joseph Meyer | 1928 | 0.54 | swing |
I'll See You in My Dreams | Isham Jones | 1924 | 0.56 | songbook |
Rose Room | Art Hickman | 1917 | 0.52 | swing |
Whispering | John Schonberger | 1920 | 0.54 | songbook |
Moonlight Becomes You | Jimmy Van Heusen | 1942 | 0.56 | ballad,film |
Wichita Lineman | Jimmy Webb | 1968 | 0.62 | modern standard |
God Only Knows | Brian Wilson | 1966 | 0.7 | modern standard |
Paranoid Android | Radiohead | 1997 | 0.56 | modern standard |
Be My Love | Nicholas Brodszky | 1950 | 0.6 | ballad,film |
My Wild Irish Rose | Chauncey Olcott | 1899 | 0.5 | ballad |
Midnight Mood | Joe Zawinul | 1967 | 0.45 | ballad |
Get Out of Town | Cole Porter | 1938 | 0.58 | porter,broadway |
A Cottage for Sale | Willard Robison | 1929 | 0.5 | ballad | Cottage for Sale
If I Could Be with You | James P. Johnson | 1926 | 0.56 | songbook | If I Could Be With You One Hour Tonight
Street of Dreams | Victor Young | 1932 | 0.52 | ballad |
My Melancholy Baby | Ernie Burnett | 1912 | 0.56 | ballad |
Rockin' Chair | Hoagy Carmichael | 1929 | 0.54 | ballad |
All Alone | Irving Berlin | 1924 | 0.5 | waltz |
I'm Just Wild About Harry | Eubie Blake | 1921 | 0.56 | songbook |
Invitation | Bronisław Kaper | 1950 | 0.6 | film,latin |
The Night We Called It a Day | Matt Dennis | 1941 | 0.52 | ballad |
The Night Has a Thousand Eyes | Jerry Brainin | 1948 | 0.56 | latin |
You're My Everything | Harry Warren | 1931 | 0.58 | songbook |
Out of This World | Harold Arlen | 1945 | 0.56 | film |
Last Night When We Were Young | Harold Arlen | 1935 | 0.52 | ballad |
The Meaning of the Blues | Bobby Troup | 1957 | 0.5 | ballad,blues |
After Hours | Avery Parrish | 1940 | 0.56 | blues |
Topsy | Edgar Battle | 1936 | 0.5 | swing |
Black and Blue | Fats Waller | 1929 | 0.58 | stride,songbook | What Did I Do to Be So Black and Blue; (What Did I Do to Be So) Black and Blue
Keepin' Out of Mischief Now | Fats Waller | 1932 | 0.56 | stride | Keeping Out of Mischief Now
Spring Will Be a Little Late This Year | Frank Loesser | 1944 | 0.5 | ballad |
Wouldn't It Be Loverly | Frederick Loewe | 1956 | 0.58 | broadway | Wouldn't It Be Loverly?
Goodbye Pork Pie Hat | Charles Mingus | 1959 | 0.66 | ballad,blues |
"""

# Titles the curated test-piano set already holds (normalised key -> its file); never staged again.
# Matched from the set's own file names at run time; this list only adds titles whose names differ.
EXISTING_EXTRA_KEYS: list[str] = []

# Pianists, most famous first within each tier: the take of the best-known pianist wins
# (a composer playing their own tune beats everyone). Everyone not named is tier C.
TIER_A = ["Bill Evans", "Art Tatum", "Oscar Peterson", "Thelonious Monk", "Keith Jarrett",
          "Erroll Garner", "Herbie Hancock", "Chick Corea", "Brad Mehldau", "McCoy Tyner",
          "Ahmad Jamal", "Hank Jones", "Tommy Flanagan", "Teddy Wilson", "Earl Hines", "Fred Hersch"]
TIER_B = ["George Shearing", "Kenny Barron", "Cedar Walton", "Michel Petrucciani", "Red Garland",
          "Phineas Newborn Jr", "Mary Lou Williams", "Lennie Tristano", "Marian McPartland",
          "Barry Harris", "Dave McKenna", "Dick Hyman", "Bill Charlap", "Mulgrew Miller",
          "Jaki Byard", "Roland Hanna", "André Previn", "Ray Bryant", "Junior Mance",
          "Monty Alexander", "Gonzalo Rubalcaba", "Hiromi", "Michel Camilo", "Abdullah Ibrahim",
          "Kenny Drew", "Ellis Larkins", "Ralph Sutton", "Denny Zeitlin", "Gene Harris",
          "Bobby Timmons", "Ramsey Lewis", "Eliane Elias", "Michel Legrand", "Benny Green",
          "John Hicks", "Harold Mabern", "Geri Allen", "Steve Kuhn", "Don Friedman", "Jason Moran",
          "Vijay Iyer", "Tigran Hamasyan", "Stanley Cowell", "Randy Weston", "Toshiko Akiyoshi",
          "Richard Beirach", "Kenny Werner", "Makoto Ozone", "Alan Broadbent", "George Cables",
          "Cyrus Chestnut", "Eric Reed", "Marcus Roberts", "Ellis Marsalis", "Jessica Williams",
          "Joanne Brackeen", "Renee Rosnes", "Ethan Iverson", "Joey Alexander", "Clare Fischer"]

# Composer -> pianist names that count as "the composer playing" (own-tune bonus).
COMPOSER_PIANISTS = {
    "Thelonious Monk": "Thelonious Monk", "Erroll Garner": "Erroll Garner", "Bill Evans": "Bill Evans",
    "Herbie Hancock": "Herbie Hancock", "Chick Corea": "Chick Corea", "George Shearing": "George Shearing",
    "Oscar Peterson": "Oscar Peterson", "Fats Waller": "Fats Waller", "Earl Hines": "Earl Hines",
    "Cedar Walton": "Cedar Walton", "Abdullah Ibrahim": "Abdullah Ibrahim", "McCoy Tyner": "McCoy Tyner",
    "Keith Jarrett": "Keith Jarrett", "Michel Petrucciani": "Michel Petrucciani",
    "Michel Legrand": "Michel Legrand", "Bobby Timmons": "Bobby Timmons", "Horace Silver": "Horace Silver",
    "Duke Jordan": "Duke Jordan", "Bud Powell": "Bud Powell", "Dave Brubeck": "Dave Brubeck",
    "Mal Waldron": "Mal Waldron", "Eubie Blake": "Eubie Blake", "Duke Ellington": "Duke Ellington",
    "Billy Strayhorn": "Billy Strayhorn", "Brad Mehldau": "Brad Mehldau",
}

# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------

def log(*a):
    print(*a, file=sys.stderr, flush=True)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


SUFFIX_RE = re.compile(
    r"\s+-\s+(live|remaster\w*|digital\w*|\d{4}|take|alternate|alt\.?|from|solo|version|mono|stereo|"
    r"rec\w*|bonus|single|edit|instrumental|piano).*$", re.I)


def norm(t: str) -> str:
    """Normalised match key: lower case, no accents/punctuation/articles/brackets/suffixes."""
    t = strip_accents(t).replace("’", "'").replace("‘", "'").replace("…", " ")
    t = SUFFIX_RE.sub("", t)
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = t.lower().replace("&", " and ")
    t = re.sub(r"'", "", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\b(the|a|an)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"(?<=[a-z]) [1-4]$", "", t)   # "Blue in Green 2" (a second take) -> "blue in green"


def slug(s: str) -> str:
    s = strip_accents(s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s[:80] or "x"


def parse_standards() -> list[dict]:
    out, seen = [], set()
    for line in STANDARDS_TABLE.strip().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        parts += [""] * (6 - len(parts))
        title, composer, year, rank, tags, aliases = parts[:6]
        year = "" if year in ("null", "None") else year
        k = norm(title)
        if k in seen:
            continue
        seen.add(k)
        keys = {k}
        if "(" in title:
            keys.add(norm(re.sub(r"\(.*?\)", "", title)))
        for a in filter(None, (x.strip() for x in aliases.split(";"))):
            keys.add(norm(a))
        out.append({"title": title, "composer": composer, "year": int(year) if year else None,
                    "rank": float(rank), "tags": [t for t in (x.strip() for x in tags.split(",")) if t],
                    "key": k, "keys": sorted(keys)})
    return out


def http_get(url: str, *, binary=True, retries=4, timeout=60, headers=None) -> bytes:
    h = {"User-Agent": UA}
    h.update(headers or {})
    delay = 2.0
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410, 403):
                raise
            log(f"  http {e.code} on {url}; retry {i + 1}")
        except Exception as e:  # noqa: BLE001 - network flakiness of any kind
            log(f"  {type(e).__name__} on {url}: {e}; retry {i + 1}")
        time.sleep(delay)
        delay *= 2
    raise RuntimeError(f"giving up on {url}")


_last_hit: dict[str, float] = {}


def polite_get(url: str, min_gap=1.2, **kw) -> bytes:
    host = urllib.parse.urlsplit(url).netloc
    wait = _last_hit.get(host, 0) + min_gap - time.time()
    if wait > 0:
        time.sleep(wait)
    try:
        return http_get(url, **kw)
    finally:
        _last_hit[host] = time.time()


def md5_file(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download_resumable(url: str, dest: Path, size: int | None = None, md5: str | None = None):
    """Download url to dest, resuming a partial file with a Range request; verify md5 when given."""
    if dest.exists() and md5 and md5_file(dest) == md5:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(5):
        have = part.stat().st_size if part.exists() else 0
        if size and have >= size:
            break
        headers = {"User-Agent": UA}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r, open(part, "ab" if have else "wb") as f:
                if have and r.status != 206:
                    f.seek(0)
                    f.truncate()
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            if not size:
                break
        except Exception as e:  # noqa: BLE001
            log(f"  download error {e}; retry {attempt + 1}")
            time.sleep(3 * (attempt + 1))
    if md5 and md5_file(part) != md5:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"md5 mismatch for {url}")
    part.replace(dest)


# ---------------------------------------------------------------------------------------------
# MIDI inspection and the few source-specific fixes
# ---------------------------------------------------------------------------------------------

def inspect_midi(data: bytes) -> dict:
    m = mido.MidiFile(file=io.BytesIO(data), clip=True)
    notes, vels, pedal, drums = 0, set(), 0, 0
    chans: dict[int, int] = {}
    progs: dict[int, int] = {}
    t = 0.0
    last = 0.0
    for msg in m:
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            notes += 1
            vels.add(msg.velocity)
            chans[msg.channel] = chans.get(msg.channel, 0) + 1
            if msg.channel == 9:
                drums += 1
            last = t
        elif msg.type == "control_change" and msg.control == 64:
            pedal += 1
        elif msg.type == "program_change":
            progs.setdefault(msg.channel, msg.program)
    names = [tr.name for tr in m.tracks if tr.name]
    return {"notes": notes, "duration_s": round(last, 1), "velocities": len(vels), "pedal": pedal,
            "channels": chans, "programs": progs, "drums": drums, "type": m.type,
            "ticks_per_beat": m.ticks_per_beat, "tracks": len(m.tracks), "track_names": names}


def is_piano_prog(p: int | None) -> bool:
    return p is None or 0 <= p <= 7


BAND_WORDS = ("bass", "drum", "kit", "perc", "string", "sax", "gtr", "guitar", "vox", "vocal", "horn",
              "trumpet", "flute", "brass", "organ", "vibe")


def classify_channels(m: mido.MidiFile) -> dict[int, str]:
    """Label each note channel 'piano', 'drums', 'bass' or 'other' (program, track name, and for
    unlabelled channels next to others: a low, mostly single-note line counts as bass)."""
    progs: dict[int, int] = {}
    names: dict[int, str] = {}
    pitches: dict[int, list[int]] = {}
    for tr in m.tracks:
        tname = (tr.name or "").lower()
        for msg in tr:
            if msg.type == "program_change":
                progs.setdefault(msg.channel, msg.program)
            if msg.type == "note_on" and msg.velocity > 0:
                pitches.setdefault(msg.channel, []).append(msg.note)
                if tname:
                    names.setdefault(msg.channel, tname)
    out = {}
    for ch, ps in pitches.items():
        nm = names.get(ch, "")
        if ch == 9:
            out[ch] = "drums"
        elif not is_piano_prog(progs.get(ch)) or any(w in nm for w in BAND_WORDS):
            out[ch] = "bass" if (32 <= progs.get(ch, 0) <= 39 or "bass" in nm) else "other"
        else:
            out[ch] = "piano"
    piano = [ch for ch, k in out.items() if k == "piano"]
    if len(piano) > 1:
        for ch in piano:
            ps = sorted(pitches[ch])
            if ps[len(ps) // 2] < 45 and ps[-1] < 60 and len(piano) > 1:
                out[ch] = "bass"
    return out


def reduce_to_piano(data: bytes) -> bytes | None:
    """For a McKenzie trio/group file: one piano. Drums and other instruments are dropped; the piano
    channels and the bass line (played by the piano's left hand, slightly softer) are merged onto
    channel 0 with program 0; sustain/soft/sostenuto kept. None when fewer than 16 piano notes."""
    m = mido.MidiFile(file=io.BytesIO(data), clip=True)
    kinds = classify_channels(m)
    keep = {ch for ch, k in kinds.items() if k in ("piano", "bass")}
    bass = {ch for ch, k in kinds.items() if k == "bass"}
    if not any(k == "piano" for k in kinds.values()):
        return None
    out = mido.MidiFile(type=1, ticks_per_beat=m.ticks_per_beat)
    kept = 0
    for tr in m.tracks:
        nt = mido.MidiTrack()
        pending = 0
        for msg in tr:
            pending += msg.time
            if msg.is_meta or not hasattr(msg, "channel"):
                nt.append(msg.copy(time=pending))
                pending = 0
                continue
            if msg.channel not in keep:
                continue
            if msg.type == "program_change":
                msg = msg.copy(program=0)
            elif msg.type == "control_change" and (msg.channel in bass or msg.control not in (64, 66, 67)):
                continue
            elif msg.type in ("pitchwheel", "aftertouch", "polytouch"):
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                kept += 1
                if msg.channel in bass:
                    msg = msg.copy(velocity=max(1, int(msg.velocity * 0.85)))
            nt.append(msg.copy(channel=0, time=pending))
            pending = 0
        out.tracks.append(nt)
    if kept < 16:
        return None
    buf = io.BytesIO()
    out.save(file=buf)
    return buf.getvalue()


def drop_melody_track(data: bytes) -> tuple[bytes, bool]:
    """PIAST files carry a second track named 'melody' that repeats the piano's own notes."""
    m = mido.MidiFile(file=io.BytesIO(data), clip=True)
    keep = [tr for tr in m.tracks if (tr.name or "").strip().lower() != "melody"]
    if len(keep) == len(m.tracks):
        return data, False
    m.tracks = keep
    buf = io.BytesIO()
    m.save(file=buf)
    return buf.getvalue(), True


def trim_window(data: bytes, start_s: float, end_s: float) -> tuple[bytes, int]:
    """Cut a PiJAMA transcription to the annotated performance (the dataset tags applause, talk and
    other non-piano audio at the ends). Notes starting outside [start_s, end_s] are dropped, notes
    are cut at end_s, and time is shifted so the music starts at once. Returns (bytes, dropped)."""
    m = mido.MidiFile(file=io.BytesIO(data), clip=True)
    events = []  # (abs_seconds, order, msg)
    # mido's own tempo-aware iteration gives absolute seconds.
    t = 0.0
    for i, msg in enumerate(m):
        t += msg.time
        if msg.is_meta:
            continue
        events.append((t, i, msg))
    on: dict[tuple[int, int], float] = {}
    notes = []  # (on_s, off_s, ch, note, vel)
    ctrl = []   # (t, msg)
    for t, _, msg in events:
        if msg.type == "note_on" and msg.velocity > 0:
            on[(msg.channel, msg.note)] = (t, msg.velocity)
        elif msg.type in ("note_off", "note_on"):
            k = (msg.channel, msg.note)
            if k in on:
                t0, v = on.pop(k)
                notes.append((t0, t, msg.channel, msg.note, v))
        elif msg.type == "control_change":
            ctrl.append((t, msg))
    for (ch, n), (t0, v) in on.items():
        notes.append((t0, t0 + 0.5, ch, n, v))
    kept = [x for x in notes if start_s <= x[0] <= end_s]
    dropped = len(notes) - len(kept)
    if not kept:
        return data, 0
    t_shift = max(0.0, min(x[0] for x in kept) - 0.05)
    t_end = min(end_s, max(x[1] for x in kept))
    out_ev = []
    # Pedal state at the cut: re-assert the last value of each controller before start.
    last_cc: dict[tuple[int, int], int] = {}
    for t, msg in ctrl:
        if t < t_shift:
            last_cc[(msg.channel, msg.control)] = msg.value
        elif t <= t_end + 0.5:
            out_ev.append((t - t_shift, 1, msg))
    for (ch, c), v in last_cc.items():
        out_ev.append((0.0, 0, mido.Message("control_change", channel=ch, control=c, value=v)))
    for t0, t1, ch, n, v in kept:
        out_ev.append((t0 - t_shift, 2, mido.Message("note_on", channel=ch, note=n, velocity=v)))
        out_ev.append((min(t1, t_end) - t_shift, 0, mido.Message("note_off", channel=ch, note=n, velocity=0)))
    out_ev.sort(key=lambda x: (x[0], x[1]))
    tpb = 480
    tempo = 500000  # 120 bpm: 960 ticks per second
    out = mido.MidiFile(type=0, ticks_per_beat=tpb)
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    tr.append(mido.Message("program_change", channel=0, program=0, time=0))
    prev = 0
    for t, _, msg in out_ev:
        tick = int(round(t * 1e6 / tempo * tpb))
        tr.append(msg.copy(time=max(0, tick - prev)))
        prev = max(prev, tick)
    tr.append(mido.MetaMessage("end_of_track", time=0))
    out.tracks.append(tr)
    buf = io.BytesIO()
    out.save(file=buf)
    return buf.getvalue(), dropped


# ---------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------

def load_pijama() -> tuple[zipfile.ZipFile, list[dict]]:
    zp = CACHE / "midi_kong.zip"
    if not (zp.exists() and zp.stat().st_size == PIJAMA_ZIP_SIZE):
        log("downloading PiJAMA midi_kong.zip (36 MB) from Zenodo")
        download_resumable(PIJAMA_ZIP_URL, zp, PIJAMA_ZIP_SIZE, PIJAMA_ZIP_MD5)
    cp = CACHE / "pijama.csv"
    if not cp.exists():
        log("downloading pijama.csv")
        cp.write_bytes(http_get(PIJAMA_CSV_URL))
    z = zipfile.ZipFile(zp)
    names = set(z.namelist())
    rows = []
    for r in csv.DictReader(open(cp, encoding="utf-8")):
        path = r["midi_filepath"].replace("data/midi/", "midi_kong/", 1)
        if path not in names:
            continue
        r["zip_path"] = path
        rows.append(r)
    return z, rows


def mck_listing() -> list[dict]:
    """Every McKenzie file: {url, file, title_hint, label}. Cached HTML pages are reused."""
    pages = {"index": (MCK_INDEX_URL, "mck_index.html"),
             "midisite": (MCK_INDEX_URL + "Midi%20site/", "mck_midisite.html"),
             "page": (MCK_PAGE_URL, "mck_page.html")}
    txt = {}
    for k, (url, fn) in pages.items():
        p = CACHE / fn
        if not p.exists():
            log(f"fetching {url}")
            p.write_bytes(polite_get(url, min_gap=2.0))
        txt[k] = p.read_text("utf-8", "replace")
    out: dict[str, dict] = {}
    labels = {}
    for href, label in re.findall(r'<a[^>]+href="([^"]+\.mid)"[^>]*>([^<]*)', txt["page"], re.I):
        u = urllib.parse.urljoin(MCK_PAGE_URL, html.unescape(href)).replace("://www.", "://")
        labels[u] = html.unescape(label).strip()
    for base, k in ((MCK_INDEX_URL, "index"), (MCK_INDEX_URL + "Midi%20site/", "midisite")):
        for href in re.findall(r'href="([^"?/][^"]*\.mid)"', txt[k], re.I):
            u = urllib.parse.urljoin(base, html.unescape(href))
            fn = urllib.parse.unquote(u.rsplit("/", 1)[1])
            out[u] = {"url": u, "file": fn, "dir": k, "label": labels.get(u)}
    for u, lab in labels.items():
        if u not in out:
            out[u] = {"url": u, "file": urllib.parse.unquote(u.rsplit("/", 1)[1]), "dir": "page", "label": lab}
    return list(out.values())


MCK_JUNK = {
    "gm", "xg", "trio", "solo", "group", "duet", "duo", "corrected", "correct", "rubato", "tempo",
    "combined", "annotations", "only", "accompany", "accompaniment", "vocal", "transcription", "first",
    "chorus", "pt", "part", "tk", "take", "v", "version", "edited", "doug", "mckenzie", "type",
    "chords", "exbiab", "biab", "ok", "latin", "piano", "midi", "file", "plus", "ex", "a", "b", "c",
    "s"}


def mck_title_from_file(fn: str) -> str:
    base = re.sub(r"\.mid$", "", fn, flags=re.I)
    base = re.sub(r"\(.*?\)|\[.*?\]", " ", base)
    base = base.replace("_", " ").replace("-", " ")
    base = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)
    base = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", base)
    base = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", base)
    words = [w for w in base.split() if w.lower() not in MCK_JUNK and not w.isdigit()]
    return " ".join(words)


def mck_flags(item: dict) -> dict:
    s = (item["file"] + " " + (item.get("label") or "")).lower()
    return {"trio": any(w in s for w in ("trio", "group", "duet", "duo", "jazz kit", "2 pianos")),
            "solo": "solo" in s, "xg": bool(re.search(r"xg", item["file"], re.I)),
            "vocal": any(w in s for w in ("vocal", "accompan"))}


# McKenzie files whose names mislead the matcher: lower-case file name -> canonical title, or None to skip.
MCK_OVERRIDES: dict[str, str | None] = {
    "prelude1.mid": None, "prelude1.mid ": None,          # "Prelude" (not Prelude to a Kiss)
    "somewhere.mid": None,                                  # untitled: West Side Story or Over the Rainbow?
    "dolphindance3 no piano.mid": None,
    "allthethingsgroup.mid": "All the Things You Are",
    "allthethingssoloandtrio.mid": "All the Things You Are",
    "allthethings8.mid": "All the Things You Are",
    "allthethings reharmonized.mid": "All the Things You Are",
    "sweetway1.mid": "In Your Own Sweet Way", "sweetway2.mid": "In Your Own Sweet Way",
    "sweetway3.mid": "In Your Own Sweet Way",
    "youdon'tknowsolochorus.mid": "You Don't Know What Love Is",
    "youdontknow trio.mid": "You Don't Know What Love Is",
    "theduke.mid": "The Duke", "thedukeforpt.mid": "The Duke",
    "imgettingsentimentalpianotranscription.mid": "I'm Getting Sentimental Over You",
    "inasent.mid": "In a Sentimental Mood", "inasentgm.mid": "In a Sentimental Mood",
    "inasent2.mid": "In a Sentimental Mood",
    "moodind2.mid": "Mood Indigo", "maiden3.mid": "Maiden Voyage",
    "misty5gm.mid": "Misty", "misty5xg.mid": "Misty", "mistycombined3.mid": "Misty",
    "cryme.mid": "Cry Me a River", "sunnyside.mid": "On the Sunny Side of the Street",
    "illbeseegmversion.mid": "I'll Be Seeing You", "illbeseexgversion.mid": "I'll Be Seeing You",
    "everyth2b.mid": None, "nig6.mid": None, "polka.mid": None, "thetalk.mid": None,
    "verythoughtromantic.mid": None, "touchv1.mid": None, "touchv3.mid": None, "touchv4.mid": None,
    "reddress2.mid": None, "sammywalked.mid": None, "rainwalt.mid": None, "memoriesofparis.mid": None,
    "memories of paris.mid": None, "mikepee.mid": None, "mike pee 2.mid": None,
    "renewalannotations.mid": None, "renewaltrio.mid": None, "pathetique.mid": None,
    "chopinwaltz4.mid": None, "chopinwaltzab.mid": None, "chopin waltzab.mid": None,
    "nocturneebchopin.mid": None, "avemaria.mid": None, "bach prelude no 1 (ave maria).mid": None,
    "oklahomamedley.mid": None, "kingandi.mid": None, "surrey medley.mid": None,
    "gymnopedie-it never entered my mind.mid": None, "long ago and far away - 3 versions.mid": None,
    "haveyourselfamerry4.mid": None, "letitsnow.mid": None, "santaclausiscomingtotown.mid": None,
    "whitechristmasfirstchorusonly.mid": None, "winterwonderland.mid": None, "merryxmas.mid": None,
    "baby its cold outside.mid": None, "christmastimegroup.mid": None, "christmassongsolo.mid": None,
    "thechristmassong.mid": None,
    "godblessthechildpianoaccompany.mid": None, "goodmorningheartacheaccompany.mid": None,
    "closeenoughforlove(vocalaccompaniment).mid": None, "surreyvocal.mid": None,
    "brazillike backing.mid": None, "spartacus- 2 pianos.mid": None, "alabama.mid": None,
    "have you met - duet.mid": None, "come rain or come shine duo.mid": None,
    "she'sleaving.mid": None, "she'sleavingrubato corrected.mid": None,
    "she'sleaving withtempotrack.mid": None, "yesterday.mid": None, "eleanor.mid": None,
    "eleanorrigby.mid": None, "eleanor rigby.mid": None, "blackbird(gm).mid": None,
    "blackbird(xg).mid": None, "blackbird.mid": None, "everybreath.mid": None,
    "neverletme.mid": None, "taketheatrain.mid": None, "poinciana.mid": None, "poinciana[1].mid": None,
    "goinhome.mid": None, "goin'_home_(real_time)_(antonin_dvorak).mid": None,
}

# PIAST-YT picks (YouTube piano videos transcribed to MIDI): id | title | performer | kind
#   kind "pianist": the video's audio is that pianist's own recording (transcription channels, artist uploads)
#   kind "advanced"/"medium": a jazz pianist-teacher's arrangement played by them (Sabin Todorov, Jacob Koller)
PIAST_TABLE = r"""
t_wHQnDKqEs | In a Sentimental Mood | Billy Taylor | pianist
cYqEIZYzdK0 | My Funny Valentine | Emmet Cohen | pianist
fmLijbAiK48 | Sweet Georgia Brown | Oscar Peterson | pianist
buroWB9ETRw | Sweet Lorraine | Hank Jones | pianist
6on2OyKOPm4 | Moonglow | Teddy Wilson | pianist
d8gs8iLoEUU | If I Loved You | Fred Hersch | pianist
z278FNdETq0 | Corcovado (Quiet Nights of Quiet Stars) | Fred Hersch | pianist
oY7OHGTTKNU | Blues for Alice | Peter Martin | pianist
5MmWYuCoDLg | Misty | Benny Green | pianist
W8ybvqmQzgc | I Got It Bad (and That Ain't Good) | Benny Green | pianist
-vOXQoUn3yg | The Girl from Ipanema | Sabin Todorov | advanced
w-6iaizjJwY | Cheek to Cheek | Sabin Todorov | advanced
Vt9wuzoZKxU | I Got Rhythm | Sabin Todorov | advanced
rOEMKcPnkjw | A Night in Tunisia | Sabin Todorov | advanced
w_NzgR-wkJs | Cantaloupe Island | Sabin Todorov | medium
lkAfS4v2AvQ | All Blues | Sabin Todorov | advanced
0yUU5EN2msQ | Footprints | Sabin Todorov | advanced
gCYd6EC2VbE | Wave | Sabin Todorov | advanced
6K92schczKM | Wave | Jacob Koller | advanced
eZA0tNTnufs | One Note Samba | MIWHA | medium
Lw5fxN8IoNs | Take Five | Jacob Koller | advanced
58QS5POhkug | Fly Me to the Moon | Jacob Koller | advanced
P4r5R4JuVIo | Desafinado | Sabin Todorov | advanced
HQtdUmVB2_4 | How Insensitive | Jacob Koller | advanced
TgyseY-Q3m8 | Meditation | Sabin Todorov | advanced
wDNtv01-3mw | Once I Loved | Sabin Todorov | advanced
_BB4xGJx9o8 | Manhã de Carnaval (Black Orpheus) | Sabin Todorov | advanced
5PtR0HOvASE | Billie's Bounce | Sabin Todorov | advanced
F-JRASFzpns | Hot House | Sabin Todorov | advanced
xHPhRzqy6Ec | Nica's Dream | Sabin Todorov | advanced
RgLyCWXMQzs | Fascinating Rhythm | Sabin Todorov | advanced
wA4XtwW6c5w | Goodbye Pork Pie Hat | Sabin Todorov | advanced
v2g3nvlXac4 | Upper Manhattan Medical Group | Sabin Todorov | advanced
SIgQ5_mLtMw | Isfahan | Sabin Todorov | advanced
-JJ5U9vKeIY | Very Early | Sabin Todorov | advanced
mbopPiXbtAc | The Peacocks | Sabin Todorov | advanced
eeHNey4BYOA | Pent-Up House | Sabin Todorov | advanced
GE5_3p4HBjU | Whisper Not | Sabin Todorov | advanced
ue0SknCkIag | I'll Remember April | Sabin Todorov | advanced
uyD-OZJ7zKE | I Get a Kick Out of You | Sabin Todorov | advanced
B37qSb8aumk | Just Friends | Sabin Todorov | advanced
Nv8PZIzrQww | Stormy Weather | Sabin Todorov | advanced
PaIDM0MT5ic | Speak Low | Sabin Todorov | advanced
m5szc76nUUI | Nature Boy | Sabin Todorov | advanced
WD4mOuRJjxI | You Must Believe in Spring | Sabin Todorov | advanced
PKZSlYM6p3c | Dancing on the Ceiling | Sabin Todorov | advanced
Qw-sXse5J4k | All of You | Sabin Todorov | advanced
27C9Sm12Jiw | Every Time We Say Goodbye | Sabin Todorov | advanced
Y1f0zy1GPHc | Angel Eyes | Sabin Todorov | advanced
tQpWCbZ65C8 | Dolphin Dance | Sabin Todorov | advanced
4XBOV7gWjl8 | Bluesette | Sabin Todorov | advanced
yrC1ut4qnWc | It Don't Mean a Thing (If It Ain't Got That Swing) | Sabin Todorov | advanced
yKTYM5eQ30A | But Not for Me | Sabin Todorov | advanced
qpaWYasWWcs | A Foggy Day | Sabin Todorov | advanced
41s-_sIQgcI | Tenderly | Sabin Todorov | advanced
bUfGZG9PVoo | Moon River | Sabin Todorov | advanced
oDovCLJCDPQ | My Favorite Things | Sabin Todorov | advanced
BE9y4bgyPqs | Well, You Needn't | Sabin Todorov | advanced
9lHVAByQPnw | What Is This Thing Called Love? | Sabin Todorov | advanced
H1I-Ghc04lY | Stella by Starlight | Sabin Todorov | advanced
lJXDQHpvqVI | Satin Doll | Sabin Todorov | advanced
gbxgMFMuT_4 | Autumn Leaves | Sabin Todorov | advanced
zK-zegKXrxE | 'Round Midnight | Sabin Todorov | advanced
XahYwLELXmM | Blue in Green | Sabin Todorov | advanced
uMRVxK1yqvU | How Long Has This Been Going On? | Sabin Todorov | advanced
46-wHclE6Kg | I Thought About You | Sabin Todorov | advanced
YmYdF4fe_Dk | You Do Something to Me | Sabin Todorov | advanced
mie3rgkcVKY | So in Love | Sabin Todorov | advanced
moIoaQozfY4 | Gone with the Wind | Sabin Todorov | advanced
1Cjmu0Mue18 | Armando's Rumba | Sabin Todorov | advanced
B8tpuMPsL8Q | Con Alma | Sabin Todorov | advanced
H2KUVjPCQDU | Spring Is Here | Sabin Todorov | advanced
CNW5rzQlkqg | Like Someone in Love | Sabin Todorov | advanced
Eksnrc8-rjg | Days of Wine and Roses | Jacob Koller | advanced
b_nMUSO62Bw | Moanin' | Jonny May | advanced
UMY8CXFAzCg | Lullaby of Birdland | Daeyoun Kim | medium
"""

EXTRA_TIER_B = ["Billy Taylor", "Duke Jordan", "Tete Montoliu", "Horace Parlan", "Emmet Cohen"]
ARRANGER_SCORE = {"advanced": 1.45, "medium": 1.3}
MCK_SOLO, MCK_TRIO = 1.7, 1.0


def parse_piast() -> list[dict]:
    out = []
    for line in PIAST_TABLE.strip().splitlines():
        vid, title, performer, kind = [p.strip() for p in line.split("|")]
        out.append({"id": vid, "title": title, "performer": performer, "kind": kind})
    return out


def norm_articles(t: str) -> str:
    """Like norm() but keeping articles (for run-together file names such as 'atimeforlove')."""
    t = strip_accents(t).replace("’", "'")
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t).lower().replace("&", " and ").replace("'", "")
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class Matcher:
    def __init__(self, standards: list[dict]):
        self.by_key: dict[str, dict] = {}
        self.by_title: dict[str, dict] = {}
        self.compacts: list[tuple[str, dict]] = []
        for s in standards:
            self.by_title[s["title"]] = s
            for k in s["keys"]:
                self.by_key.setdefault(k, s)
                self.compacts.append((k.replace(" ", ""), s))
            for t in [s["title"]] + s.get("alias_titles", []):
                self.compacts.append((norm_articles(t).replace(" ", ""), s))

    def exact(self, title: str) -> dict | None:
        return self.by_key.get(norm(title))

    def fuzzy(self, title: str) -> dict | None:
        s = self.exact(title)
        if s:
            return s
        for v in {norm(title).replace(" ", ""), norm_articles(title).replace(" ", "")}:
            if len(v) < 6:
                continue
            hits = {st["title"]: st for ck, st in self.compacts
                    if ck.startswith(v) or (len(ck) >= 6 and v.startswith(ck))}
            if len(hits) == 1:
                return next(iter(hits.values()))
        return None


def existing_keys() -> set[str]:
    keys = set(EXISTING_EXTRA_KEYS)
    if TEST_PIANO.is_dir():
        for p in TEST_PIANO.glob("Jazz - *.mid"):
            parts = p.stem.split(" - ", 2)
            if len(parts) == 3:
                keys.add(norm(re.sub(r"\((live|joplin)\)", "", parts[2], flags=re.I)))
    return keys


# ---------------------------------------------------------------------------------------------
# Candidates and scoring
# ---------------------------------------------------------------------------------------------

def pianist_score(name: str) -> float:
    if name in TIER_A:
        return 3.0 - 0.02 * TIER_A.index(name)
    if name in TIER_B:
        return 2.0 - 0.004 * TIER_B.index(name)
    if name in EXTRA_TIER_B:
        return 1.8
    return 1.5


def is_medley(title: str) -> bool:
    """Two tunes in one track ("A / B", "A  B", "Medley - ..."); live-album suffixes don't count."""
    t = SUFFIX_RE.sub("", title)
    return bool(re.search(r"medley|\s/\s|\S  +\S", t, re.I))


def collect(standards: list[dict]) -> tuple[dict, zipfile.ZipFile]:
    m = Matcher(standards)
    cands: dict[str, list[dict]] = {s["title"]: [] for s in standards}
    z, rows = load_pijama()
    for r in rows:
        t = r["title"]
        if is_medley(t):
            continue
        s = m.exact(t)
        if not s:
            continue
        d, st, en = float(r["duration_sec"]), float(r["performance_start_sec"]), float(r["performance_end_sec"])
        cands[s["title"]].append({
            "src": "pijama", "performer": r["artist"], "album": r["album"], "orig_title": t,
            "live": r["recording_condition"] == "live", "duration": d, "start": st, "end": en,
            "agreement": float(r["agreement"] or 0.88), "zip_path": r["zip_path"],
            "youtube": r["youtube_url"], "alt": bool(re.search(r"take|alternate|alt\.", t, re.I))})
    for it in mck_listing():
        fn = it["file"].lower()
        if fn in MCK_OVERRIDES:
            title = MCK_OVERRIDES[fn]
            s = m.by_title.get(title) if title else None
            if title and not s:
                log(f"  override title not in table: {title}")
        else:
            s = None
            if it.get("label"):
                lab = re.sub(r"\[M\]|\(.*?\)|–.*$|/\s*\d.*$|/\s*$|\s/\S.*$", "", it["label"]).strip()
                s = m.exact(lab) or m.fuzzy(lab)
            if not s:
                s = m.fuzzy(mck_title_from_file(it["file"]))
        if s:
            cands[s["title"]].append({"src": "mckenzie", "performer": "Doug McKenzie", **it,
                                      "flags": mck_flags(it)})
    for p in parse_piast():
        s = m.by_title.get(p["title"])
        if not s:
            log(f"  PIAST title not in table: {p['title']}")
            continue
        cands[s["title"]].append({"src": "piast", **p})
    return cands, z


def mck_fetch(it: dict) -> bytes | None:
    d = CACHE / "mckenzie"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (it["dir"] + "__" + slug(it["file"]) + ".mid")
    if not p.exists():
        try:
            data = polite_get(it["url"], min_gap=1.5)
        except Exception as e:  # noqa: BLE001
            log(f"  McKenzie fetch failed {it['url']}: {e}")
            return None
        p.write_bytes(data)
    return p.read_bytes()


def piast_fetch(vid: str) -> bytes | None:
    d = CACHE / "piast"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{vid}.mid"
    if not p.exists():
        url = f"https://huggingface.co/datasets/Hayeonbang/PIAST/resolve/main/piast_yt/midi/{vid}.mid"
        try:
            p.write_bytes(polite_get(url, min_gap=1.0))
        except Exception as e:  # noqa: BLE001
            log(f"  PIAST fetch failed {vid}: {e}")
            return None
    return p.read_bytes()


def score(c: dict, std: dict, used: dict[str, int]) -> float:
    perf = c["performer"]
    if c["src"] == "pijama" or (c["src"] == "piast" and c["kind"] == "pianist"):
        sc = pianist_score(perf)
    elif c["src"] == "piast":
        sc = ARRANGER_SCORE.get(c["kind"], 1.2)
    else:
        sc = MCK_SOLO if not c["flags"]["trio"] else MCK_TRIO
        if c["flags"]["xg"]:
            sc -= 0.05
        if c["flags"]["vocal"]:
            sc -= 1.0
        if re.search(r"part\s*\d", c["file"], re.I):
            sc -= 0.3
        info = c.get("info")
        if info:
            if not info["solo"]:
                sc = min(sc, MCK_TRIO)
            dur = info["duration_s"]
            sc += 0.1 * min(dur / 180.0, 1.0)
            if dur < 70:
                sc -= 0.6
    if COMPOSER_PIANISTS.get(std["composer"]) == perf:
        sc += 1.5
    if c["src"] == "pijama":
        sc += 0.1 if not c["live"] else 0.0
        sc += max(-0.1, min(0.1, c["agreement"] - 0.88))
        span = c["end"] - c["start"]
        if span < 90:
            sc -= 0.5
        elif span > 900:
            sc -= 0.6
        elif span > 540:
            sc -= 0.2
        if c["alt"]:
            sc -= 0.05
    # Spread the picks across pianists; Doug McKenzie and the arrangers are the fallbacks anyway.
    light = perf == "Doug McKenzie" or (c["src"] == "piast" and c["kind"] != "pianist")
    sc -= (0.01 if light else 0.08) * used.get(perf, 0)
    return sc


def is_famous_take(c: dict) -> bool:
    """A transcription of a tier-A pianist's own recording."""
    if c["src"] == "mckenzie" or (c["src"] == "piast" and c["kind"] != "pianist"):
        return False
    return pianist_score(c["performer"]) >= 2.6


def solo_info(data: bytes) -> dict:
    info = inspect_midi(data)
    kinds = classify_channels(mido.MidiFile(file=io.BytesIO(data), clip=True))
    info["solo"] = all(k == "piano" for k in kinds.values())
    return info


# ---------------------------------------------------------------------------------------------
# Selection, staging, manifest
# ---------------------------------------------------------------------------------------------

MIN_RANK = 0.78          # every standard at least this well known is staged when a source has it
MIN_RANK_TRIO = 0.84     # ...but a McKenzie trio take (made one piano) only at this rank and above
MIN_RANK_FAMOUS = 0.6    # less-known tunes only in a famous (tier A) pianist's take,
FAMOUS_MIN_SCORE = 2.4   # and only while that pianist is not already over-represented


def choose(standards: list[dict], cands: dict, excl: set[str]) -> list[tuple[dict, dict, float]]:
    used: dict[str, int] = {}
    picks = []
    for std in sorted(standards, key=lambda s: (-s["rank"], s["title"])):
        if any(k in excl for k in std["keys"]):
            continue
        cs = [c for c in cands.get(std["title"], []) if not c.get("dead")]
        if not cs or std["rank"] < MIN_RANK_FAMOUS:
            continue
        others = [score(c, std, used) for c in cs if c["src"] != "mckenzie"]
        if any(c["src"] == "mckenzie" for c in cs) and max(others, default=-9) < MCK_SOLO + 0.3:
            for c in cs:
                if c["src"] == "mckenzie" and "info" not in c:
                    data = mck_fetch(c)
                    try:
                        c["info"] = solo_info(data) if data else None
                    except Exception as e:  # noqa: BLE001 - a broken file is just skipped
                        log(f"  unreadable {c['file']}: {e}")
                        c["info"] = None
                    if not c["info"]:
                        c["dead"] = True
            cs = [c for c in cs if not c.get("dead")]
            if not cs:
                continue
        best = max(cs, key=lambda c: score(c, std, used))
        bs = score(best, std, used)
        famous = is_famous_take(best)
        if "modern standard" in std["tags"] and not famous:
            continue        # pop songs belong to the pop shelf unless a great jazz pianist made them theirs
        if std["rank"] >= MIN_RANK:
            if best["src"] == "mckenzie" and not (best.get("info") or {}).get("solo", True) \
                    and std["rank"] < MIN_RANK_TRIO:
                continue    # a trio take made one piano only for the best-known tunes
        elif not (famous and bs >= FAMOUS_MIN_SCORE):
            continue
        picks.append((std, best, bs))
        used[best["performer"]] = used.get(best["performer"], 0) + 1
    return picks


def materialise(std: dict, c: dict, z: zipfile.ZipFile) -> tuple[bytes, list[str]] | None:
    edits: list[str] = []
    if c["src"] == "pijama":
        data = z.read(c["zip_path"])
        lo, hi = max(0.0, c["start"] - 1.0), c["end"] + 2.0
        if c["start"] > 1.0 or c["end"] < c["duration"] - 3.0:
            cut, dropped = trim_window(data, lo, hi)
            if dropped >= 5:
                data = cut
                edits.append(f"cut to the annotated performance {lo:.0f}-{hi:.0f} s "
                             f"(applause/talk at the ends; {dropped} notes dropped)")
    elif c["src"] == "mckenzie":
        data = mck_fetch(c)
        if data is None:
            return None
        info = c.get("info") or solo_info(data)
        if not info["solo"]:
            red = reduce_to_piano(data)
            if red is None:
                return None
            data = red
            edits.append("trio/group file made one piano: drums and other instruments dropped, "
                         "the bass line kept on the piano")
    else:
        data = piast_fetch(c["id"])
        if data is None:
            return None
        data, dropped = drop_melody_track(data)
        if dropped:
            edits.append("dropped the duplicate 'melody' track")
    return data, edits


def item_for(std: dict, c: dict, fname: str, info: dict, edits: list[str]) -> dict:
    tags = list(std["tags"])
    if c["src"] == "pijama":
        source, licence, take = "PiJAMA", PIJAMA_LICENCE, "transcription"
        ref = c["zip_path"] + (f" ({c['youtube']})" if c.get("youtube") else "")
        tags.append("live" if c["live"] else "studio")
        performer = c["performer"]
    elif c["src"] == "mckenzie":
        source, licence, take = "Doug McKenzie jazz piano MIDI (bushgrafts.com)", None, "performance"
        ref = c["url"]
        performer = "Doug McKenzie"
        if not (c.get("info") or {}).get("solo", True):
            tags.append("trio reduced")
    else:
        source, licence, take = "PIAST-YT", "MIT", "transcription"
        ref = f"https://huggingface.co/datasets/Hayeonbang/PIAST/resolve/main/piast_yt/midi/{c['id']}.mid " \
              f"(https://www.youtube.com/watch?v={c['id']})"
        performer = c["performer"]
        if c["kind"] != "pianist":
            tags.append("arrangement")
    tags = ["solo piano"] + [t for t in dict.fromkeys(tags) if t]
    item = {"src": f"files/{fname}", "title": std["title"], "composer": std["composer"],
            "performer": performer, "genre": "jazz", "era": None, "year": std["year"],
            "source": source, "source_ref": ref, "licence": licence, "take": take, "tags": tags,
            "rank": std["rank"], "duration_s": info["duration_s"], "notes": info["notes"],
            "pedal_events": info["pedal"], "velocity_levels": info["velocities"]}
    if c["src"] == "pijama":
        item["recording"] = f"{c['album']} ({'live' if c['live'] else 'studio'})"
    if edits:
        item["edits"] = edits
    return item


def write_if_changed(p: Path, data: bytes) -> bool:
    if p.exists() and p.read_bytes() == data:
        return False
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    return True


SOURCES_MD = """# Jazz shelf: sources

Staged by `tools/music_library/fetch_jazz.py` (re-runnable; downloads cached under
`~/.local/share/screen-machine/midi-library/_cache/jazz/`). One take per title; titles already in
`config/live/test-piano` are not repeated. Private, non-commercial use in this house only.

Selection: for each standard (canonical title, composer, year and a 0-1 "how well known" rank kept in
the script's table), the take of the best-known pianist wins (a composer playing their own tune first;
picks are spread so no pianist dominates); then Doug McKenzie's solo take; then a lesser-known pianist;
then a pianist-teacher's arrangement; then a McKenzie trio take made one piano. Standards ranked
>= {min_rank} are staged when any source has them; less-known tunes (>= {min_famous}) only in a famous
pianist's take; pop songs only in a famous jazz pianist's take.

## PiJAMA (Queen Mary University of London): {n_pijama} files

Solo jazz piano recordings (studio and live) by 120 pianists, transcribed to MIDI (notes, velocities,
sustain pedal) by the Kong et al. high-resolution piano transcription model.
- URL: https://zenodo.org/records/8354955 (midi_kong.zip, md5 {md5}); metadata
  https://github.com/almostimplemented/PiJAMA (pijama.csv)
- Licence: **{pijama_lic}** (the MIDI release). Compositions and recordings remain their owners'.
- Citation: Drew Edwards, Simon Dixon, Emmanouil Benetos, "PiJAMA: Piano Jazz with Automatic MIDI
  Annotations", Transactions of the International Society for Music Information Retrieval 6(1), 2023.
- Changes: live tracks whose dataset annotation marks applause/talk at the ends were cut to the
  annotated performance window ({n_trimmed} files; noted per item in `edits`). Otherwise unchanged.

## Doug McKenzie jazz piano MIDI (bushgrafts.com): {n_mck} files

Jazz standards hand-played on a MIDI keyboard by pianist Doug McKenzie (real velocities and pedal).
- URL: https://bushgrafts.com/jazz/ (and https://bushgrafts.com/midi/, files under /jazz/Midi%20site/)
- Licence: none stated (files offered freely on the site); recorded as null.
- Changes: trio/group files are made one piano: drums and other instruments dropped, the bass line
  kept on the piano ({n_reduced} files). Otherwise unchanged.

## PIAST-YT (PIAST dataset, YouTube subset): {n_piast} files

Solo piano YouTube videos transcribed to MIDI (pedal, varied velocity). Used for a few standards no
other source has, and for transcription videos of named pianists' recordings.
- URL: https://huggingface.co/datasets/Hayeonbang/PIAST (piast_yt/midi/<youtube id>.mid); titles from
  YouTube oEmbed. Project page: https://hayeonbang.github.io/PIAST_dataset/
- Licence: **MIT** (dataset README).
- Citation: Hayeon Bang et al., "PIAST: A Multimodal Piano Dataset with Audio, Symbolic and Text",
  2024 (arXiv:2411.02551).
- Changes: the duplicate 'melody' track (it repeats the piano's notes) is dropped.

## Counts

{counts}
"""


def run(args) -> int:
    standards = parse_standards()
    excl = existing_keys()
    log(f"{len(standards)} standards in the table; {len(excl)} titles already in test-piano")
    cands, z = collect(standards)
    picks = choose(standards, cands, excl)
    log(f"{len(picks)} titles chosen")
    if args.report or args.dry_run:
        for std, c, sc in picks:
            where = c.get("zip_path") or c.get("url") or c.get("id")
            print(f"{std['rank']:.2f}  {std['title'][:44]:44}  {c['src']:8}  {c['performer'][:22]:22}  "
                  f"{sc:.2f}  {where}")
        return 0
    files_dir = STAGE / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    items, names = [], set()
    for std, c, _ in picks:
        got = materialise(std, c, z)
        if not got:
            log(f"  skipped {std['title']}: source file unavailable")
            continue
        data, edits = got
        try:
            info = inspect_midi(data)
        except Exception as e:  # noqa: BLE001
            log(f"  skipped {std['title']}: does not parse ({e})")
            continue
        if info["notes"] < 16:
            log(f"  skipped {std['title']}: only {info['notes']} notes")
            continue
        fname = f"{slug(std['title'])}--{slug(c['performer'])}.mid"
        names.add(fname)
        if write_if_changed(files_dir / fname, data):
            log(f"  wrote {fname}")
        items.append(item_for(std, c, fname, info, edits))
    for p in files_dir.glob("*.mid"):
        if p.name not in names:
            p.unlink()
            log(f"  removed stale {p.name}")
    manifest = {"v": 1, "key": KEY, "items": items}
    write_if_changed(STAGE / "manifest.json",
                     (json.dumps(manifest, indent=1, ensure_ascii=False) + "\n").encode())
    by_src: dict[str, int] = {}
    by_take: dict[str, int] = {}
    for it in items:
        by_src[it["source"]] = by_src.get(it["source"], 0) + 1
        by_take[it["take"]] = by_take.get(it["take"], 0) + 1
    perf: dict[str, int] = {}
    for it in items:
        perf[it["performer"]] = perf.get(it["performer"], 0) + 1
    counts = "\n".join([f"- {len(items)} titles in all."] +
                       [f"- {k}: {v}" for k, v in sorted(by_src.items(), key=lambda x: -x[1])] +
                       [f"- take '{k}': {v}" for k, v in sorted(by_take.items(), key=lambda x: -x[1])] +
                       ["- performers: " + ", ".join(f"{k} {v}" for k, v in
                                                     sorted(perf.items(), key=lambda x: (-x[1], x[0])))])
    md = SOURCES_MD.format(
        min_rank=MIN_RANK, min_famous=MIN_RANK_FAMOUS, n_pijama=by_src.get("PiJAMA", 0), md5=PIJAMA_ZIP_MD5, pijama_lic=PIJAMA_LICENCE,
        n_trimmed=sum(1 for it in items if any("cut to" in e for e in it.get("edits", []))),
        n_mck=by_src.get("Doug McKenzie jazz piano MIDI (bushgrafts.com)", 0),
        n_reduced=sum(1 for it in items if any("one piano" in e for e in it.get("edits", []))),
        n_piast=by_src.get("PIAST-YT", 0), counts=counts)
    write_if_changed(STAGE / "SOURCES.md", md.encode())
    log(f"staged {len(items)} files in {STAGE}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--report", action="store_true", help="print the selection; stage nothing")
    ap.add_argument("--dry-run", action="store_true", help="same as --report")
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
