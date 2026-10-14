"""Dr. Krömtgen's cell (music vis): This shader is part of my latest music visualization video: www.youtube.com/watch?v=KKDOUHB9ovA You can create your own styles using your shaders, i released...

Source: https://www.shadertoy.com/view/MsjfRd
Author: vapoi
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Permission is hereby granted, free of charge, to any person obtaining a copy
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''const float fPi = 3.14159;
const float fEu = 2.71828;

const vec3 colMain1 = vec3(1.2, 1.1, 0.15);
const vec3 colMain2 = vec3(0.1, 1.3, 1.2);
const vec3 colDetail1 = vec3(0.2, 0.05, 0.04);
const vec3 colDetail2 = vec3(0.2, 0.05, 0.04);


vec4 mod289(vec4 x)
{
  return x - floor(x * (1.0 / 289.0)) * 289.0;
}

vec4 permute(vec4 x)
{
  return mod289(((x*34.0)+1.0)*x);
}

vec4 taylorInvSqrt(vec4 r)
{
  return 1.79284291400159 - 0.85373472095314 * r;
}

vec2 fade(vec2 t) {
  return t*t*t*(t*(t*6.0-15.0)+10.0);
}


// Noise-Functions, https://github.com/ashima/webgl-noise
// 
// Copyright (C) 2011 by Ashima Arts (Simplex noise)
// Copyright (C) 2011-2016 by Stefan Gustavson (Classic noise and others)
// 
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
// 
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.
// 
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
// THE SOFTWARE.

// Classic Perlin noise
float cnoise(vec2 P)
{
  vec4 Pi = floor(P.xyxy) + vec4(0.0, 0.0, 1.0, 1.0);
  vec4 Pf = fract(P.xyxy) - vec4(0.0, 0.0, 1.0, 1.0);
  Pi = mod289(Pi); // To avoid truncation effects in permutation
  vec4 ix = Pi.xzxz;
  vec4 iy = Pi.yyww;
  vec4 fx = Pf.xzxz;
  vec4 fy = Pf.yyww;

  vec4 i = permute(permute(ix) + iy);

  vec4 gx = fract(i * (1.0 / 41.0)) * 2.0 - 1.0 ;
  vec4 gy = abs(gx) - 0.5 ;
  vec4 tx = floor(gx + 0.5);
  gx = gx - tx;

  vec2 g00 = vec2(gx.x,gy.x);
  vec2 g10 = vec2(gx.y,gy.y);
  vec2 g01 = vec2(gx.z,gy.z);
  vec2 g11 = vec2(gx.w,gy.w);

  vec4 norm = taylorInvSqrt(vec4(dot(g00, g00), dot(g01, g01), dot(g10, g10), dot(g11, g11)));
  g00 *= norm.x;  
  g01 *= norm.y;  
  g10 *= norm.z;  
  g11 *= norm.w;  

  float n00 = dot(g00, vec2(fx.x, fy.x));
  float n10 = dot(g10, vec2(fx.y, fy.y));
  float n01 = dot(g01, vec2(fx.z, fy.z));
  float n11 = dot(g11, vec2(fx.w, fy.w));

  vec2 fade_xy = fade(Pf.xy);
  vec2 n_x = mix(vec2(n00, n01), vec2(n10, n11), fade_xy.x);
  float n_xy = mix(n_x.x, n_x.y, fade_xy.y);
  return 2.3 * n_xy;
}

// Classic Perlin noise, periodic variant
float pnoise(vec2 P, vec2 rep)
{
  vec4 Pi = floor(P.xyxy) + vec4(0.0, 0.0, 1.0, 1.0);
  vec4 Pf = fract(P.xyxy) - vec4(0.0, 0.0, 1.0, 1.0);
  Pi = mod(Pi, rep.xyxy); // To create noise with explicit period
  Pi = mod289(Pi);        // To avoid truncation effects in permutation
  vec4 ix = Pi.xzxz;
  vec4 iy = Pi.yyww;
  vec4 fx = Pf.xzxz;
  vec4 fy = Pf.yyww;

  vec4 i = permute(permute(ix) + iy);

  vec4 gx = fract(i * (1.0 / 41.0)) * 2.0 - 1.0 ;
  vec4 gy = abs(gx) - 0.5 ;
  vec4 tx = floor(gx + 0.5);
  gx = gx - tx;

  vec2 g00 = vec2(gx.x,gy.x);
  vec2 g10 = vec2(gx.y,gy.y);
  vec2 g01 = vec2(gx.z,gy.z);
  vec2 g11 = vec2(gx.w,gy.w);

  vec4 norm = taylorInvSqrt(vec4(dot(g00, g00), dot(g01, g01), dot(g10, g10), dot(g11, g11)));
  g00 *= norm.x;  
  g01 *= norm.y;  
  g10 *= norm.z;  
  g11 *= norm.w;  

  float n00 = dot(g00, vec2(fx.x, fy.x));
  float n10 = dot(g10, vec2(fx.y, fy.y));
  float n01 = dot(g01, vec2(fx.z, fy.z));
  float n11 = dot(g11, vec2(fx.w, fy.w));

  vec2 fade_xy = fade(Pf.xy);
  vec2 n_x = mix(vec2(n00, n01), vec2(n10, n11), fade_xy.x);
  float n_xy = mix(n_x.x, n_x.y, fade_xy.y);
  return 2.3 * n_xy;
}

float heightNoise(vec2 poi)
{
	return 	  cnoise(poi) * 0.633
			+ cnoise(poi + vec2(4.7)) * 0.333
			+ cnoise(poi + vec2(9.4)) * 0.133;
}
float sNoise(vec2 poi)
{
	return 	  cnoise(poi) * 0.633
			+ cnoise(poi * vec2(2.2)) * 0.333
			+ cnoise(poi * vec2(4.7)) * 0.133;
}

float DistanceP(vec2 p, vec2 segA, vec2 segB)
{
    	vec2 p2 = vec2(segB.x - segA.x,segB.y - segA.y);
    	float num = p2.x * p2.x + p2.y * p2.y;
    	float u = ((p.x - segA.x) * p2.x + (p.y - segA.y) * p2.y) / num;

    	if (u > 1.0 ) {
    		u = 1.0;
    	}
    	else if (u < 0.0) {
    		u = 0.0;
    	}

    	float x = segA.x + u * p2.x;
    	float y = segA.y + u * p2.y;

    	float dx = x - p.x;
    	float dy = y - p.y;

    	return sqrt(dx*dx + dy*dy);
}


float GetNoiseP(vec2 poi, vec2 per)
{
	return 	1.5 + pnoise(poi, per) 
			+ pnoise(poi * vec2(0.50), per)
			;
}
float GetNoiseD(vec2 poi, vec2 per)
{
	return 	0.7 * pnoise(poi, per) +
			0.3 * pnoise(poi + vec2(0.5, 3.0), per) +
			0.1 * pnoise(poi + vec2(1.0, 6.0), per)
			;
}

float GetNoise(vec2 poi)
{
	return 	0.7 * cnoise(poi) +
			0.3 * cnoise(poi * vec2(2.17))
			;
}


void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 uv = fragCoord.xy / iResolution.xy;
    
    vec2 pMid = vec2(-1.0, -1.0) + 2.0 * uv;
	
	pMid.x *= iResolution.x / iResolution.y;
	
    float fNumber = 5.0;
    float vTime = iTime * 1.0;
    float fSecondCore = clamp(sin(vTime * 1.3) + cos(vTime * 0.95), 0.0, 1.0);
    float fSpec = clamp(sin(vTime * 0.93) + cos(vTime * 1.35), 0.0, 1.0);
	
	float distanceToMid = length(pMid);
	float fAngle = atan(pMid.y, pMid.x);
	float fGrain = 0.0 + GetNoise(pMid * 14.0 + vec2(fNumber * 4.0, fNumber * 3.0));

	float someNoise = GetNoiseD(vec2(((fAngle / fPi) + 0.5) * 7.0 + fNumber * 13.0,
									 distanceToMid * 7.30 + fNumber * 3.1 - vTime * 0.93),
								vec2(7.0, 35.0));
	someNoise *= clamp((distanceToMid - 0.1) * 4.0, 0.0, 1.0);
	float modifyBorder = someNoise * (0.025 + fSpec * 0.025);
					
	// Cell-Core 
	float rotateOnAction = sin(vTime * 0.61 + fNumber * 0.92);
	float rotateOnAction2 = sin(vTime * 0.41 + fNumber * 0.52);
	vec2 corePos = vec2(sin(vTime * 0.64 + fNumber * 0.71 + fSpec * rotateOnAction), 
					    cos(vTime * 0.73 + fNumber * 1.2 + fSpec * rotateOnAction2)) * (0.2 + 0.8 * fSpec) * 0.18;
	
	float fCore = smoothstep(-0.1, -0.04, -length(pMid + corePos));
	
	
	vec2 corePos2 = corePos;
	float fSecCore = fSecondCore * 0.5 * (1.0 + cos(vTime * 0.43 + sin(vTime * 0.46 + fNumber * 0.42) + fNumber * 0.35));
	
	if (fSecCore > 0.0)
	{
		float secCoreDif1 = -0.6 * (1.0 + sin(vTime * 0.5 + sin(vTime * 0.6 + fSpec + fNumber * 0.3) + fNumber * 0.45));
		float secCoreDif2 = 0.7 * (1.0 + sin(vTime * 0.6 + sin(vTime * 0.4 + fSpec + fNumber * 0.35) + fNumber * 0.4));
		corePos2 = mix(corePos, vec2(sin(secCoreDif1 + vTime * 0.64 + fNumber * 0.71 + fSpec * rotateOnAction), 
									 cos(secCoreDif2 + vTime * 0.73 + fNumber * 1.2 + fSpec * rotateOnAction2)) 
									 * (0.2 + 0.8 * fSpec) * 0.18 * clamp(secCoreDif1 + secCoreDif2, -0.5, 1.0),
					  0.5 * (1.0 + cos(fPi + fPi * fSecCore)));
		fCore = max(fCore, smoothstep(-0.1, -0.04, -length(pMid + corePos2)));
		corePos = (corePos + corePos2) * 0.5;
	}
	
	float fOuterCore = smoothstep(-0.2, -0.05, -length(pMid + corePos * 0.75) + 2.0 * modifyBorder * fGrain);
	
	
	
	// Cell
	float fShapeVisible = 0.0;
	fShapeVisible = (0.6 + 0.45 * fSpec ) * (1.0 - 0.05 * someNoise - fGrain * 0.1)
						 * smoothstep(-0.42, -0.30 - fSpec * 0.05, -distanceToMid + modifyBorder - min(0.0, fGrain * 0.05))
						 * (1.0 - 0.35 * smoothstep(0.20 - fSpec * 0.0, 0.38, distanceToMid + modifyBorder - min(0.0, fGrain * 0.1))) ;

	float fModifiedDist = distanceToMid * (1.0 - 0.7 * modifyBorder);
	fShapeVisible += fOuterCore * (0.15 - fSpec * 0.45);
	

	// cell core structure
	if (fCore > 0.0)
	{
		fCore += 0.5 * fGrain;
	}
	
	// coloring
	vec3 colSpec = mix(vec3(0.45), mix(colMain1, colMain2, fNumber / 40.0), fSpec * 1.0);
	
	vec3 colSpec2 = mix(vec3(0.25), mix(colDetail1, colDetail2, fNumber / 40.0), fSpec * 1.1);
	colSpec = mix(colSpec, colSpec2, min(1.0, max(0.0, -someNoise * 1.0) * fSpec * fShapeVisible * 1.5));
	
	// cell core is fading to grey on amplitude
	colSpec = mix(colSpec, vec3(0.1) + vec3(0.25) * fSpec, fCore);
	
	
	
	vec3 colBorder = mix(colSpec * 0.5, vec3(0.2) + vec3(0.1) * fSpec, smoothstep(0.3, 0.6, fModifiedDist))
					* (0.2 + 0.2 * fSpec)
					* smoothstep(-0.9, -0.4, -fModifiedDist)
					* smoothstep(0.3, 0.4, fModifiedDist);
	vec3 colDot = mix(colBorder, colSpec * fShapeVisible, fShapeVisible);
	
	
	
	
	// 3D Light Effect Test
	fAngle = ((fAngle / fPi) + 0.5) * 3.0 - 0.25 * fSpec * (1.0 + rotateOnAction);
	
	fAngle *= 1.0 - fSpec * 0.3;
	float fLight = smoothstep(0.0, 1.0, fAngle)
				 * smoothstep(-2.0, -1.0, -fAngle);
	float moveIn = 0.025 * fSpec;
	fLight *= smoothstep(-0.3 - moveIn, -0.2 - moveIn, -fModifiedDist)
			* smoothstep(0.1 + moveIn, 0.2 + moveIn, fModifiedDist);
	colDot += vec3(0.3) * (1.0 - 2.0 * fSpec) * (1.0 - 2.0 * modifyBorder) * fLight;
	

	
	fragColor = vec4(colDot, smoothstep(-0.9, -0.38, -fModifiedDist));
}'''


class StDrKrMtgenSCellMusicVis(GlslSource):
    name = 'st_dr_kr_mtgen_s_cell_music_vis'
    label = "Dr. Krömtgen's cell (music vis)"
    description = 'This shader is part of my latest music visualization video: www.youtube.com/watch?v=KKDOUHB9ovA You can create your own styles using your shaders, i released...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
