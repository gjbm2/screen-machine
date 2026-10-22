#!/usr/bin/env python3
"""Stage the classical shelf of the piano MIDI library (about 190 movements).

Selection: a hand-curated list (PIECES below) built from PianoCoRe's popularity ranking (number of recorded
performances per movement: the top ~150 is close to "the pieces everyone knows"), with Chopin held to ~30 and
Beethoven to ~20, plus well-known pieces from ranks 150-400 (Lyric Pieces, Songs without Words, Seasons,
Gymnopedies/Gnossiennes, Kinderszenen, Inventions, Brahms intermezzi, ...).  The 10 MAESTRO titles already in
config/live/test-piano are left out.

Take per piece (one only), best first:
    ASAP (Yamaha Disklavier capture, e-Competition)                          take = performance
  > ATEPP transcription of a named modern-era pianist's commercial recording   take = transcription
  > PERiScoPe (Transkun V2 transcription of a recording)                     take = transcription
  > GiantMIDI-Piano / other ATEPP                                            take = transcription
  > piano-midi.de (Bernd Krueger's hand-sequenced, expressive files)         take = sequenced
  > Aria-MIDI (Aria-AMT transcription, velocity quantised to ~16 levels)     take = transcription
Only PianoCoRe rows labelled "high quality", not duplicates, not "_mini" excerpts, and with a duration
within 0.6-1.7x the piece's median (over its non-Aria takes) are eligible: this drops whole-work rows
filed under one movement.

Sources:
  PianoCoRe v1.0 (SyMuPe): the index, and every performance file (ASAP, ATEPP, PERiScoPe, GiantMIDI and
      Aria rows alike) range-read out of the Zenodo raw-midi zip (central directory cached once, ~45 MB).
  piano-midi.de: single files, for pieces PianoCoRe has no good take of.

Output (an assembler step cleans the files and builds the catalogue):
  ~/.local/share/screen-machine/midi-library/_staging/classical/{files/*.mid, manifest.json, SOURCES.md}
Downloads are cached under ~/.local/share/screen-machine/midi-library/_cache/classical/, so a re-run is
resumable and fetches only what is missing.  Polite: >= 1 s between requests to piano-midi.de, 0.5 s to
Zenodo, retries with backoff, a browser User-Agent.

Run:  nice -n 19 ~/.venvs/sm-musiclib/bin/python -I tools/music_library/fetch_classical.py [--plan] [--only SUBSTR]
Needs mido (and pyarrow only if the PianoCoRe metadata index has to be rebuilt).
"""
import argparse
import csv
import gzip
import io
import json
import math
import os
import re
import statistics
import struct
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib

ROOT = os.path.expanduser('~/.local/share/screen-machine/midi-library')
KEY = 'classical'
STAGE = os.path.join(ROOT, '_staging', KEY)
FILES = os.path.join(STAGE, 'files')
CACHE = os.path.join(ROOT, '_cache', KEY)

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/130.0 Safari/537.36')
HOST_DELAY = {'zenodo.org': 0.5, 'huggingface.co': 0.3, 'piano-midi.de': 1.5}
DEFAULT_DELAY = 1.0
BYTES = {'n': 0}
_last = {}

PCR_ZIP = 'https://zenodo.org/api/records/19186016/files/PianoCoRe-1.0-raw-midi.zip/content'
PCR_PARQUET = 'https://huggingface.co/datasets/SyMuPe/PianoCoRe/resolve/main/data/{}.parquet'
PCR_SHARDS = [f'train-{i:05d}-of-00016' for i in range(16)] + ['test-00000-of-00002', 'test-00001-of-00002']
PCR_COLS = ['id', 'composer', 'composition', 'movement', 'performance_id', 'performance_dataset', 'performer',
            'is_transcription', 'capture_model', 'is_duplicate', 'lead_performance', 'quality_label',
            'prob_high_quality', 'prob_corrupted', 'tier_b', 'tier_a_star', 'performance_duration',
            'performance_midi_path']
PMD = 'http://piano-midi.de/'   # bare host: www. answers 418/404

LICENCE = {
    'ASAP': 'CC BY-NC-SA 4.0',
    'ATEPP': 'CC BY 4.0',
    'PERiScoPe': 'CC BY-NC-SA 4.0',
    'GiantMIDI-Piano': 'CC BY 4.0',
    'Aria-MIDI': 'CC BY-NC-SA 4.0',
    'piano-midi.de': 'CC BY-SA 3.0 DE (Bernd Krueger, www.piano-midi.de)',
}
SOURCE_NAME = {
    'ASAP': 'ASAP (MAESTRO Disklavier) via PianoCoRe v1.0',
    'ATEPP': 'ATEPP v1.1 via PianoCoRe v1.0',
    'PERiScoPe': 'PERiScoPe v1.1 via PianoCoRe v1.0',
    'GiantMIDI-Piano': 'GiantMIDI-Piano v1.2 via PianoCoRe v1.0',
    'Aria-MIDI': 'Aria-MIDI v1 via PianoCoRe v1.0',
    'piano-midi.de': 'piano-midi.de',
}

# ATEPP pianists recorded in the modern era (high-quality rate 90-100 % in PianoCoRe); others rank lower.
GOOD_ATEPP = {
    'Murray Perahia', 'Mitsuko Uchida', 'Lang Lang', 'Radu Lupu', 'Alicia de Larrocha', 'Hélène Grimaud',
    'Alice Sara Ott', 'Daniel Barenboim', 'Claudio Arrau', 'András Schiff', 'Vladimir Ashkenazy',
    'Maurizio Pollini', 'Glenn Gould', 'Leif Ove Andsnes', 'Krystian Zimerman', 'Evgeny Kissin',
    'Nikolai Lugansky', 'Emanuel Ax', 'Alfred Brendel', 'Wilhelm Kempff', 'Mikhail Pletnev', 'Zoltán Kocsis',
    'Grigory Sokolov', 'Ivo Pogorelich', 'Valentina Lisitsa', 'Alexandre Tharaud', 'Bertrand Chamayou',
    'Yuja Wang', 'Anne Queffélec', 'Pierre-Laurent Aimard', 'Samson François', 'Friedrich Gulda'}
# PERiScoPe "performers" that are channels / compilations rather than pianists.
BAD_PERFORMER = ('sound', 'relax', 'sleep', 'music', 'emporium', 'coversart', 'piano', 'channel', 'official',
                 'studio', 'tutorial', 'lesson', 'karaoke', 'baby', 'kids', 'meditation', 'orchestr',
                 'ensemble', 'various', 'klassik', 'classical', 'records', 'masterworks')

# data composer key -> (full name, era)
COMPOSERS = {
    'Chopin,_Frédéric': ('Frédéric Chopin', 'Romantic'),
    'Beethoven,_Ludwig_van': ('Ludwig van Beethoven', 'Classical'),
    'Bach,_Johann_Sebastian': ('Johann Sebastian Bach', 'Baroque'),
    'Bach,_Carl_Philipp_Emanuel': ('Carl Philipp Emanuel Bach', 'Classical'),
    'Pezold,_Christian': ('Christian Petzold', 'Baroque'),
    'Scarlatti,_Domenico': ('Domenico Scarlatti', 'Baroque'),
    'Handel,_George_Frideric': ('George Frideric Handel', 'Baroque'),
    'Pachelbel,_Johann': ('Johann Pachelbel', 'Baroque'),
    'Haydn,_Joseph': ('Joseph Haydn', 'Classical'),
    'Mozart,_Wolfgang_Amadeus': ('Wolfgang Amadeus Mozart', 'Classical'),
    'Clementi,_Muzio': ('Muzio Clementi', 'Classical'),
    'Schubert,_Franz': ('Franz Schubert', 'Romantic'),
    'Mendelssohn,_Felix': ('Felix Mendelssohn', 'Romantic'),
    'Schumann,_Robert': ('Robert Schumann', 'Romantic'),
    'Liszt,_Franz': ('Franz Liszt', 'Romantic'),
    'Brahms,_Johannes': ('Johannes Brahms', 'Romantic'),
    'Grieg,_Edvard': ('Edvard Grieg', 'Romantic'),
    'Tchaikovsky,_Pyotr_Ilyich': ('Pyotr Ilyich Tchaikovsky', 'Romantic'),
    'Mussorgsky,_Modest': ('Modest Mussorgsky', 'Romantic'),
    'Field,_John': ('John Field', 'Romantic'),
    'Burgmüller,_Friedrich': ('Friedrich Burgmüller', 'Romantic'),
    'Elgar,_Edward': ('Edward Elgar', 'Romantic'),
    'Dvořák,_Antonín': ('Antonín Dvořák', 'Romantic'),
    'Saint-Saëns,_Camille': ('Camille Saint-Saëns', 'Romantic'),
    'Rimsky-Korsakov,_Nikolay': ('Nikolai Rimsky-Korsakov', 'Romantic'),
    'MacDowell,_Edward': ('Edward MacDowell', 'Romantic'),
    'Balakirev,_Mily': ('Mily Balakirev', 'Romantic'),
    'Sinding,_Christian': ('Christian Sinding', 'Romantic'),
    'Debussy,_Claude': ('Claude Debussy', 'Impressionist'),
    'Ravel,_Maurice': ('Maurice Ravel', 'Impressionist'),
    'Satie,_Erik': ('Erik Satie', 'Impressionist'),
    'Rachmaninoff,_Sergei': ('Sergei Rachmaninoff', 'Romantic'),
    'Scriabin,_Aleksandr': ('Alexander Scriabin', 'Romantic'),
    'Prokofiev,_Sergey': ('Sergei Prokofiev', '20th century'),
    'Bartók,_Béla': ('Béla Bartók', '20th century'),
    'Albéniz,_Isaac': ('Isaac Albéniz', 'Romantic'),
    'Falla,_Manuel_de': ('Manuel de Falla', '20th century'),
}


