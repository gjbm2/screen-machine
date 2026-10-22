"""DSP building blocks for piano_render.realism (each module documents its own parameters).

  filters    RBJ biquads, the instrument EQ list, Butterworth helpers (causal, minimum phase)
  tone       una corda shaping, mid/side width, mixing detuned engine copies
  resonance  the sympathetic / sustain-pedal resonator bank (numba, decimated rate)
  analysis   per-instrument partial measurement (f0 and inharmonicity B per key), cached
  room       impulse responses (true-stereo files or synthetic) and the FFT convolution
  _kernels   the numba kernels (imported only when the resonance bank runs)
"""
