"""Live score: turning what the piano plays into engraved notation as it is played (owner, 16 Oct 2026).

  quantize.py  ScoreBuilder: notes (sound-clock seconds) -> a beat clock that follows the playing -> bars of
               lightly quantised notation (16th grid, swing read as straight eighths, hands split at middle C)
  mei.py       bars -> an MEI document for one grand-staff system (ties, beams, accidentals, pedal, dynamics)
  engrave.py   the engraving worker (its own process: Verovio + resvg hold the GIL): MEI -> ink alpha + geometry
Used by stream_service/plugins/sources/live_score.py.
"""