def P(composer, comp, mv, title, year, tags, **kw):
    """One piece: PianoCoRe composer key prefix, composition regex, movement regex (None = no movement),
    canonical title, composition year, space-separated tags.  kw: pmd (piano-midi.de path, used only when
    PianoCoRe has no good take, or alone when comp is None), pick (force a performance_id), rank (override),
    era (override), aria_ok (allow an Aria-MIDI take, default True)."""
    d = dict(composer=composer, comp=comp, mv=mv, title=title, year=year, tags=tags.split())
    d.update(kw)
    return d


PIECES = [
    # ------------------------------------------------------------------ Chopin (28)
    P('Chopin', r'^Impromptu_No\.4_', None, 'Fantaisie-Impromptu in C-sharp minor, Op. 66', 1834, 'impromptu fantaisie-impromptu dramatic'),
    P('Chopin', r'^Nocturne_No\.20_', None, 'Nocturne in C-sharp minor, B. 49 (Op. posth.)', 1830, 'nocturne lyrical slow'),
    P('Chopin', r'^Ballade_No\.1_', None, 'Ballade No. 1 in G minor, Op. 23', 1835, 'ballade dramatic'),
    P('Chopin', r'^12_Études,_Op\.10$', r'^No\.12_', 'Étude in C minor, Op. 10 No. 12 "Revolutionary"', 1831, 'etude revolutionary virtuosic'),
    P('Chopin', r'^Ballade_No\.4_', None, 'Ballade No. 4 in F minor, Op. 52', 1842, 'ballade dramatic'),
    P('Chopin', r'^Waltzes,_Op\.64$', r'^Waltz_No\.7_', 'Waltz in C-sharp minor, Op. 64 No. 2', 1847, 'waltz lyrical'),
    P('Chopin', r'^Polonaise_No\.6_', None, 'Polonaise in A-flat major, Op. 53 "Heroic"', 1842, 'polonaise heroic dramatic'),
    P('Chopin', r'^Nocturnes,_Op\.9$', r'^Nocturne_No\.1_', 'Nocturne in B-flat minor, Op. 9 No. 1', 1832, 'nocturne calm slow'),
    P('Chopin', r'^24_Préludes,_Op\.28$', r'^No\.15_', 'Prelude in D-flat major, Op. 28 No. 15 "Raindrop"', 1838, 'prelude raindrop calm slow'),
    P('Chopin', r'^12_Études,_Op\.10$', r'^No\.1_', 'Étude in C major, Op. 10 No. 1 "Waterfall"', 1830, 'etude virtuosic'),
    P('Chopin', r'^Waltz_No\.19_', None, 'Waltz in A minor, B. 150 (Op. posth.)', None, 'waltz calm'),
    P('Chopin', r'^Waltzes,_Op\.64$', r'Minute', 'Waltz in D-flat major, Op. 64 No. 1 "Minute Waltz"', 1847, 'waltz minute-waltz lively'),
    P('Chopin', r'^Scherzo_No\.2_', None, 'Scherzo No. 2 in B-flat minor, Op. 31', 1837, 'scherzo dramatic'),
    P('Chopin', r'^24_Préludes,_Op\.28$', r'^No\.4_', 'Prelude in E minor, Op. 28 No. 4', 1839, 'prelude calm slow'),
    P('Chopin', r'^Barcarolle_', None, 'Barcarolle in F-sharp major, Op. 60', 1846, 'barcarolle lyrical'),
    P('Chopin', r'^Ballade_No\.3_', None, 'Ballade No. 3 in A-flat major, Op. 47', 1841, 'ballade lyrical'),
    P('Chopin', r'^12_Études,_Op\.10$', r'^No\.3_', 'Étude in E major, Op. 10 No. 3 "Tristesse"', 1832, 'etude tristesse lyrical'),
    P('Chopin', r'^Nocturnes,_Op\.27$', r'^Nocturne_No\.8_', 'Nocturne in D-flat major, Op. 27 No. 2', 1836, 'nocturne calm'),
    P('Chopin', r'^Nocturnes,_Op\.48$', r'^Nocturne_No\.13_', 'Nocturne in C minor, Op. 48 No. 1', 1841, 'nocturne dramatic'),
    P('Chopin', r'^12_Études,_Op\.25$', r'^No\.11_', 'Étude in A minor, Op. 25 No. 11 "Winter Wind"', 1836, 'etude winter-wind virtuosic'),
    P('Chopin', r'^Ballade_No\.2_', None, 'Ballade No. 2 in F major, Op. 38', 1839, 'ballade dramatic'),
    P('Chopin', r'^Waltz_No\.1_in_E_flat', None, 'Grande valse brillante in E-flat major, Op. 18', 1833, 'waltz lively'),
    P('Chopin', r'^Piano_Sonata_No\.2_', r'Marche_fun', 'Piano Sonata No. 2 in B-flat minor, Op. 35: III. Marche funèbre', 1837, 'sonata funeral-march slow'),
    P('Chopin', r'^24_Préludes,_Op\.28$', r'^No\.20_', 'Prelude in C minor, Op. 28 No. 20', 1839, 'prelude slow'),
    P('Chopin', r'^Waltzes,_Op\.69$', r'^Waltz_No\.9_', 'Waltz in A-flat major, Op. 69 No. 1 "Farewell"', 1835, 'waltz farewell lyrical'),
    P('Chopin', r'^Polonaises,_Op\.40$', r'Military', 'Polonaise in A major, Op. 40 No. 1 "Military"', 1838, 'polonaise military lively'),
    P('Chopin', r'^Nocturne_No\.19_', None, 'Nocturne in E minor, Op. 72 No. 1', 1827, 'nocturne lyrical'),
    P('Chopin', r'^24_Préludes,_Op\.28$', r'^No\.7_', 'Prelude in A major, Op. 28 No. 7', 1839, 'prelude calm'),
    # ------------------------------------------------------------------ Beethoven (18)
    P('Beethoven', r'Op\.27_No\.2', r'^1\.', 'Piano Sonata No. 14 in C-sharp minor, Op. 27 No. 2 "Moonlight": I. Adagio sostenuto', 1801, 'sonata moonlight calm slow'),
    P('Beethoven', r'Op\.27_No\.2', r'^2\.', 'Piano Sonata No. 14 in C-sharp minor, Op. 27 No. 2 "Moonlight": II. Allegretto', 1801, 'sonata moonlight lively'),
    P('Beethoven', r'Op\.27_No\.2', r'^3\.', 'Piano Sonata No. 14 in C-sharp minor, Op. 27 No. 2 "Moonlight": III. Presto agitato', 1801, 'sonata moonlight dramatic virtuosic'),
    P('Beethoven', r'^Für_Elise', None, 'Für Elise (Bagatelle in A minor), WoO 59', 1810, 'bagatelle fur-elise lyrical'),
    P('Beethoven', r'Op\.13_', r'^2\.', 'Piano Sonata No. 8 in C minor, Op. 13 "Pathétique": II. Adagio cantabile', 1798, 'sonata pathetique calm slow'),
    P('Beethoven', r'Op\.13_', r'^3\.', 'Piano Sonata No. 8 in C minor, Op. 13 "Pathétique": III. Rondo. Allegro', 1798, 'sonata pathetique lively'),
    P('Beethoven', r'Op\.13_', r'^1\.', 'Piano Sonata No. 8 in C minor, Op. 13 "Pathétique": I. Grave – Allegro di molto e con brio', 1798, 'sonata pathetique dramatic'),
    P('Beethoven', r'Op\.57_', r'^1\.', 'Piano Sonata No. 23 in F minor, Op. 57 "Appassionata": I. Allegro assai', 1805, 'sonata appassionata dramatic'),
    P('Beethoven', r'Op\.57_', r'^2\.', 'Piano Sonata No. 23 in F minor, Op. 57 "Appassionata": II. Andante con moto', 1805, 'sonata appassionata variations calm'),
    P('Beethoven', r'Op\.57_', r'^3\.', 'Piano Sonata No. 23 in F minor, Op. 57 "Appassionata": III. Allegro ma non troppo – Presto', 1805, 'sonata appassionata dramatic virtuosic'),
    P('Beethoven', r'Op\.53,', r'^1\.', 'Piano Sonata No. 21 in C major, Op. 53 "Waldstein": I. Allegro con brio', 1804, 'sonata waldstein lively'),
    P('Beethoven', r'Op\.53,', r'^3\.', 'Piano Sonata No. 21 in C major, Op. 53 "Waldstein": III. Rondo. Allegretto moderato – Prestissimo', 1804, 'sonata waldstein rondo lyrical'),
    P('Beethoven', r'Op\.31_No\.2', r'^1\.', 'Piano Sonata No. 17 in D minor, Op. 31 No. 2 "Tempest": I. Largo – Allegro', 1802, 'sonata tempest dramatic'),
    P('Beethoven', r'Op\.31_No\.2', r'^3\.', 'Piano Sonata No. 17 in D minor, Op. 31 No. 2 "Tempest": III. Allegretto', 1802, 'sonata tempest lively'),
    P('Beethoven', r'Op\.81a', r'^1\.', 'Piano Sonata No. 26 in E-flat major, Op. 81a "Les Adieux": I. Das Lebewohl', 1810, 'sonata les-adieux'),
    P('Beethoven', r'Op\.110$', r'^1\.', 'Piano Sonata No. 31 in A-flat major, Op. 110: I. Moderato cantabile molto espressivo', 1821, 'sonata lyrical'),
    P('Beethoven', r'Op\.49_No\.2', r'^1\.', 'Piano Sonata No. 20 in G major, Op. 49 No. 2: I. Allegro ma non troppo', 1796, 'sonata sonatina lively'),
    P('Beethoven', r'^Rondo_a_capriccio', None, 'Rondo a capriccio in G major, Op. 129 "Rage over a Lost Penny"', 1795, 'rondo rage-over-a-lost-penny lively'),
    # ------------------------------------------------------------------ Baroque
    P('Bach,_Johann_S', r'^The_Well-Tempered_Clavier,_Book_I,', r'^No\.1_in_C_major,_BWV_846:_Prelude', 'Prelude in C major, BWV 846 (The Well-Tempered Clavier, Book I)', 1722, 'prelude well-tempered-clavier calm'),
    P('Bach,_Johann_S', r'^The_Well-Tempered_Clavier,_Book_I,', r'^No\.2_in_C_minor,_BWV_847:_Prelude', 'Prelude in C minor, BWV 847 (The Well-Tempered Clavier, Book I)', 1722, 'prelude well-tempered-clavier lively'),
    P('Bach,_Johann_S', r'^15_Inventions', r'^Invention_No\.1_', 'Invention No. 1 in C major, BWV 772', 1723, 'invention counterpoint lively'),
    P('Bach,_Johann_S', r'^15_Inventions', r'^Invention_No\.4_', 'Invention No. 4 in D minor, BWV 775', 1723, 'invention counterpoint lively'),
    P('Bach,_Johann_S', r'^15_Inventions', r'^Invention_No\.8_', 'Invention No. 8 in F major, BWV 779', 1723, 'invention counterpoint lively'),
    P('Bach,_Johann_S', r'^15_Inventions', r'^Invention_No\.13_', 'Invention No. 13 in A minor, BWV 784', 1723, 'invention counterpoint'),
    P('Bach,_Johann_S', r'^Goldberg_Variations', r'^Aria$', 'Goldberg Variations, BWV 988: Aria', 1741, 'aria goldberg calm slow',
      pick='ATEPP_02273'),   # Glenn Gould
    P('Bach,_Johann_S', r'^Herz_und_Mund_und_Tat_und_Leben,_BWV_147$', r'Jesus_bleibet', "Jesu, Joy of Man's Desiring (from BWV 147, arr. for piano)", 1723, 'chorale arrangement calm', rank=0.85),
    P('Bach,_Johann_S', r'^Oboe_Concerto_in_D_minor', r'^2\.', 'Concerto in D minor after Marcello, BWV 974: II. Adagio', None, 'concerto arrangement calm slow'),
    P('Bach,_Johann_S', r'^Ich_ruf_zu_dir', None, 'Ich ruf zu dir, Herr Jesu Christ, BWV 639 (arr. Busoni)', None, 'chorale-prelude arrangement calm slow'),
    P('Bach,_Carl', r'^Solfeggio', None, 'Solfeggietto in C minor, H. 220', 1766, 'solfeggietto lively'),
    P('Pezold', r'^Minuet_in_G_major', None, 'Minuet in G major, BWV Anh. 114', 1725, 'minuet anna-magdalena calm', rank=0.85),
    P('Scarlatti', r'K\.380$', None, 'Keyboard Sonata in E major, K. 380', None, 'sonata lively'),
    P('Scarlatti', r'K\.466$', None, 'Keyboard Sonata in F minor, K. 466', None, 'sonata calm'),
    P('Scarlatti', r'K\.27$', None, 'Keyboard Sonata in B minor, K. 27', 1738, 'sonata lively'),
    P('Handel', r'HWV_430', r'Air', 'Suite No. 5 in E major, HWV 430: Air and Variations "The Harmonious Blacksmith"', 1720, 'suite variations harmonious-blacksmith'),
    P('Handel', r'HWV_432', r'Passacaille', 'Suite No. 7 in G minor, HWV 432: Passacaille', 1720, 'suite passacaglia dramatic'),
    P('Pachelbel', r'^Canon_and_Gigue', r'^1\._Canon$', 'Canon in D (arr. for piano)', None, 'canon arrangement calm',
      pick='Aria_053846_0', rank=0.85),   # Aria-only piece: the take with the widest dynamics (17 levels)
    # ------------------------------------------------------------------ Classical
    P('Haydn', r'Hob\.XVI:50', r'^1\.', 'Piano Sonata in C major, Hob. XVI:50: I. Allegro', 1795, 'sonata lively'),
    P('Haydn', None, None, 'Piano Sonata in C major, Hob. XVI:35: I. Allegro con brio', 1780, 'sonata lively', pmd='midis/haydn/haydn_35_1.mid', rank=0.6),
    P('Mozart', r'K\.331', r'^3\.', 'Piano Sonata No. 11 in A major, K. 331: III. Rondo alla Turca', 1783, 'sonata alla-turca rondo lively'),
    P('Mozart', r'K\.331', r'^1\.', 'Piano Sonata No. 11 in A major, K. 331: I. Andante grazioso con variazioni', 1783, 'sonata variations calm'),
    P('Mozart', r'K\.545', r'^1\.', 'Piano Sonata No. 16 in C major, K. 545 "Sonata facile": I. Allegro', 1788, 'sonata sonata-facile lively'),
    P('Mozart', r'K\.545', r'^2\.', 'Piano Sonata No. 16 in C major, K. 545 "Sonata facile": II. Andante', 1788, 'sonata sonata-facile calm'),
    P('Mozart', r'K\.545', r'^3\.', 'Piano Sonata No. 16 in C major, K. 545 "Sonata facile": III. Rondo. Allegretto', 1788, 'sonata sonata-facile rondo lively'),
    P('Mozart', r'^Fantasia_in_D_minor', None, 'Fantasia in D minor, K. 397', 1782, 'fantasia dramatic'),
    P('Mozart', r'^Twelve_Variations', None, 'Twelve Variations on "Ah, vous dirai-je, Maman", K. 265', 1781, 'variations twinkle lively'),
    P('Mozart', r'K\.330', r'^1\.', 'Piano Sonata No. 10 in C major, K. 330: I. Allegro moderato', 1783, 'sonata lively'),
    P('Mozart', r'K\.332', r'^1\.', 'Piano Sonata No. 12 in F major, K. 332: I. Allegro', 1783, 'sonata lively'),
    P('Mozart', r'^Rondo_in_D_major', None, 'Rondo in D major, K. 485', 1786, 'rondo lively'),
    P('Clementi', r'Op\.36,_No\.1$', r'Spiritoso', 'Sonatina in C major, Op. 36 No. 1: I. Spiritoso', 1797, 'sonatina lively',
      pmd='midis/clementi/clementi_opus36_1_1.mid'),
    P('Clementi', None, None, 'Sonatina in C major, Op. 36 No. 3: I. Spiritoso', 1797, 'sonatina lively', pmd='midis/clementi/clementi_opus36_3_1.mid', rank=0.75),
    # ------------------------------------------------------------------ Schubert, Mendelssohn, Schumann
    P('Schubert', r'^4_Impromptus,_Op\.90', r'^3\.', 'Impromptu in G-flat major, D. 899 No. 3', 1827, 'impromptu lyrical calm'),
    P('Schubert', r'^4_Impromptus,_Op\.90', r'^4\.', 'Impromptu in A-flat major, D. 899 No. 4', 1827, 'impromptu lively'),
    P('Schubert', r'^4_Impromptus,_Op\.90', r'^1\.', 'Impromptu in C minor, D. 899 No. 1', 1827, 'impromptu dramatic'),
    P('Schubert', r'D\.935', r'^No\.2_', 'Impromptu in A-flat major, D. 935 No. 2', 1827, 'impromptu lyrical calm'),
    P('Schubert', r'D\.935', r'^No\.3_', 'Impromptu in B-flat major, D. 935 No. 3 (Theme and Variations)', 1827, 'impromptu variations rosamunde'),
    P('Schubert', r'D\.935', r'^No\.4_', 'Impromptu in F minor, D. 935 No. 4', 1827, 'impromptu lively'),
    P('Schubert', r'D\.780', r'^No\.3_', 'Moment musical in F minor, D. 780 No. 3', 1823, 'moment-musical lively'),
    P('Schubert', r'D\.960', r'^2\.', 'Piano Sonata in B-flat major, D. 960: II. Andante sostenuto', 1828, 'sonata calm slow'),
    P('Schubert', r'^Schwanengesang', r'Ständchen', 'Ständchen (Serenade), D. 957 No. 4 (arr. Liszt)', 1828, 'serenade song-transcription arrangement lyrical'),
    P('Mendelssohn', r'Op\.30$', r'^6\.', 'Song without Words in F-sharp minor, Op. 30 No. 6 "Venetian Gondola Song"', 1834, 'song-without-words gondola-song calm'),
    P('Mendelssohn', r'Op\.19$', r'^6\.', 'Song without Words in G minor, Op. 19 No. 6 "Venetian Gondola Song"', 1830, 'song-without-words gondola-song calm'),
    P('Mendelssohn', r'Op\.19$', r'^1\.', 'Song without Words in E major, Op. 19 No. 1 "Sweet Remembrance"', 1830, 'song-without-words lyrical'),
    P('Mendelssohn', r'Op\.62$', r'^6\.', 'Song without Words in A major, Op. 62 No. 6 "Spring Song"', 1842, 'song-without-words spring-song lyrical'),
    P('Mendelssohn', r'Op\.67$', r'^4\.', 'Song without Words in C major, Op. 67 No. 4 "Spinning Song"', 1845, "song-without-words spinning-song bees-wedding lively"),
    P('Mendelssohn', r'^Rondo_capriccioso', None, 'Rondo capriccioso in E major, Op. 14', 1830, 'rondo virtuosic'),
    P('Mendelssohn', r'^Variations_sérieuses', None, 'Variations sérieuses, Op. 54', 1841, 'variations dramatic'),
    P('Schumann,_R', r'^Kinderszenen', r'^7\.', 'Kinderszenen, Op. 15: No. 7, Träumerei', 1838, 'character-piece traumerei calm slow'),
    P('Schumann,_R', r'^Kinderszenen', r'^1\.', 'Kinderszenen, Op. 15: No. 1, Von fremden Ländern und Menschen', 1838, 'character-piece calm'),
    P('Schumann,_R', r'^Kinderszenen', r'^13\.', 'Kinderszenen, Op. 15: No. 13, Der Dichter spricht', 1838, 'character-piece calm slow'),
    P('Schumann,_R', r'^Kinderszenen', r'^12\.', 'Kinderszenen, Op. 15: No. 12, Kind im Einschlummern', 1838, 'character-piece calm slow'),
    P('Schumann,_R', r'^8_Fantasiestücke', r'^2\.', 'Fantasiestücke, Op. 12: No. 2, Aufschwung', 1837, 'character-piece dramatic'),
    P('Schumann,_R', r'^8_Fantasiestücke', r'^1\.', 'Fantasiestücke, Op. 12: No. 1, Des Abends', 1837, 'character-piece calm'),
    P('Schumann,_R', r'^Fantasie_in_C_major', r'^1\.', 'Fantasie in C major, Op. 17: I. Durchaus fantastisch und leidenschaftlich vorzutragen', 1836, 'fantasy dramatic'),
    P('Schumann,_R', r'^Waldszenen', r'^7\.', 'Waldszenen, Op. 82: No. 7, Vogel als Prophet', 1849, 'character-piece calm'),
    P('Schumann,_R', r'^Album_für_die_Jugend', r'^10\.', 'Album for the Young, Op. 68: No. 10, The Happy Farmer', 1848, 'character-piece happy-farmer lively',
      pmd='midis/schumann/scn68_10.mid'),
    # ------------------------------------------------------------------ Liszt, Brahms
    P('Liszt', r'^Liebesträume', r'^3\.', 'Liebestraum No. 3 in A-flat major, S. 541', 1850, 'nocturne liebestraum lyrical'),
    P('Liszt', r'^Grandes_études_de_Paganini', r'^3\.', 'La campanella (Grandes études de Paganini, S. 141 No. 3)', 1851, 'etude campanella virtuosic'),
    P('Liszt', r'^Consolations', r'^3\.', 'Consolation No. 3 in D-flat major, S. 172', 1850, 'consolation calm slow'),
    P('Liszt', r'^3_Etudes_de_concert', r'^No\.3_', 'Un sospiro (Concert Étude No. 3 in D-flat major, S. 144)', 1848, 'etude un-sospiro lyrical'),
    P('Liszt', r'^Hungarian_Rhapsody_No\.2_', None, 'Hungarian Rhapsody No. 2 in C-sharp minor, S. 244', 1847, 'rhapsody hungarian virtuosic'),
    P('Liszt', r'^Mephisto_Waltz_No\.1', None, 'Mephisto Waltz No. 1, S. 514', 1860, 'waltz mephisto virtuosic'),
    P('Liszt', r'^Années_de_pèlerinage_II,', r'^5\.', 'Années de pèlerinage II: Sonetto 104 del Petrarca, S. 161 No. 5', 1858, 'sonnet lyrical'),
    P('Liszt', r'^2_Etudes_de_Concert', r'^2\.', 'Gnomenreigen (Concert Étude, S. 145 No. 2)', 1863, 'etude virtuosic lively'),
    P('Liszt', r'^Piano_Sonata_in_B_minor', None, 'Piano Sonata in B minor, S. 178', 1853, 'sonata dramatic virtuosic'),
    P('Liszt', r"^12_Etudes_d'exécution_transcendante", r'^10\.', 'Transcendental Étude No. 10 in F minor, S. 139', 1852, 'etude transcendental virtuosic'),
    P('Brahms', r'Op\.118$', r'^2\.', 'Intermezzo in A major, Op. 118 No. 2', 1893, 'intermezzo lyrical calm'),
    P('Brahms', None, None, 'Intermezzo in E-flat major, Op. 117 No. 1', 1892, 'intermezzo lullaby calm slow', pmd='midis/brahms/brahms_opus117_1.mid', rank=0.7),
    P('Brahms', r'^16_Waltzes,_Op\.39', r'^15\.', 'Waltz in A-flat major, Op. 39 No. 15', 1865, 'waltz calm'),
    P('Brahms', r'^2_Rhapsodies,_Op\.79', r'^1\.', 'Rhapsody in B minor, Op. 79 No. 1', 1879, 'rhapsody dramatic'),
    P('Brahms', r'Op\.119$', r'^1\.', 'Intermezzo in B minor, Op. 119 No. 1', 1893, 'intermezzo calm slow'),
    P('Brahms', r'Op\.118$', r'^6\.', 'Intermezzo in E-flat minor, Op. 118 No. 6', 1893, 'intermezzo dramatic slow'),
    P('Brahms', r'^4_Ballades,_Op\.10', r'^No\.1_', 'Ballade in D minor, Op. 10 No. 1 "Edward"', 1854, 'ballade dramatic'),
    # ------------------------------------------------------------------ Grieg, Tchaikovsky, Mussorgsky
    P('Grieg', r'Op\.54$', r'^4\.', 'Lyric Pieces, Op. 54 No. 4: Notturno', 1891, 'lyric-piece nocturne calm'),
    P('Grieg', r'Op\.12$', r'^1\.', 'Lyric Pieces, Op. 12 No. 1: Arietta', 1867, 'lyric-piece calm'),
    P('Grieg', r'Op\.43$', r'^1\.', 'Lyric Pieces, Op. 43 No. 1: Butterfly', 1886, 'lyric-piece butterfly lively'),
    P('Grieg', r'Op\.43$', r'^6\.', 'Lyric Pieces, Op. 43 No. 6: To Spring', 1886, 'lyric-piece to-spring lyrical'),
    P('Grieg', r'Op\.54$', r'^3\.', 'Lyric Pieces, Op. 54 No. 3: March of the Trolls', 1891, 'lyric-piece march trolls lively'),
    P('Grieg', r'Op\.65$', r'^6\.', 'Lyric Pieces, Op. 65 No. 6: Wedding Day at Troldhaugen', 1896, 'lyric-piece wedding-day lively',
      pmd='midis/grieg/grieg_wedding.mid', rank=0.75),
    P('Grieg', r'Op\.71$', r'^3\.', 'Lyric Pieces, Op. 71 No. 3: Puck', 1901, 'lyric-piece puck lively'),
    P('Grieg', r'^Peer_Gynt_Suite_No\.1', r'Mountain_King', 'Peer Gynt: In the Hall of the Mountain King (piano)', 1875, 'peer-gynt arrangement dramatic', rank=0.8),
    P('Tchaikovsky', r'^The_Seasons', r'^6\.', 'The Seasons, Op. 37a: June – Barcarolle', 1876, 'seasons barcarolle lyrical'),
    P('Tchaikovsky', r'^The_Seasons', r'^10\.', "The Seasons, Op. 37a: October – Autumn Song", 1876, 'seasons autumn-song calm slow'),
    P('Tchaikovsky', r'^The_Seasons', r'^4\.', 'The Seasons, Op. 37a: April – Snowdrop', 1876, 'seasons lyrical'),
    P('Tchaikovsky', r'^The_Seasons', r'^1\.', 'The Seasons, Op. 37a: January – By the Fireside', 1876, 'seasons calm'),
    P('Tchaikovsky', r'^The_Seasons', r'^12\.', 'The Seasons, Op. 37a: December – Christmas', 1876, 'seasons waltz'),
    P('Tchaikovsky', r'^The_Seasons', r'^5\.', 'The Seasons, Op. 37a: May – White Nights', 1876, 'seasons calm'),
    P('Tchaikovsky', r'^The_Seasons', r'^11\.', 'The Seasons, Op. 37a: November – Troika', 1876, 'seasons troika lively'),
    P('Mussorgsky', r'^Pictures_at_an_Exhibition', r'^Promenade$', 'Pictures at an Exhibition: Promenade', 1874, 'pictures-at-an-exhibition promenade'),
    P('Mussorgsky', r'^Pictures_at_an_Exhibition', r'^2\._Old_Castle', 'Pictures at an Exhibition: The Old Castle', 1874, 'pictures-at-an-exhibition calm'),
    P('Mussorgsky', r'^Pictures_at_an_Exhibition', r'^10\._The_Great_Gate', 'Pictures at an Exhibition: The Great Gate of Kiev', 1874, 'pictures-at-an-exhibition dramatic'),
    # ------------------------------------------------------------------ Debussy, Ravel, Satie
    P('Debussy', r'^Suite_bergamasque', r'^3\.', 'Suite bergamasque: III. Clair de lune', 1890, 'clair-de-lune calm'),
    P('Debussy', r'^2_Arabesques', r'^1\.', 'Arabesque No. 1 in E major', 1888, 'arabesque lyrical'),
    P('Debussy', r'^2_Arabesques', r'^2\.', 'Arabesque No. 2 in G major', 1888, 'arabesque lively'),
    P('Debussy', r'^Préludes,_Book_1', r'^8\.', 'Préludes, Book 1: La fille aux cheveux de lin', 1910, 'prelude calm slow'),
    P('Debussy', r'^Rêverie', None, 'Rêverie, L. 68', 1890, 'reverie calm'),
    P('Debussy', r"^L'Isle_joyeuse", None, "L'isle joyeuse", 1904, 'lively virtuosic'),
    P('Debussy', r"^Children's_Corner", r'^1\.', "Children's Corner: Doctor Gradus ad Parnassum", 1908, 'childrens-corner lively'),
    P('Debussy', r"^Children's_Corner", r'^6\.', "Children's Corner: Golliwogg's Cake-walk", 1908, 'childrens-corner cakewalk lively'),
    P('Debussy', r"^Children's_Corner", r'^5\.', "Children's Corner: The Little Shepherd", 1908, 'childrens-corner calm'),
    P('Debussy', r'^Estampes', r'^3\.', 'Estampes: Jardins sous la pluie', 1903, 'estampes lively'),
    P('Debussy', r'^Estampes', r'^1\.', 'Estampes: Pagodes', 1903, 'estampes calm'),
    P('Debussy', r'^Suite_bergamasque', r'^1\.', 'Suite bergamasque: I. Prélude', 1890, 'prelude lively'),
    P('Debussy', r'^Suite_bergamasque', r'^4\.', 'Suite bergamasque: IV. Passepied', 1890, 'dance lively'),
    P('Debussy', r'^Préludes,_Book_1', r'^10\.', 'Préludes, Book 1: La cathédrale engloutie', 1910, 'prelude cathedral calm slow'),
    P('Debussy', r'^La_plus_que_lente', None, 'La plus que lente', 1910, 'waltz calm'),
    P('Ravel', r'^Pavane_pour', None, 'Pavane pour une infante défunte', 1899, 'pavane calm slow'),
    P('Ravel', r"^Jeux_d'eau", None, "Jeux d'eau", 1901, 'water lively virtuosic'),
    P('Ravel', r'^Gaspard_de_la_nuit', r'^Ondine', 'Gaspard de la nuit: I. Ondine', 1908, 'gaspard virtuosic'),
    P('Ravel', r'^Gaspard_de_la_nuit', r'^Le_gibet', 'Gaspard de la nuit: II. Le gibet', 1908, 'gaspard calm slow'),
    P('Ravel', r'^Miroirs', r'^3\.', "Miroirs: III. Une barque sur l'océan", 1905, 'miroirs water'),
    P('Ravel', r'^Miroirs', r'^4\.', 'Miroirs: IV. Alborada del gracioso', 1905, 'miroirs lively virtuosic'),
    P('Ravel', r'^Sonatine', r'^1\.', 'Sonatine: I. Modéré', 1905, 'sonatine lyrical'),
    P('Ravel', r'^Le_Tombeau_de_Couperin', r'^6\.', 'Le Tombeau de Couperin: VI. Toccata', 1917, 'toccata virtuosic lively', era='20th century'),
    P('Satie', r'^3_Gymnopédies', r'^1\.', 'Gymnopédie No. 1', 1888, 'gymnopedie calm slow'),
    P('Satie', r'^3_Gymnopédies', r'^2\.', 'Gymnopédie No. 2', 1888, 'gymnopedie calm slow'),
    P('Satie', r'^3_Gymnopédies', r'^3\.', 'Gymnopédie No. 3', 1888, 'gymnopedie calm slow'),
    P('Satie', r'^Gnossiennes', r'^1\.', 'Gnossienne No. 1', 1890, 'gnossienne calm slow'),
    P('Satie', r'^Gnossiennes', r'^2\.', 'Gnossienne No. 2', 1890, 'gnossienne calm slow'),
    P('Satie', r'^Gnossiennes', r'^3\.', 'Gnossienne No. 3', 1890, 'gnossienne calm slow'),
    P('Satie', r'^Je_te_veux', None, 'Je te veux (valse)', 1897, 'waltz lyrical'),
    # ------------------------------------------------------------------ Rachmaninoff, Scriabin, Prokofiev, Bartok
    P('Rachmaninoff', r'^Morceaux_de_Fantasie', r'^2\.', 'Prelude in C-sharp minor, Op. 3 No. 2', 1892, 'prelude bells dramatic'),
    P('Rachmaninoff', r'^10_Preludes,_Op\.23', r'^No\.5_', 'Prelude in G minor, Op. 23 No. 5', 1901, 'prelude march dramatic'),
    P('Rachmaninoff', r'^13_Preludes,_Op\.32', r'^No\.12_', 'Prelude in G-sharp minor, Op. 32 No. 12', 1910, 'prelude lyrical'),
    P('Rachmaninoff', r'^10_Preludes,_Op\.23', r'^No\.4_', 'Prelude in D major, Op. 23 No. 4', 1903, 'prelude calm'),
    P('Rachmaninoff', r'^Morceaux_de_Fantasie', r'^1\.', 'Élégie in E-flat minor, Op. 3 No. 1', 1892, 'elegy lyrical slow'),
    P('Rachmaninoff', r'^6_Moments_musicaux', r'^No\.4_', 'Moment musical in E minor, Op. 16 No. 4', 1896, 'moment-musical virtuosic'),
    P('Rachmaninoff', r'^Etudes-Tableaux,_Op\.39', r'^No\.5_', 'Étude-tableau in E-flat minor, Op. 39 No. 5', 1917, 'etude dramatic', era='20th century'),
    P('Scriabin', r'^12_Etudes,_Op\.8', r'^No\.12_', 'Étude in D-sharp minor, Op. 8 No. 12', 1894, 'etude dramatic virtuosic'),
    P('Scriabin', r'^3_Pieces,_Op\.2', r'^1\.', 'Étude in C-sharp minor, Op. 2 No. 1', 1887, 'etude lyrical slow'),
    P('Scriabin', r'^8_Etudes,_Op\.42', r'^No\.5_', 'Étude in C-sharp minor, Op. 42 No. 5', 1903, 'etude dramatic', era='20th century'),
    P('Prokofiev', r'^Toccata,_Op\.11', None, 'Toccata in D minor, Op. 11', 1912, 'toccata virtuosic'),
    P('Prokofiev', r'Romeo_and_Juliet', r'^6\.', 'Romeo and Juliet, Op. 75: Montagues and Capulets', 1937, 'ballet arrangement dramatic'),
    P('Bartók', r'^Romanian_Folk_Dances,_Sz\.56$', None, 'Romanian Folk Dances, Sz. 56', 1915, 'folk-dance lively'),
    # ------------------------------------------------------------------ Spanish and other favourites
    P('Albéniz', r'^Suite_española_No\.1', r'Asturias', 'Suite española No. 1, Op. 47: Asturias (Leyenda)', 1892, 'spanish asturias dramatic', rank=0.75),
    P('Albéniz', r'^España,_Op\.165', r'^2\.', 'España, Op. 165: No. 2, Tango', 1890, 'spanish tango calm'),
    P('Albéniz', r'^Iberia', r'^1\.', 'Iberia, Book 1: Evocación', 1906, 'spanish calm', era='20th century'),
    P('Falla', r'^El_amor_brujo', r'Ritual', 'Ritual Fire Dance (El amor brujo)', 1915, 'spanish dance arrangement dramatic'),
    P('Field', r'^Nocturne_No\.5_', None, 'Nocturne No. 5 in B-flat major, H. 37', 1817, 'nocturne calm'),
    P('Burgmüller', r'^25_Études_faciles', r'^2\.', "25 Easy Études, Op. 100: No. 2, L'Arabesque", 1851, 'etude arabesque lively'),
    P('Elgar', r"^Salut_d'amour", None, "Salut d'amour, Op. 12", 1888, 'salon lyrical'),
    P('Dvořák', r'^8_Humoresques', r'^7\.', 'Humoresque in G-flat major, Op. 101 No. 7', 1894, 'humoresque lyrical'),
    P('Saint-Saëns', r'^Le_carnaval_des_animaux', r'cygne', 'The Swan (Le carnaval des animaux, arr. for piano)', 1886, 'swan arrangement calm', rank=0.75),
    P('Rimsky-Korsakov', r'Saltan', r'Bumblebee', 'Flight of the Bumblebee (arr. for piano)', 1900, 'bumblebee arrangement virtuosic', rank=0.8),
    P('MacDowell', r'^10_Woodland_Sketches', r'^1\.', 'Woodland Sketches, Op. 51: No. 1, To a Wild Rose', 1896, 'character-piece calm'),
    P('Balakirev', r'^Islamey', None, 'Islamey (Oriental Fantasy), Op. 18', 1869, 'fantasy virtuosic'),
    P('Sinding', None, None, 'Rustle of Spring, Op. 32 No. 3', 1896, 'character-piece rustle-of-spring lively', pmd='midis/sinding/fruehlingsrauschen.mid', rank=0.7),
]

