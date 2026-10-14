# Third-party shaders in the plug-in folders

The `glt_*`, `isf_*` and `st_*` plug-ins embed third-party GLSL verbatim. Each plug-in file's docstring records
the source URL, the author, the SPDX licence and the `license_class` (CONVENTIONS section 5). The upstream code
keeps its own header comments, including copyright and licence text where the author gave them.

| Prefix | Origin | Licence |
|---|---|---|
| `glt_` | gl-transitions, github.com/gl-transitions/gl-transitions @ b2dfdcf | MIT (`gl-transitions.txt`); per-file headers: 123 MIT, 1 BSD-3-Clause, 1 BSD-2-Clause |
| `isf_` | Vidvox ISF-Files, github.com/Vidvox/ISF-Files @ 395072d | MIT (`Vidvox-ISF-Files.txt`) unless the file says otherwise. v002 ports and Shadertoy-derived files are CC-BY-NC-SA-3.0 (noncommercial). |
| `isf_` | Gnomalab/VDMX-ISF-Effects-Suite, daitomanabe/isf-shaders-for-vj, ProjectileObjects/MiscISFShaders, icalvin102/ISF | MIT (repo LICENSE files here); Shadertoy-derived files are noncommercial. |
| `isf_` | bareimage/ISF, nwhetsell/isf-shaders | Per file: MIT or CC-BY-NC-SA-3.0; files using LYGIA carry its Prosperity licence (noncommercial). |
| `st_` | Shadertoy (www.shadertoy.com/view/<id>) | Only shaders whose code states a permissive licence (MIT, CC0, Unlicense, BSD, Apache, WTFPL). |

Noncommercial-class plug-ins are allowed for this private, never-distributed installation (CONVENTIONS section
5). Do not publish their output, the HEVC stream or recordings. Shaders with GPL code, with no licence (e.g.
glslsandbox), or with all-rights-reserved headers were not imported.
