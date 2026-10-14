"""Bow Tie With Parameter: A bow-tie wipe with an adjustable shape that can run in reverse

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/BowTieWithParameter.glsl
Author: KMojek
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: KMojek
// License: MIT
// Description: A bow-tie wipe with an adjustable shape that can run in reverse
// Tags: wipe, shape

uniform float adjust; // = 0.5
// @param Run the wipe in reverse
uniform bool reverse; // = false

float check(vec2 p1, vec2 p2, vec2 p3)
{
  return (p1.x - p3.x) * (p2.y - p3.y) - (p2.x - p3.x) * (p1.y - p3.y);
}

bool pointInTriangle(vec2 pt, vec2 p1, vec2 p2, vec2 p3)
{

    bool b1 = check(pt, p1, p2) < 0.0;
    bool b2 = check(pt, p2, p3) < 0.0;
    bool b3 = check(pt, p3, p1) < 0.0;
    return b1 == b2 && b2 == b3;
}

const float height = 0.5;

vec4 transition_firstHalf( vec2 uv, float prog )
{
  if ( uv.y < 0.5 )
  {
    vec2 botLeft = vec2( -0., prog-height );
    vec2 botRight = vec2( 1., prog-height );
    vec2 tip = vec2( adjust, prog );
    if ( pointInTriangle( uv, botLeft, botRight, tip ) )
        return getToColor(uv);
    }
  else
  {
    vec2 topLeft = vec2( -0., 1.-prog+height );
    vec2 topRight = vec2( 1., 1.-prog+height );
    vec2 tip = vec2( adjust, 1.-prog );
    if ( pointInTriangle( uv, topLeft, topRight, tip ) )
      return getToColor( uv );
  }
  return getFromColor( uv );
}

vec4 transition_secondHalf( vec2 uv, float prog )
{
  if ( uv.x > adjust )
  {
    vec2 top = vec2( prog + height,  1. );
    vec2 bot = vec2( prog + height, -0. );
    vec2 tip = vec2( mix( adjust, 1.0, 2.0 * (prog - 0.5) ), 0.5 );
    if ( pointInTriangle( uv, top, bot, tip) )
      return getFromColor( uv );
  }
  else
  {
    vec2 top = vec2( 1.0-prog - height,  1. );
    vec2 bot = vec2( 1.0-prog - height, -0. );
    vec2 tip = vec2( mix( adjust, 0.0, 2.0 * (prog - 0.5)  ), 0.5 );
    if ( pointInTriangle( uv, top, bot, tip) )
      return getFromColor( uv );
  }
  return getToColor( uv );
}

vec4 transition (vec2 uv) {
  if ( reverse )
    return ( progress < 0.5 ) ? transition_secondHalf( uv, 1.-progress ) : transition_firstHalf( uv, 1.-progress );
  else
    return ( progress < 0.5 ) ? transition_firstHalf( uv, progress ) : transition_secondHalf( uv, progress );
}'''


class GltBowTieWithParameter(GlslTransition):
    name = 'glt_bow_tie_with_parameter'
    label = 'Bow Tie With Parameter'
    description = 'A bow-tie wipe with an adjustable shape that can run in reverse'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "adjust": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Adjust",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "adjust"
            }
        },
        "reverse": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Run the wipe in reverse"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "reverse"
            }
        }
    }