# Titles already in config/live/test-piano (MAESTRO takes) - never staged here.
ALREADY_IN_TEST_PIANO = [
    'Bach P&F F minor WTC I BWV 857', 'Chopin Berceuse Op.57', 'Chopin Etude Op.10 No.5', 'Chopin Nocturne Op.9 No.2',
    'Debussy La terrasse des audiences', "Debussy Reflets dans l'eau", 'Grieg Op.12 No.2 Waltz',
    'Rachmaninoff Prelude Op.32 No.8', 'Schubert Impromptu D.899 No.2', 'Schumann Arabeske Op.18']


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------------------------- http
class NotFound(Exception):
    pass


def _wait(host):
    d = HOST_DELAY.get(host, DEFAULT_DELAY)
    t = _last.get(host, 0) + d - time.time()
    if t > 0:
        time.sleep(t)
    _last[host] = time.time()


def http(url, rng=None, tries=6, timeout=120):
    """GET url (optionally a byte range) -> (status, bytes, headers). NotFound on 404/410; backoff on the rest."""
    host = urllib.parse.urlsplit(url).hostname or ''
    hdr = {'User-Agent': UA, 'Accept': '*/*'}
    if rng:
        hdr['Range'] = 'bytes=%d-%d' % rng
    delay = 4.0
    for i in range(tries):
        _wait(host)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=timeout) as r:
                data = r.read()
                BYTES['n'] += len(data)
                if rng and r.status != 206:
                    raise RuntimeError(f'no range support at {url} (HTTP {r.status})')
                return r.status, data, r.headers
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise NotFound(url)
            if i == tries - 1:
                raise
            ra = e.headers.get('Retry-After') if e.headers else None
            wait = float(ra) if ra and ra.isdigit() else delay
            log(f'  HTTP {e.code} {url[:90]} - retry in {wait:.0f}s')
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if i == tries - 1:
                raise
            log(f'  {type(e).__name__} {url[:90]} - retry in {delay:.0f}s')
            time.sleep(delay)
        delay = min(delay * 2, 120)


def cpath(*parts):
    p = os.path.join(CACHE, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def write_atomic(path, data, mode='wb'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.part', mode) as f:
        f.write(data)
    os.replace(path + '.part', path)


def ascii_slug(s, n=70):
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c)).replace('ß', 'ss').replace('ø', 'o').replace('æ', 'ae')
    s = re.sub(r'[^A-Za-z0-9]+', '-', s).strip('-').lower()
    return s[:n].rstrip('-')


# --------------------------------------------------------------------------- remote zip (Range reads)
def rz_size(url):
    _, _, h = http(url, rng=(0, 0))
    return int(h['Content-Range'].split('/')[-1])


def rz_central(url, cache_file):
    """A remote zip's central directory -> {member name: (method, csize, offset)}; cached (gzip JSON)."""
    if os.path.exists(cache_file):
        with gzip.open(cache_file, 'rt', encoding='utf-8') as f:
            return {k: tuple(v) for k, v in json.load(f).items()}
    n = rz_size(url)
    tail_len = min(n, 65536 + 22)
    _, tail, _ = http(url, rng=(n - tail_len, n - 1))
    i = tail.rfind(b'PK\x05\x06')
    cd_entries = struct.unpack('<H', tail[i + 10:i + 12])[0]
    cd_size, cd_off = struct.unpack('<II', tail[i + 12:i + 20])
    if cd_off == 0xFFFFFFFF or cd_entries == 0xFFFF or cd_size == 0xFFFFFFFF:
        j = tail.rfind(b'PK\x06\x07')
        z64off = struct.unpack('<Q', tail[j + 8:j + 16])[0]
        _, rec, _ = http(url, rng=(z64off, z64off + 55))
        cd_size, cd_off = struct.unpack('<QQ', rec[40:56])
    log(f'  reading zip directory: {cd_size / 1e6:.1f} MB')
    chunks, pos = [], cd_off
    while pos < cd_off + cd_size:
        end = min(cd_off + cd_size, pos + 16 * 1024 * 1024) - 1
        _, part, _ = http(url, rng=(pos, end), timeout=300)
        chunks.append(part)
        pos = end + 1
    cd = b''.join(chunks)
    out, p = {}, 0
    while p < len(cd) and cd[p:p + 4] == b'PK\x01\x02':
        meth, = struct.unpack('<H', cd[p + 10:p + 12])
        csz, usz = struct.unpack('<II', cd[p + 20:p + 28])
        nl, el, cl = struct.unpack('<HHH', cd[p + 28:p + 34])
        lho, = struct.unpack('<I', cd[p + 42:p + 46])
        name = cd[p + 46:p + 46 + nl].decode('utf-8', 'replace')
        extra = cd[p + 46 + nl:p + 46 + nl + el]
        if 0xFFFFFFFF in (csz, usz, lho):
            q = 0
            while q + 4 <= len(extra):
                hid, hl = struct.unpack('<HH', extra[q:q + 4])
                if hid == 1:
                    vals = list(struct.unpack('<' + 'Q' * (hl // 8), extra[q + 4:q + 4 + (hl // 8) * 8]))
                    k = 0
                    if usz == 0xFFFFFFFF:
                        usz = vals[k]; k += 1
                    if csz == 0xFFFFFFFF:
                        csz = vals[k]; k += 1
                    if lho == 0xFFFFFFFF:
                        lho = vals[k]; k += 1
                q += 4 + hl
        if name.lower().endswith(('.mid', '.midi')):
            out[name] = (meth, csz, lho)
        p += 46 + nl + el + cl
    with gzip.open(cache_file + '.part', 'wt', encoding='utf-8') as f:
        json.dump(out, f)
    os.replace(cache_file + '.part', cache_file)
    return out


def zip_member(zipdir, path):
    """PianoCoRe's performance_midi_path -> its member name in the raw-midi zip (PianoCoRe/raw/<path>)."""
    for m in (path, 'PianoCoRe/raw/' + path):
        if m in zipdir:
            return m
    return next((m for m in zipdir if m.endswith('/' + path)), None)


def rz_get(url, entry):
    meth, csz, lho = entry
    _, hdr, _ = http(url, rng=(lho, lho + 29))
    nl, el = struct.unpack('<HH', hdr[26:30])
    start = lho + 30 + nl + el
    _, data, _ = http(url, rng=(start, start + csz - 1))
    if meth == 8:
        data = zlib.decompress(data, -15)
    elif meth != 0:
        raise ValueError(f'zip method {meth}')
    return data


# ------------------------------------------------------------------------------ PianoCoRe index
class _HttpFile(io.RawIOBase):
    def __init__(self, url):
        self.url, self.size, self.pos = url, rz_size(url), 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n == 0 or self.pos >= self.size:
            return b''
        end = min(self.size, self.pos + n) - 1
        _, data, _ = http(self.url, rng=(self.pos, end), timeout=300)
        self.pos += len(data)
        return data

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def pcr_rows():
    """All PianoCoRe metadata rows (one cached CSV per parquet shard; built by Range-reading only the metadata
    columns of the 18 Hugging Face parquet files, about 15 MB in all)."""
    rows = []
    for shard in PCR_SHARDS:
        p = cpath('pianocore_meta', shard + '.csv')
        if not os.path.exists(p):
            import pyarrow.parquet as pq  # only needed to rebuild the index
            log(f'  PianoCoRe metadata {shard} ...')
            t = pq.ParquetFile(_HttpFile(PCR_PARQUET.format(shard))).read(columns=PCR_COLS)
            cols = {c: t.column(c).to_pylist() for c in PCR_COLS}
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(PCR_COLS)
            for i in range(t.num_rows):
                w.writerow([cols[c][i] for c in PCR_COLS])
            write_atomic(p, buf.getvalue(), mode='w')
        with open(p, encoding='utf-8') as f:
            rows += list(csv.DictReader(f))
    return rows


def _tb(v):
    return str(v).strip().lower() in ('true', '1')


def clean_performer(perf, composer_key):
    perf = (perf or '').strip()
    if not perf or perf == 'None':
        return None
    low = perf.lower()
    if composer_key.split(',_')[0].lower() in low or any(b in low for b in BAD_PERFORMER):
        return None
    return perf


def score(r, composer_key):
    ds = r['performance_dataset']
    perf = clean_performer(r['performer'], composer_key)
    if ds == 'ASAP':
        tier = 5
    elif ds == 'ATEPP' and r['performer'] in GOOD_ATEPP:
        tier = 4
    elif ds == 'PERiScoPe':
        tier = 3
    elif ds == 'GiantMIDI-Piano':
        tier = 2.5
    elif ds == 'ATEPP':
        tier = 2
    else:
        tier = 0   # Aria-MIDI
    raw = (r['performer'] or '').lower()
    if tier > 1 and raw and any(b in raw for b in BAD_PERFORMER):
        tier = 1   # a channel / compilation upload: below piano-midi.de
    s = float(r['prob_high_quality'] or 0)
    s += 0.2 if _tb(r['tier_a_star']) else 0
    s += 0.1 if perf and ' ' in perf else 0
    return (tier, s)


def candidates(rows):
    """Eligible rows for one piece, best first."""
    hq = [r for r in rows if r['quality_label'] == 'high quality' and not _tb(r['is_duplicate'])
          and '_mini' not in r['performance_midi_path']]
    durs = [float(r['performance_duration'] or 0) for r in hq if float(r['performance_duration'] or 0) > 0
            and r['performance_dataset'] != 'Aria-MIDI']   # Aria rows include partial segments
    durs = durs or [float(r['performance_duration'] or 0) for r in hq if float(r['performance_duration'] or 0) > 0]
    if not durs:
        return []
    med = statistics.median(durs)
    ok = [r for r in hq if 0.6 * med <= float(r['performance_duration'] or 0) <= 1.7 * med]
    ck = rows[0]['composer']
    return sorted(ok, key=lambda r: score(r, ck), reverse=True)


def rank_of(n):
    return round(max(0.3, min(1.0, 0.25 + 0.75 * math.log(max(n, 1)) / math.log(2500))), 3)


# ------------------------------------------------------------------------------------- planning
def plan(only=None):
    rows = pcr_rows()
    index = {}
    for r in rows:
        k = (r['composer'], r['composition'], '' if r['movement'] in ('None', None) else r['movement'])
        index.setdefault(k, []).append(r)
    out, gaps = [], []
    for spec in PIECES:
        if only and only.lower() not in spec['title'].lower():
            continue
        item = {'spec': spec}
        if spec['comp'] is not None:
            keys = [k for k in index if k[0].startswith(spec['composer']) and re.search(spec['comp'], k[1])
                    and ((spec['mv'] is None and k[2] == '') or (spec['mv'] is not None and re.search(spec['mv'], k[2])))]
            if len(keys) != 1:
                gaps.append((spec['title'], f'{len(keys)} PianoCoRe keys match: {keys[:4]}'))
                continue
            key = keys[0]
            item['key'] = key
            item['n_perf'] = len(index[key])
            cands = candidates(index[key])
            if spec.get('pick'):
                cands = [r for r in index[key] if r['performance_id'] == spec['pick']]
            best = cands[0] if cands else None
            tier = score(best, key[0])[0] if best else -1
            if best and (tier >= 2 or not spec.get('pmd')) and (tier > 0 or spec.get('aria_ok', True)):
                item['row'] = best
                item['source'] = best['performance_dataset']
            elif spec.get('pmd'):
                item['source'] = 'piano-midi.de'
            else:
                gaps.append((spec['title'], 'no eligible take'))
                continue
        else:
            item['source'] = 'piano-midi.de'
        out.append(item)
    return out, gaps


# -------------------------------------------------------------------------------------- fetching
def midi_stats(data):
    import mido
    mf = mido.MidiFile(file=io.BytesIO(data))
    notes = sum(1 for tr in mf.tracks for m in tr if m.type == 'note_on' and m.velocity > 0)
    meta = [getattr(m, 'text', getattr(m, 'name', '')) for tr in mf.tracks for m in tr
            if m.is_meta and m.type in ('text', 'track_name', 'copyright')]
    return round(mf.length, 2), notes, meta


def asap_performer(meta):
    """The pianist's name from an e-Competition file's text ('... performed on the Yamaha Disklavier by NAME--date')
    or track name ('Title--First Last'); None when only a surname or nothing usable is there."""
    for t in meta:
        m = re.search(r'performed on the Yamaha Disklavier by (.+?)(?:--|$)', t or '')
        if m and ' ' in m.group(1).strip():
            return m.group(1).strip()
    for t in meta:
        m = re.search(r'--\s*([^\W\d_][^\d/]*\s[^\d/]+)$', (t or '').strip())
        if m:
            return m.group(1).strip()
    return None


def composer_key(spec, item):
    if 'key' in item:
        return item['key'][0]
    for k in COMPOSERS:
        if k.startswith(spec['composer']):
            return k
    raise KeyError(spec['composer'])


def fetch(items, dry=False):
    os.makedirs(FILES, exist_ok=True)
    zipdir = None
    manifest = []
    for n, item in enumerate(items, 1):
        spec, src = item['spec'], item['source']
        ckey = composer_key(spec, item)
        cname, era = COMPOSERS[ckey]
        era = spec.get('era', era)
        surname = ascii_slug(cname.split()[-1], 20)
        if src == 'piano-midi.de':
            ref = PMD + spec['pmd']
            tag = 'pmd-' + ascii_slug(os.path.splitext(os.path.basename(spec['pmd']))[0], 30)
            performer, take = 'Bernd Krueger', 'sequenced'
        else:
            r = item['row']
            ref = f"{r['id']} {r['performance_id']} ({r['performance_midi_path']})"
            tag = ascii_slug(r['performance_id'], 40)
            performer = clean_performer(r['performer'], ckey)
            take = 'performance' if src == 'ASAP' else 'transcription'
        name = f"{surname}_{ascii_slug(spec['title'], 70)}__{tag}.mid"
        dst = os.path.join(FILES, name)
        log(f'[{n}/{len(items)}] {cname}: {spec["title"]}  <- {src} {tag}')
        if dry:
            continue
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            data = open(dst, 'rb').read()
        else:
            if src == 'piano-midi.de':
                _, data, _ = http(ref)
            else:
                if zipdir is None:
                    zipdir = rz_central(PCR_ZIP, cpath('pianocore_raw_midi_zip_listing.json.gz'))
                member = zip_member(zipdir, item['row']['performance_midi_path'])
                if not member:
                    log(f"  !! not in zip: {item['row']['performance_midi_path']}")
                    continue
                data = rz_get(PCR_ZIP, zipdir[member])
            if data[:4] != b'MThd':
                log(f'  !! not a MIDI file ({len(data)} bytes)')
                continue
            write_atomic(dst, data)
        try:
            dur, notes, meta = midi_stats(data)
        except Exception as e:  # noqa: BLE001
            log(f'  !! mido cannot parse: {e}')
            os.remove(dst)
            continue
        if notes < 16:
            log(f'  !! only {notes} notes')
            os.remove(dst)
            continue
        if src == 'ASAP':
            performer = asap_performer(meta) or performer
        if src != 'piano-midi.de':
            pd = float(item['row']['performance_duration'] or 0)
            if pd and abs(dur - pd) > max(5, 0.1 * pd):
                log(f'  ?? duration {dur:.0f}s vs index {pd:.0f}s')
        rank = spec.get('rank') or rank_of(item.get('n_perf', 1))
        manifest.append({
            'src': 'files/' + name,
            'title': spec['title'],
            'composer': cname,
            'performer': performer,
            'genre': 'classical',
            'era': era,
            'year': spec['year'],
            'source': SOURCE_NAME[src],
            'source_ref': ref,
            'licence': LICENCE[src],
            'take': take,
            'tags': spec['tags'],
            'rank': rank,
            'duration_s': dur,
            'notes': notes,
        })
    return manifest


# ------------------------------------------------------------------------------------ write-out
def write_sources(manifest, gaps):
    from collections import Counter
    by_src = Counter(m['source'] for m in manifest)
    by_take = Counter(m['take'] for m in manifest)
    lines = [
        '# Classical shelf: sources and licences', '',
        f'{len(manifest)} movements, one take each, staged by tools/music_library/fetch_classical.py '
        f'({time.strftime("%Y-%m-%d")}). Private, non-commercial use in this house only; never redistributed.', '',
        '"take" in manifest.json: performance = MIDI captured from a real piano (Yamaha Disklavier); '
        'transcription = audio-to-MIDI transcription of a recorded human performance; '
        'sequenced = hand-entered, expressive MIDI (velocities, tempo map, pedal).', '',
        'Counts by source: ' + ', '.join(f'{k} {v}' for k, v in by_src.most_common()) + '.',
        'Counts by take: ' + ', '.join(f'{k} {v}' for k, v in by_take.most_common()) + '.', '',
        '## PianoCoRe v1.0 (index and file store)', '',
        'Combined and Refined Piano MIDI dataset (SyMuPe, 2026): 249,841 performances of 5,611 movement-level pieces, '
        'with canonical composer/work/movement names and quality labels. Used to rank pieces (number of recorded '
        'performances per movement) and to choose the take; every performance file below was range-read from its '
        'raw-midi zip. Licence: **CC BY-NC-SA 4.0** (respects each source dataset\'s licence).',
        'URL: https://zenodo.org/records/19186016 (PianoCoRe-1.0-raw-midi.zip); index: '
        'https://huggingface.co/datasets/SyMuPe/PianoCoRe (metadata columns of the parquet files).',
        'Citation: PianoCoRe v1.0, SyMuPe, 2026 (https://github.com/ilya16/PianoCoRe; cite as its README asks).', '',
        '### ASAP (Disklavier performances)', '',
        'Aligned Scores and Performances: International Piano-e-Competition performances recorded on Yamaha '
        'Disklavier concert grands (MAESTRO data), cut into single pieces. Licence: **CC BY-NC-SA 4.0**. '
        'https://github.com/fosfrancesco/asap-dataset. Citation: Foscarin, McLeod, Rigaux, Jacquemard, Sakai, '
        '"ASAP: a dataset of aligned scores and performances for piano transcription", ISMIR 2020; MAESTRO: '
        'Curtis Hawthorne et al., "Enabling Factorized Piano Music Modeling and Generation with the MAESTRO '
        'Dataset", ICLR 2019.', '',
        '### ATEPP v1.1', '',
        'Automatically Transcribed Expressive Piano Performances: transcriptions of commercial recordings by named '
        'concert pianists (only modern-era pianists preferred here). Licence: **CC BY 4.0**. '
        'https://zenodo.org/records/7182820. Citation: Huan Zhang, Jingjing Tang, Syed Rifat Mahmud Rafee, '
        'Simon Dixon, George Fazekas, "ATEPP: A Dataset of Automatically Transcribed Expressive Piano Performance", '
        'ISMIR 2022.', '',
        '### PERiScoPe v1.1', '',
        'Piano Expression Refined Score and Performance: web-collected recordings transcribed with Transkun V2. '
        'Licence: **CC BY-NC-SA 4.0**. https://huggingface.co/datasets/SyMuPe/PERiScoPe.', '',
        '### GiantMIDI-Piano v1.2 and Aria-MIDI v1 (gap fillers)', '',
        'GiantMIDI-Piano (ByteDance; Kong et al. transcription of YouTube recordings), licence **CC BY 4.0**, '
        'https://github.com/bytedance/GiantMIDI-Piano (Qiuqiang Kong et al., TISMIR 2022). '
        'Aria-MIDI (EleutherAI; Aria-AMT transcriptions, velocity quantised to ~16 levels), licence '
        '**CC BY-NC-SA 4.0**, https://huggingface.co/datasets/loubb/aria-midi (Louis Bradshaw, Simon Colton, '
        'ICLR 2025). Used only where nothing better exists.', '',
        '## piano-midi.de', '',
        'Bernd Krueger\'s Classical Piano Midi Page: hand-sequenced, carefully expressive files (velocities, '
        'tempo maps, sustain pedal; several tracks on channel 0). Licence: **CC BY-SA, Germany** '
        '("Copyright © Bernd Krueger, http://www.piano-midi.de"). Used for pieces PianoCoRe has no good take of.', '',
        '## Selection', '',
        'A hand-curated list (in the script) built from PianoCoRe\'s popularity ranking, Chopin held to ~30 and '
        'Beethoven to ~20, plus well-known pieces from ranks 150-400. Take order: ASAP > ATEPP (modern named '
        'pianist) > PERiScoPe > GiantMIDI / other ATEPP > piano-midi.de > Aria-MIDI; only rows labelled high '
        'quality, not duplicates, with a duration within 0.6-1.7x the piece\'s median. The ten MAESTRO titles '
        'already in config/live/test-piano are left out: ' + '; '.join(ALREADY_IN_TEST_PIANO) + '.',
    ]
    if gaps:
        lines += ['', '## Not staged', ''] + [f'- {t}: {why}' for t, why in gaps]
    write_atomic(os.path.join(STAGE, 'SOURCES.md'), '\n'.join(lines) + '\n', mode='w')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--plan', action='store_true', help='print the chosen take per piece; download nothing')
    ap.add_argument('--only', help='only titles containing this text (no manifest written)')
    a = ap.parse_args()
    items, gaps = plan(a.only)
    if a.plan:
        from collections import Counter
        for it in items:
            r = it.get('row') or {}
            print(f"{it.get('n_perf', 0):5d} {it['source'][:9]:9s} {(r.get('performer') or '')[:22]:22s} "
                  f"{float(r.get('performance_duration') or 0):6.0f}s  {it['spec']['title'][:80]}")
        print('pieces', len(items), dict(Counter(it['source'] for it in items)))
        for t, why in gaps:
            print('GAP', t, '::', why)
        return
    manifest = fetch(items)
    log(f'downloaded {BYTES["n"] / 1e6:.1f} MB this run')
    if a.only:
        return
    manifest.sort(key=lambda m: (-m['rank'], m['composer'], m['title']))
    write_atomic(os.path.join(STAGE, 'manifest.json'),
                 json.dumps({'v': 1, 'key': KEY, 'items': manifest}, ensure_ascii=False, indent=1), mode='w')
    write_sources(manifest, gaps)
    keep = {os.path.basename(m['src']) for m in manifest}
    for f in os.listdir(FILES):   # takes dropped from the list since the last run
        if f.endswith('.mid') and f not in keep:
            os.remove(os.path.join(FILES, f))
            log(f'  removed stale {f}')
    log(f'staged {len(manifest)} files; manifest + SOURCES.md in {STAGE}')


if __name__ == '__main__':
    main()
