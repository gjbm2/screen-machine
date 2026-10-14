"""Tileable textures collection: Collection of tileable procedural textures such as: cellular noise, fbm, voronoi, perlin and other. Github link:...

Source: https://www.shadertoy.com/view/3sKXWh
Author: alint
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2019 Alin Loghin
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

#define SCALE   8.0 
#define TILES   2.0     

#define SHOW_TILING 1      
#define ANIMATE 1      

// hashes

uint ihash1D(uint q)
{
    // hash by Hugo Elias, Integer Hash - I, 2017
    q = (q << 13u) ^ q;
    return q * (q * q * 15731u + 789221u) + 1376312589u;
}

uvec4 ihash1D(uvec4 q)
{
    // hash by Hugo Elias, Integer Hash - I, 2017
    q = (q << 13u) ^ q;
    return q * (q * q * 15731u + 789221u) + 1376312589u;
}

float hash1D(vec2 x)
{
    // hash by Inigo Quilez, Integer Hash - III, 2017
    uvec2 q = uvec2(x * 65536.0);
    q = 1103515245u * ((q >> 1u) ^ q.yx);
    uint n = 1103515245u * (q.x ^ (q.y >> 3u));
    return float(n) * (1.0 / float(0xffffffffu));
}

vec2 hash2D(vec2 x)
{
    // based on: Inigo Quilez, Integer Hash - III, 2017
    uvec4 q = uvec2(x * 65536.0).xyyx + uvec2(0u, 3115245u).xxyy;
    q = 1103515245u * ((q >> 1u) ^ q.yxwz);
    uvec2 n = 1103515245u * (q.xz ^ (q.yw >> 3u));
    return vec2(n) * (1.0 / float(0xffffffffu));
}

vec3 hash3D(vec2 x) 
{
    // based on: pcg3 by Mark Jarzynski: http://www.jcgt.org/published/0009/03/02/
    uvec3 v = uvec3(x.xyx * 65536.0) * 1664525u + 1013904223u;
    v += v.yzx * v.zxy;
    v ^= v >> 16u;

    v.x += v.y * v.z;
    v.y += v.z * v.x;
    v.z += v.x * v.y;
    return vec3(v) * (1.0 / float(0xffffffffu));
}

vec4 hash4D(vec2 x)
{
    // based on: pcg4 by Mark Jarzynski: http://www.jcgt.org/published/0009/03/02/
    uvec4 v = uvec4(x.xyyx * 65536.0) * 1664525u + 1013904223u;

    v += v.yzxy * v.wxyz;
    v.x += v.y * v.w;
    v.y += v.z * v.x;
    v.z += v.x * v.y;
    v.w += v.y * v.z;
    
    v.x += v.y * v.w;
    v.w += v.y * v.z;
    
    v ^= v >> 16u;

    return vec4(v ^ (v >> 16u)) * (1.0 / float(0xffffffffu));
}

vec4 hash4D(vec4 x)
{
    // based on: pcg4 by Mark Jarzynski: http://www.jcgt.org/published/0009/03/02/
    uvec4 v = uvec4(x * 65536.0) * 1664525u + 1013904223u;

    v += v.yzxy * v.wxyz;
    v.x += v.y * v.w;
    v.y += v.z * v.x;
    v.z += v.x * v.y;
    v.w += v.y * v.z;
    
    v.x += v.y*v.w;
    v.y += v.z*v.x;
    v.z += v.x*v.y;
    v.w += v.y*v.z;

    v ^= v >> 16u;

    return vec4(v ^ (v >> 16u)) * (1.0 / float(0xffffffffu));
}


vec2 betterHash2D(vec2 x)
{
    uvec2 q = uvec2(x);
    uint h0 = ihash1D(ihash1D(q.x) + q.y);
    uint h1 = h0 * 1933247u + ~h0 ^ 230123u;
    return vec2(h0, h1)  * (1.0 / float(0xffffffffu));
}

// generates a random number for each of the 4 cell corners
vec4 betterHash2D(vec4 cell)    
{
    uvec4 i = uvec4(cell) + 101323u;
    uvec4 hash = ihash1D(ihash1D(i.xzxz) + i.yyww);
    return vec4(hash) * (1.0 / float(0xffffffffu));
}

// generates 2 random numbers for each of the 4 cell corners
void betterHash2D(vec4 cell, out vec4 hashX, out vec4 hashY)
{
    uvec4 i = uvec4(cell) + 101323u;
    uvec4 hash0 = ihash1D(ihash1D(i.xzxz) + i.yyww);
    uvec4 hash1 = ihash1D(hash0 ^ 1933247u);
    hashX = vec4(hash0) * (1.0 / float(0xffffffffu));
    hashY = vec4(hash1) * (1.0 / float(0xffffffffu));
}

// generates 2 random numbers for each of the four 2D coordinates
void betterHash2D(vec4 coords0, vec4 coords1, out vec4 hashX, out vec4 hashY)
{
    uvec4 hash0 = ihash1D(ihash1D(uvec4(coords0.xz, coords1.xz)) + uvec4(coords0.yw, coords1.yw));
    uvec4 hash1 = hash0 * 1933247u + ~hash0 ^ 230123u;
    hashX = vec4(hash0) * (1.0 / float(0xffffffffu));
    hashY = vec4(hash1) * (1.0 / float(0xffffffffu));
} 

// generates a random number for each of the 8 cell corners
void betterHash3D(vec3 cell, vec3 cellPlusOne, out vec4 lowHash, out vec4 highHash)
{
    uvec4 cells = uvec4(cell.xy, cellPlusOne.xy);  
    uvec4 hash = ihash1D(ihash1D(cells.xzxz) + cells.yyww);
    
    lowHash = vec4(ihash1D(hash + uint(cell.z))) * (1.0 / float(0xffffffffu));
    highHash = vec4(ihash1D(hash + uint(cellPlusOne.z))) * (1.0 / float(0xffffffffu));
}

#define multiHash2D betterHash2D
#define multiHash3D betterHash3D

void smultiHash2D(vec4 cell, out vec4 hashX, out vec4 hashY)
{
    multiHash2D(cell, hashX, hashY);
    hashX = hashX * 2.0 - 1.0; 
    hashY = hashY * 2.0 - 1.0;
}

// common

vec2 noiseInterpolate(const in vec2 x) 
{ 
    vec2 x2 = x * x;
    return x2 * x * (x * (x * 6.0 - 15.0) + 10.0); 
}
vec4 noiseInterpolate(const in vec4 x) 
{ 
    vec4 x2 = x * x;
    return x2 * x * (x * (x * 6.0 - 15.0) + 10.0); 
}
vec4 noiseInterpolateDu(const in vec2 x) 
{ 
    vec2 x2 = x * x;
    vec2 u = x2 * x * (x * (x * 6.0 - 15.0) + 10.0); 
    vec2 du = 30.0 * x2 * (x * (x - 2.0) + 1.0);
    return vec4(u, du);
}
void noiseInterpolateDu(const in vec3 x, out vec3 u, out vec3 du) 
{ 
    vec3 x2 = x * x;
    u = x2 * x * (x * (x * 6.0 - 15.0) + 10.0); 
    du = 30.0 * x2 * (x * (x - 2.0) + 1.0);
}

float distanceMetric(vec2 pos, uint metric)
{
    switch (metric)
    {
        case 0u:
            // squared euclidean
            return dot(pos, pos);
        case 1u:
            // manhattam   
            return dot(abs(pos), vec2(1.0));
        case 2u:
            // chebyshev
            return max(abs(pos.x), abs(pos.y));
        default:
            // triangular
            return  max(abs(pos.x) * 0.866025 + pos.y * 0.5, -pos.y);
    }
}

vec4 distanceMetric(vec4 px, vec4 py, uint metric)
{
    switch (metric)
    {
        case 0u:
            // squared euclidean
            return px * px + py * py;
        case 1u:
            // manhattam   
            return abs(px) + abs(py);
        case 2u:
            // chebyshev
            return max(abs(px), abs(py));
        default:
            // triangular
            return max(abs(px) * 0.866025 + py * 0.5, -py);
    }
}

// noises

float noise(vec2 pos, vec2 scale, float phase, float seed) 
{
    const float kPI2 = 6.2831853071;
    pos *= scale;
    vec4 i = floor(pos).xyxy + vec2(0.0, 1.0).xxyy;
    vec2 f = pos - i.xy;
    i = mod(i, scale.xyxy) + seed;

    vec4 hash = multiHash2D(i);
    hash = 0.5 * sin(phase + kPI2 * hash) + 0.5;
    float a = hash.x;
    float b = hash.y;
    float c = hash.z;
    float d = hash.w;

    vec2 u = noiseInterpolate(f);
    float value = mix(a, b, u.x) + (c - a) * u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
    return value * 2.0 - 1.0;
}

vec3 noised(vec2 pos, vec2 scale, float phase, float seed) 
{
    const float kPI2 = 6.2831853071;
    // value noise with derivatives based on Inigo Quilez
    pos *= scale;
    vec4 i = floor(pos).xyxy + vec2(0.0, 1.0).xxyy;
    vec2 f = pos - i.xy;
    i = mod(i, scale.xyxy) + seed;

    vec4 hash = multiHash2D(i);
    hash = 0.5 * sin(phase + kPI2 * hash) + 0.5;
    float a = hash.x;
    float b = hash.y;
    float c = hash.z;
    float d = hash.w;
    
    vec4 udu = noiseInterpolateDu(f);    
    float abcd = a - b - c + d;
    float value = a + (b - a) * udu.x + (c - a) * udu.y + abcd * udu.x * udu.y;
    vec2 derivative = udu.zw * (udu.yx * abcd + vec2(b, c) - a);
    return vec3(value * 2.0 - 1.0, derivative);
}

vec2 multiNoise(vec4 pos, vec4 scale, float phase, vec2 seed) 
{
    const float kPI2 = 6.2831853071;
    pos *= scale;
    vec4 i = floor(pos);
    vec4 f = pos - i;
    vec4 i0 = mod(i.xyxy + vec2(0.0, 1.0).xxyy, scale.xyxy) + seed.x;
    vec4 i1 = mod(i.zwzw + vec2(0.0, 1.0).xxyy, scale.xyxy) + seed.y;

    vec4 hash0 = multiHash2D(i0);
    hash0 = 0.5 * sin(phase + kPI2 * hash0) + 0.5;
    vec4 hash1 = multiHash2D(i1);
    hash1 = 0.5 * sin(phase + kPI2 * hash1) + 0.5;
    vec2 a = vec2(hash0.x, hash1.x);
    vec2 b = vec2(hash0.y, hash1.y);
    vec2 c = vec2(hash0.z, hash1.z);
    vec2 d = vec2(hash0.w, hash1.w);

    vec4 u = noiseInterpolate(f);
    vec2 value = mix(a, b, u.xz) + (c - a) * u.yw * (1.0 - u.xz) + (d - b) * u.xz * u.yw;
    return value * 2.0 - 1.0;
}

vec3 gradientNoised(vec2 pos, vec2 scale, float seed) 
{
    // gradient noise with derivatives based on Inigo Quilez
    pos *= scale;
    vec4 i = floor(pos).xyxy + vec2(0.0, 1.0).xxyy;
    vec4 f = (pos.xyxy - i.xyxy) - vec2(0.0, 1.0).xxyy;
    i = mod(i, scale.xyxy) + seed;
    
    vec4 hashX, hashY;
    smultiHash2D(i, hashX, hashY);
    vec2 a = vec2(hashX.x, hashY.x);
    vec2 b = vec2(hashX.y, hashY.y);
    vec2 c = vec2(hashX.z, hashY.z);
    vec2 d = vec2(hashX.w, hashY.w);
    
    vec4 gradients = hashX * f.xzxz + hashY * f.yyww;

    vec4 udu = noiseInterpolateDu(f.xy);
    vec2 u = udu.xy;
    vec2 g = mix(gradients.xz, gradients.yw, u.x);
    
    vec2 dxdy = a + u.x * (b - a) + u.y * (c - a) + u.x * u.y * (a - b - c + d);
    dxdy += udu.zw * (u.yx * (gradients.x - gradients.y - gradients.z + gradients.w) + gradients.yz - gradients.x);
    return vec3(mix(g.x, g.y, u.y) * 1.4142135623730950, dxdy);
}
vec3 gradientNoised(vec2 pos, vec2 scale, mat2 transform, float seed) 
{
    // gradient noise with derivatives based on Inigo Quilez
    pos *= scale;
    vec4 i = floor(pos).xyxy + vec2(0.0, 1.0).xxyy;
    vec4 f = (pos.xyxy - i.xyxy) - vec2(0.0, 1.0).xxyy;
    i = mod(i, scale.xyxy) + seed;
    
    vec4 hashX, hashY;
    smultiHash2D(i, hashX, hashY);

    // transform gradients
    vec4 m = vec4(transform);
    vec4 rh = vec4(hashX.x, hashY.x, hashX.y, hashY.y);
    rh = rh.xxzz * m.xyxy + rh.yyww * m.zwzw;
    hashX.xy = rh.xz;
    hashY.xy = rh.yw;

    rh = vec4(hashX.z, hashY.z, hashX.w, hashY.w);
    rh = rh.xxzz * m.xyxy + rh.yyww * m.zwzw;
    hashX.zw = rh.xz;
    hashY.zw = rh.yw;
    
    vec2 a = vec2(hashX.x, hashY.x);
    vec2 b = vec2(hashX.y, hashY.y);
    vec2 c = vec2(hashX.z, hashY.z);
    vec2 d = vec2(hashX.w, hashY.w);
    
    vec4 gradients = hashX * f.xzxz + hashY * f.yyww;

    vec4 udu = noiseInterpolateDu(f.xy);
    vec2 u = udu.xy;
    vec2 g = mix(gradients.xz, gradients.yw, u.x);
    
    vec2 dxdy = a + u.x * (b - a) + u.y * (c - a) + u.x * u.y * (a - b - c + d);
    dxdy += udu.zw * (u.yx * (gradients.x - gradients.y - gradients.z + gradients.w) + gradients.yz - gradients.x);
    return vec3(mix(g.x, g.y, u.y) * 1.4142135623730950, dxdy);
}

vec3 gradientNoised(vec2 pos, vec2 scale, float rotation, float seed) 
{
    vec2 sinCos = vec2(sin(rotation), cos(rotation));
    return gradientNoised(pos, scale, mat2(sinCos.y, sinCos.x, sinCos.x, sinCos.y), seed);
}

float perlinNoise(vec2 pos, vec2 scale, float seed)
{
    // based on Modifications to Classic Perlin Noise by Brian Sharpe: https://archive.is/cJtlS
    pos *= scale;
    vec4 i = floor(pos).xyxy + vec2(0.0, 1.0).xxyy;
    vec4 f = (pos.xyxy - i.xyxy) - vec2(0.0, 1.0).xxyy;
    i = mod(i, scale.xyxy) + seed;

    // grid gradients
    vec4 gradientX, gradientY;
    multiHash2D(i, gradientX, gradientY);
    gradientX -= 0.49999;
    gradientY -= 0.49999;

    // perlin surflet
    vec4 gradients = inversesqrt(gradientX * gradientX + gradientY * gradientY) * (gradientX * f.xzxz + gradientY * f.yyww);
    // normalize: 1.0 / 0.75^3
    gradients *= 2.3703703703703703703703703703704;
    vec4 lengthSq = f * f;
    lengthSq = lengthSq.xzxz + lengthSq.yyww;
    vec4 xSq = 1.0 - min(vec4(1.0), lengthSq); 
    xSq = xSq * xSq * xSq;
    return dot(xSq, gradients);
}
float perlinNoise(vec2 pos, vec2 scale, mat2 transform, float seed)
{
    // based on Modifications to Classic Perlin Noise by Brian Sharpe: https://archive.is/cJtlS
    pos *= scale;
    vec4 i = floor(pos).xyxy + vec2(0.0, 1.0).xxyy;
    vec4 f = (pos.xyxy - i.xyxy) - vec2(0.0, 1.0).xxyy;
    i = mod(i, scale.xyxy) + seed;

    // grid gradients
    vec4 gradientX, gradientY;
    multiHash2D(i, gradientX, gradientY);
    gradientX -= 0.49999;
    gradientY -= 0.49999;

    // transform gradients
    vec4 m = vec4(transform);
    vec4 rg = vec4(gradientX.x, gradientY.x, gradientX.y, gradientY.y);
    rg = rg.xxzz * m.xyxy + rg.yyww * m.zwzw;
    gradientX.xy = rg.xz;
    gradientY.xy = rg.yw;

    rg = vec4(gradientX.z, gradientY.z, gradientX.w, gradientY.w);
    rg = rg.xxzz * m.xyxy + rg.yyww * m.zwzw;
    gradientX.zw = rg.xz;
    gradientY.zw = rg.yw;

    // perlin surflet
    vec4 gradients = inversesqrt(gradientX * gradientX + gradientY * gradientY) * (gradientX * f.xzxz + gradientY * f.yyww);
    // normalize: 1.0 / 0.75^3
    gradients *= 2.3703703703703703703703703703704;
    f = f * f;
    f = f.xzxz + f.yyww;
    vec4 xSq = 1.0 - min(vec4(1.0), f); 
    return dot(xSq * xSq * xSq, gradients);
}
float perlinNoise(vec2 pos, vec2 scale, float rotation, float seed) 
{
    vec2 sinCos = vec2(sin(rotation), cos(rotation));
    return perlinNoise(pos, scale, mat2(sinCos.y, sinCos.x, sinCos.x, sinCos.y), seed);
}

vec3 perlinNoised(vec2 pos, vec2 scale, mat2 transform, float seed)
{
    // based on Modifications to Classic Perlin Noise by Brian Sharpe: https://archive.is/cJtlS
    pos *= scale;
    vec4 i = floor(pos).xyxy + vec2(0.0, 1.0).xxyy;
    vec4 f = (pos.xyxy - i.xyxy) - vec2(0.0, 1.0).xxyy;
    i = mod(i, scale.xyxy) + seed;

    // grid gradients
    vec4 gradientX, gradientY;
    multiHash2D(i, gradientX, gradientY);
    gradientX -= 0.49999;
    gradientY -= 0.49999;

    // transform gradients
    vec4 mt = vec4(transform);
    vec4 rg = vec4(gradientX.x, gradientY.x, gradientX.y, gradientY.y);
    rg = rg.xxzz * mt.xyxy + rg.yyww * mt.zwzw;
    gradientX.xy = rg.xz;
    gradientY.xy = rg.yw;

    rg = vec4(gradientX.z, gradientY.z, gradientX.w, gradientY.w);
    rg = rg.xxzz * mt.xyxy + rg.yyww * mt.zwzw;
    gradientX.zw = rg.xz;
    gradientY.zw = rg.yw;
    
    // perlin surflet
    vec4 gradients = inversesqrt(gradientX * gradientX + gradientY * gradientY) * (gradientX * f.xzxz + gradientY * f.yyww);
    vec4 m = f * f;
    m = m.xzxz + m.yyww;
    m = max(1.0 - m, 0.0);
    vec4 m2 = m * m;
    vec4 m3 = m * m2;
    // compute the derivatives
    vec4 m2Gradients = -6.0 * m2 * gradients;
    vec2 grad = vec2(dot(m2Gradients, f.xzxz), dot(m2Gradients, f.yyww)) + vec2(dot(m3, gradientX), dot(m3, gradientY));
    // sum the surflets and normalize: 1.0 / 0.75^3
    return vec3(dot(m3, gradients), grad) * 2.3703703703703703703703703703704;
}

float organicNoise(vec2 pos, vec2 scale, float density, vec2 phase, float contrast, float highlights, float shift, float seed)
{
    vec2 s = mix(vec2(1.0), scale - 1.0, density);
    float nx = perlinNoise(pos + phase, scale, seed);
    float ny = perlinNoise(pos, s, seed);

    float n = length(vec2(nx, ny) * mix(vec2(2.0, 0.0), vec2(0.0, 2.0), shift));
    n = pow(n, 1.0 + 8.0 * contrast) + (0.15 * highlights) / n;
    return n * 0.5;
}

vec2 randomLines(vec2 pos, vec2 scale, float count, float width, float jitter, vec2 smoothness, float phase, float seed)
{
    float strength = jitter * 1.25;

    // compute gradient
    // TODO: compute the gradient analytically
    vec2 grad;
    vec3 offsets = vec3(1.0, 0.0, -1.0) / 1024.0;
    vec4 p = pos.xyxy + offsets.xyzy;
    vec2 nv = count * (strength * multiNoise(p, scale.xyxy, phase, vec2(seed)) + p.yw);
    grad.x = nv.x - nv.y;
    p = pos.xyxy + offsets.yxyz;
    nv = count * (strength * multiNoise(p, scale.xyxy, phase, vec2(seed)) + p.yw);
    grad.y = nv.x - nv.y;
    
    float v =  count * (strength * noise(pos, scale, phase, seed) + pos.y);
    float w = fract(v) / length(grad / (2.0 * offsets.x));
    width *= 0.1;
    smoothness *= width;
    smoothness += max(abs(grad.x), abs(grad.y)) * 0.02;
    
    float d = smoothstep(0.0, smoothness.x, w) - smoothstep(max(width - smoothness.y, 0.0), width, w);
    return vec2(d, mod(floor(v), count));
}
vec4 randomLines(vec2 pos, vec2 scale, float count, float width, float jitter, vec2 smoothness, float phase, float colorVariation, float seed)
{
    vec2 l = randomLines(pos, scale, count, width, jitter, smoothness, phase, seed);
    vec3 r = hash3D(l.yy + seed);
    return vec4(l.x * (r.x < colorVariation ? r : r.xxx), l.x);
}

vec4 fbmMulti(vec2 pos, vec2 scale, float lacunarity, int octaves, float phase, float seed) 
{    
    vec4 seeds = vec4(0.0, 1031.0, 537.0, 23.0) + seed;
    float f = 2.0 / lacunarity;
    
    vec4 value = vec4(0.0);
    float w = 1.0;
    float acc = 0.0;
    for (int i = 0; i < octaves; i++) 
    {
        vec2 ns = vec2(scale / w);
        vec4 n;
        n.xy = multiNoise(pos.xyxy, ns.xyxy, phase, seeds.xy);
        n.zw = multiNoise(pos.xyxy, ns.xyxy, phase, seeds.zw);
        value += (n * 0.5 + 0.5) * w;
        acc += w;
        w *= 0.5 * f;
    }
    return value / acc;
}

vec3 dotsNoise(vec2 pos, vec2 scale, float density, float size, float sizeVariation, float roundness, float seed) 
{
    pos *= scale;
    vec4 i = floor(pos).xyxy + vec2(0.0, 1.0).xxyy;
    vec2 f = pos - i.xy;
    i = mod(i, scale.xyxy);
    
    vec4 hash = hash4D(i.xy + seed);
    if (hash.w > density)
        return vec3(0.0);

    float radius = clamp(size + (hash.z * 2.0 - 1.0) * sizeVariation * 0.5, 0.0, 1.0);
    float value = radius / size;  
    radius = 2.0 / radius;
    f = f * radius - (radius - 1.0);
    f += hash.xy * (radius - 2.0);
    f = pow(abs(f), vec2((mix(20.0, 1.0, sqrt(roundness)))));

    float u = 1.0 - min(dot(f, f), 1.0);
    return vec3(clamp(u * u * u * value, 0.0, 1.0), hash.w, hash.z);
}


// worley noises

vec2 cellularNoise(vec2 pos, vec2 scale, float jitter, float phase, uint metric, float seed) 
{       
    const float kPI2 = 6.2831853071;
    pos *= scale;
    vec2 i = floor(pos);
    vec2 f = pos - i;
    
    const vec3 offset = vec3(-1.0, 0.0, 1.0);
    vec4 cells = mod(i.xyxy + offset.xxzz, scale.xyxy) + seed;
    i = mod(i, scale) + seed;
    vec4 dx0, dy0, dx1, dy1;
    multiHash2D(vec4(cells.xy, vec2(i.x, cells.y)), vec4(cells.zyx, i.y), dx0, dy0);
    multiHash2D(vec4(cells.zwz, i.y), vec4(cells.xw, vec2(i.x, cells.w)), dx1, dy1);
    dx0 = 0.5 * sin(phase + kPI2 * dx0) + 0.5;
    dy0 = 0.5 * sin(phase + kPI2 * dy0) + 0.5;
    dx1 = 0.5 * sin(phase + kPI2 * dx1) + 0.5;
    dy1 = 0.5 * sin(phase + kPI2 * dy1) + 0.5;
    
    dx0 = offset.xyzx + dx0 * jitter - f.xxxx; // -1 0 1 -1
    dy0 = offset.xxxy + dy0 * jitter - f.yyyy; // -1 -1 -1 0
    dx1 = offset.zzxy + dx1 * jitter - f.xxxx; // 1 1 -1 0
    dy1 = offset.zyzz + dy1 * jitter - f.yyyy; // 1 0 1 1
    vec4 d0 = distanceMetric(dx0, dy0, metric);
    vec4 d1 = distanceMetric(dx1, dy1, metric);
    
    vec2 centerPos = (0.5 * sin(phase + kPI2 *  multiHash2D(i)) + 0.5) * jitter - f; // 0 0
    vec4 F = min(d0, d1);
    // shuffle into F the 4 lowest values
    F = min(F, max(d0, d1).wzyx);
    // shuffle into F the 2 lowest values 
    F.xy = min(min(F.xy, F.zw), max(F.xy, F.zw).yx);
    // add the last value
    F.zw = vec2(distanceMetric(centerPos, metric), 1e+5);
    // shuffle into F the final 2 lowest values 
    F.xy = min(min(F.xy, F.zw), max(F.xy, F.zw).yx);
    
    vec2 f12 = vec2(min(F.x, F.y), max(F.x, F.y));
    // normalize: 0.75^2 * 2.0  == 1.125
    return (metric == 0u ? sqrt(f12) : f12) * (1.0 / 1.125);
}
vec3 cellularNoised(vec2 pos, vec2 scale, float jitter, float phase, float seed) 
{       
    const float kPI2 = 6.2831853071;
    pos *= scale;
    vec2 i = floor(pos);
    vec2 f = pos - i;
    
    const vec3 offset = vec3(-1.0, 0.0, 1.0);
    vec4 cells = mod(i.xyxy + offset.xxzz, scale.xyxy) + seed;
    i = mod(i, scale) + seed;
    vec4 dx0, dy0, dx1, dy1;
    multiHash2D(vec4(cells.xy, vec2(i.x, cells.y)), vec4(cells.zyx, i.y), dx0, dy0);
    multiHash2D(vec4(cells.zwz, i.y), vec4(cells.xw, vec2(i.x, cells.w)), dx1, dy1);
    dx0 = 0.5 * sin(phase + kPI2 * dx0) + 0.5;
    dy0 = 0.5 * sin(phase + kPI2 * dy0) + 0.5;
    dx1 = 0.5 * sin(phase + kPI2 * dx1) + 0.5;
    dy1 = 0.5 * sin(phase + kPI2 * dy1) + 0.5;
    
    dx0 = offset.xyzx + dx0 * jitter - f.xxxx; // -1 0 1 -1
    dy0 = offset.xxxy + dy0 * jitter - f.yyyy; // -1 -1 -1 0
    dx1 = offset.zzxy + dx1 * jitter - f.xxxx; // 1 1 -1 0
    dy1 = offset.zyzz + dy1 * jitter - f.yyyy; // 1 0 1 1
    vec4 d0 = dx0 * dx0 + dy0 * dy0; 
    vec4 d1 = dx1 * dx1 + dy1 * dy1; 
    
    vec2 centerPos = (0.5 * sin(phase + kPI2 *  multiHash2D(i)) + 0.5) * jitter - f; // 0 0
    float dCenter = dot(centerPos, centerPos);
    vec4 d = min(d0, d1);
    vec4 less = step(d1, d0);
    vec4 dx = mix(dx0, dx1, less);
    vec4 dy = mix(dy0, dy1, less);

    vec3 t1 = d.x < d.y ? vec3(d.x, dx.x, dy.x) : vec3(d.y, dx.y, dy.y);
    vec3 t2 = d.z < d.w ? vec3(d.z, dx.z, dy.z) : vec3(d.w, dx.w, dy.w);
    t2 = t2.x < dCenter ? t2 : vec3(dCenter, centerPos);
    vec3 t = t1.x < t2.x ? t1 : t2;
    t.x = sqrt(t.x);
    // normalize: 0.75^2 * 2.0  == 1.125
    return  t * vec3(1.0, -2.0, -2.0) * (1.0 / 1.125);
}

vec3 voronoi(vec2 pos, vec2 scale, float jitter, float phase, float seed)
{
     // voronoi based on Inigo Quilez: https://archive.is/Ta7dm
    const float kPI2 = 6.2831853071;
    pos *= scale;
    vec2 i = floor(pos);
    vec2 f = pos - i;

    // first pass
    vec2 minPos, tilePos;
    float minDistance = 1e+5;
    for (int y=-1; y<=1; y++)
    {
        for (int x=-1; x<=1; x++)
        {
            vec2 n = vec2(float(x), float(y));
            vec2 cPos = hash2D(mod(i + n, scale) + seed) * jitter;
            cPos = 0.5 * sin(phase + kPI2 * cPos) + 0.5;
            vec2 rPos = n + cPos - f;

            float d = dot(rPos, rPos);
            if(d < minDistance)
            {
                minDistance = d;
                minPos = rPos;
                tilePos = cPos;
            }
        }
    }

    // second pass, distance to edges
    minDistance = 1e+5;
    for (int y=-2; y<=2; y++)
    {
        for (int x=-2; x<=2; x++)
        { 
            vec2 n = vec2(float(x), float(y));
            vec2 cPos = hash2D(mod(i + n, scale) + seed) * jitter;
            cPos = 0.5 * sin(phase + kPI2 * cPos) + 0.5;
            vec2 rPos = n + cPos - f;
            
            vec2 v = minPos - rPos;
            if(dot(v, v) > 1e-5)
                minDistance = min(minDistance, dot( 0.5 * (minPos + rPos), normalize(rPos - minPos)));
        }
    }

    return vec3(minDistance, tilePos);
}
vec3 cracks(vec2 pos, vec2 scale, float jitter, float width, float smoothness, float warp, float warpScale, bool warpSmudge, float smudgePhase, float seed)
{
    vec3 g = gradientNoised(pos, scale * warpScale, smudgePhase, seed);
    pos += (warpSmudge ? g.yz : g.xx) * 0.1 * warp;
    vec3 v = voronoi(pos, scale, jitter, 0.0, seed);
    return vec3(smoothstep(max(width - smoothness, 0.0), width + fwidth(v.x), v.x), v.yz);
}

float metaballs(vec2 pos, vec2 scale, float jitter, float phase, float seed) 
{       
    const float kPI2 = 6.2831853071;
    pos *= scale;
    vec2 i = floor(pos);
    vec2 f = pos - i;
    
    const vec3 offset = vec3(-1.0, 0.0, 1.0);
    vec4 cells = mod(i.xyxy + offset.xxzz, scale.xyxy) + seed;
    i = mod(i, scale) + seed;
    vec4 dx0, dy0, dx1, dy1;
    multiHash2D(vec4(cells.xy, vec2(i.x, cells.y)), vec4(cells.zyx, i.y), dx0, dy0);
    multiHash2D(vec4(cells.zwz, i.y), vec4(cells.xw, vec2(i.x, cells.w)), dx1, dy1);
    dx0 = 0.5 * sin(phase + kPI2 * dx0) + 0.5;
    dy0 = 0.5 * sin(phase + kPI2 * dy0) + 0.5;
    dx1 = 0.5 * sin(phase + kPI2 * dx1) + 0.5;
    dy1 = 0.5 * sin(phase + kPI2 * dy1) + 0.5;
    
    dx0 = offset.xyzx + dx0 * jitter - f.xxxx; // -1 0 1 -1
    dy0 = offset.xxxy + dy0 * jitter - f.yyyy; // -1 -1 -1 0
    dx1 = offset.zzxy + dx1 * jitter - f.xxxx; // 1 1 -1 0
    dy1 = offset.zyzz + dy1 * jitter - f.yyyy; // 1 0 1 1
    vec4 d0 = dx0 * dx0 + dy0 * dy0; 
    vec4 d1 = dx1 * dx1 + dy1 * dy1; 
    
    vec2 centerPos = (0.5 * sin(phase + kPI2 * multiHash2D(i)) + 0.5) * jitter - f; // 0 0
    
    float d = min(1.0, dot(centerPos, centerPos));
    d = min(d, d * d0.x);
    d = min(d, d * d0.y);
    d = min(d, d * d0.z);
    d = min(d, d * d0.w);
    d = min(d, d * d1.x);
    d = min(d, d * d1.y);
    d = min(d, d * d1.z);
    d = min(d, d * d1.w);
    
    return sqrt(d);
}

float metaballs(vec2 pos, vec2 scale, float jitter, float phase, float width, float smoothness, float seed) 
{       
    float d = metaballs(pos, scale, jitter, phase, seed);
    return smoothstep(width, width + smoothness, d);
}

// fbms

float fbm(vec2 pos, vec2 scale, int octaves, float shift, float timeShift, float gain, float lacunarity, float octaveFactor, float seed) 
{
    float amplitude = gain;
    float time = timeShift;
    vec2 frequency = scale;
    vec2 offset = vec2(shift, 0.0);
    vec2 p = pos * frequency;
    octaveFactor = 1.0 + octaveFactor * 0.12;
    
    vec2 sinCos = vec2(sin(shift), cos(shift));
    mat2 rotate = mat2(sinCos.y, sinCos.x, sinCos.x, sinCos.y);

    float value = 0.0;
    for (int i = 0; i < octaves; i++) 
    {
        float n = noise(p / frequency, frequency, time, seed);
        value += amplitude * n;
        
        p = p * lacunarity + offset * float(1 + i);
        frequency *= lacunarity;
        amplitude = pow(amplitude * gain, octaveFactor);
        time += timeShift;
        offset *= rotate;
    }
    return value * 0.5 + 0.5;
}

vec3 fbmd(vec2 pos, vec2 scale, int octaves, vec2 shift, float timeShift, float gain, vec2 lacunarity, float slopeness, float octaveFactor, float seed) 
{
    // fbm implementation based on Inigo Quilez
    float amplitude = gain;
    float time = timeShift;
    vec2 frequency = scale;
    vec2 p = pos * frequency;
    octaveFactor = 1.0 + octaveFactor * 0.12;
    
    vec2 sinCos = vec2(sin(shift.x), cos(shift.y));
    mat2 rotate = mat2(sinCos.y, sinCos.x, sinCos.x, sinCos.y);

    vec3 value = vec3(0.0);
    vec2 derivative = vec2(0.0);
    for (int i = 0; i < octaves; i++) 
    {
        vec3 n =  noised(p / frequency, frequency, time, seed).xyz;
        derivative += n.yz;

        n *= amplitude;
        n.x /= (1.0 + mix(0.0, dot(derivative, derivative), slopeness));
        value += n; 
        
        p = (p + shift) * lacunarity;
        frequency *= lacunarity;
        amplitude = pow(amplitude * gain, octaveFactor);
        shift = shift * rotate;
        time += timeShift;
    }
    
    value.x = value.x * 0.5 + 0.5;
    return value;
}
vec3 fbmd(vec2 pos, vec2 scale, int octaves, float shift, float timeShift, float gain, float lacunarity, float slopeness, float octaveFactor, float seed) 
{
    return fbmd(pos, scale, octaves, vec2(shift), timeShift, gain, vec2(lacunarity), slopeness, octaveFactor, seed);
}
vec3 fbmd(vec2 pos, vec2 scale, int octaves, vec2 shift, float timeShift, float gain, float lacunarity, float slopeness, float octaveFactor, float seed) 
{
    return fbmd(pos, scale, octaves, shift, timeShift, gain, vec2(lacunarity), slopeness, octaveFactor, seed);
}

float fbmMetaballs(vec2 pos, vec2 scale, int octaves, float shift, float timeShift, float gain, float lacunarity, float octaveFactor, float jitter, float interpolate, vec2 width, float seed) 
{
    float amplitude = gain;
    float time = timeShift;
    vec2 frequency = scale;
    vec2 offset = vec2(shift, 0.0);
    vec2 p = pos * frequency;
    octaveFactor = 1.0 + octaveFactor * 0.12;
    
    vec2 sinCos = vec2(sin(shift), cos(shift));
    mat2 rotate = mat2(sinCos.y, sinCos.x, sinCos.x, sinCos.y);
    
    float n = 1.0;
    float value = 0.0;
    for (int i = 0; i < octaves; i++) 
    {
        float cn = metaballs(p / frequency, frequency, jitter, timeShift, width.x, width.y, seed) * 2.0 - 1.0;
        n *= cn;
        value += amplitude * mix(n, abs(n), interpolate);
        
        p = p * lacunarity + offset * float(1 + i);
        frequency *= lacunarity;
        amplitude = pow(amplitude * gain, octaveFactor);
        time += timeShift;
        offset *= rotate;
    }
    return value * 0.5 + 0.5;
}

float fbmPerlin(vec2 pos, vec2 scale, int octaves, float shift, float axialShift, float gain, float lacunarity, uint mode, float factor, float offset, float seed) 
{
    float amplitude = gain;
    vec2 frequency = floor(scale);
    float angle = axialShift;
    float n = 1.0;
    vec2 p = fract(pos) * frequency;

    float value = 0.0;
    for (int i = 0; i < octaves; i++) 
    {
        float pn = perlinNoise(p / frequency, frequency, angle, seed) + offset;
        if (mode == 0u)
        {
            n *= abs(pn);
        }
        else if (mode == 1u)
        {
            n = abs(pn);
        }
        else if (mode == 2u)
        {
            n = pn;
        }
        else if (mode == 3u)
        {
            n *= pn;
        }
        else if (mode == 4u)
        {
            n = pn * 0.5 + 0.5;
        }
        else
        {
            n *= pn * 0.5 + 0.5;
        }
        
        n = pow(n < 0.0 ? 0.0 : n, factor);
        value += amplitude * n;
        
        p = p * lacunarity + shift;
        frequency *= lacunarity;
        amplitude *= gain;
        angle += axialShift;
    }
    return value;
}

vec3 fbmdPerlin(vec2 pos, vec2 scale, int octaves, vec2 shift, mat2 transform, float gain, vec2 lacunarity, float slopeness, float octaveFactor, bool negative, float seed) 
{
    // fbm implementation based on Inigo Quilez
    float amplitude = gain;
    vec2 frequency = floor(scale);
    vec2 p = pos * frequency;
    octaveFactor = 1.0 + octaveFactor * 0.3;

    vec3 value = vec3(0.0);
    vec2 derivative = vec2(0.0);
    for (int i = 0; i < octaves; i++) 
    {
        vec3 n = perlinNoised(p / frequency, frequency, transform, seed);
        derivative += n.yz;
        n.x = negative ? n.x : n.x * 0.5 + 0.5;
        n *= amplitude;
        value.x += n.x / (1.0 + mix(0.0, dot(derivative, derivative), slopeness));
        value.yz += n.yz; 
        
        p = (p + shift) * lacunarity;
        frequency *= lacunarity;
        amplitude = pow(amplitude * gain, octaveFactor);
        transform *= transform;
    }

    return clamp(value,-1.,1.);
}
vec3 fbmdPerlin(vec2 pos, vec2 scale, int octaves, vec2 shift, float axialShift, float gain, vec2 lacunarity, float slopeness, float octaveFactor, bool negative, float seed) 
{
    vec2 cosSin = vec2(cos(axialShift), sin(axialShift));
    mat2 transform = mat2(cosSin.x, cosSin.y, -cosSin.y, cosSin.x) * mat2(0.8, -0.6, 0.6, 0.8);
    return fbmdPerlin(pos, scale, octaves, shift, transform, gain, lacunarity, slopeness, octaveFactor, negative, seed);
}

vec4 fbmVoronoi(vec2 pos, vec2 scale, int octaves, float shift, float timeShift, float gain, float lacunarity, float octaveFactor, float jitter, float interpolate, float seed) 
{
    float amplitude = gain;
    float time = timeShift;
    vec2 frequency = scale;
    vec2 offset = vec2(shift, 0.0);
    vec2 p = pos * frequency;
    octaveFactor = 1.0 + octaveFactor * 0.12;
    
    vec2 sinCos = vec2(sin(shift), cos(shift));
    mat2 rotate = mat2(sinCos.y, sinCos.x, sinCos.x, sinCos.y);
    
    float n = 1.0;
    vec4 value = vec4(0.0);
    for (int i = 0; i < octaves; i++) 
    {
        vec3 v = voronoi(p / frequency, frequency, jitter, timeShift, seed);
        v.x = v.x * 2.0 - 1.0;
        n *= v.x;
        value += amplitude * vec4(mix(v.x, n, interpolate), hash3D(v.yz));
        
        p = p * lacunarity + offset * float(1 + i);
        frequency *= lacunarity;
        amplitude = pow(amplitude * gain, octaveFactor);
        time += timeShift;
        offset *= rotate;
    }
    value.x = value.x * 0.5 + 0.5;
    return value;
}


// warp

float fbmWarp(vec2 pos, vec2 scale, vec2 factors, int octaves, vec4 shifts, float timeShift, float gain, vec2 lacunarity, float slopeness, float octaveFactor, bool negative, float seed,
              out vec2 q, out vec2 r) 
{
    // domain warping with factal sum value noise

    float qfactor = factors.x;
    float rfactor = factors.y;
    q.x = fbmd(pos, scale, octaves, vec2(0.0), timeShift, gain, lacunarity, slopeness, octaveFactor, seed).x;
    q.y = fbmd(pos, scale, octaves, vec2(shifts.x), timeShift, gain, lacunarity, slopeness, octaveFactor, seed).x;
    q = negative ? q * 2.0 - 1.0 : q;
    
    vec2 np = pos + qfactor * q;
    r.x = fbmd(np, scale, octaves, vec2(shifts.y), timeShift, gain, lacunarity, slopeness, octaveFactor, seed).x;
    r.y = fbmd(np, scale, octaves, vec2(shifts.z), timeShift, gain, lacunarity, slopeness, octaveFactor, seed).x;
    r = negative ? r * 2.0 - 1.0 : r;
    
    return fbmd(pos + r * rfactor, scale, octaves, vec2(shifts.w), timeShift, gain, lacunarity, slopeness, octaveFactor, seed).x;
}

float perlinNoiseWarp(vec2 pos, vec2 scale, float strength, float phase, float factor, float spread, float seed)
{
    vec2 offset = vec2(spread, 0.0);
    strength *= 32.0 / max(scale.x, scale.y);
    
    vec4 gp;
    gp.x = perlinNoise(pos - offset.xy, scale, phase, seed);
    gp.y = perlinNoise(pos + offset.xy, scale, phase, seed);
    gp.z = perlinNoise(pos - offset.yx, scale, phase, seed);
    gp.w = perlinNoise(pos + offset.yx, scale, phase, seed);
    gp = pow(gp, vec4(factor));
    vec2 warp = vec2(gp.y - gp.x, gp.w - gp.z);
    return pow(perlinNoise(pos + warp * strength, scale, phase, seed), factor);
}

float curlWarp(vec2 pos, vec2 scale, vec2 factors, vec4 seeds, float curl, float seed,
               out vec2 q, out vec2 r)
{
    float qfactor = factors.x;
    float rfactor = factors.y;
    vec2 curlFactor = vec2(1.0, -1.0) * vec2(curl, 1.0 - curl);
    
    vec2 n = gradientNoised(pos, scale, seed).zy * curlFactor;
    q.x = n.x + n.y;
    n = gradientNoised(pos + hash2D(seeds.xx), scale, seed).zy * curlFactor;
    q.x = n.x + n.y;
    
    vec2 np = pos + qfactor * q;
    n = gradientNoised(np + hash2D(seeds.yy), scale, seed).zy * curlFactor;
    r.x = n.x + n.y;
    n = gradientNoised(np + hash2D(seeds.zz), scale, seed).zy * curlFactor;
    r.y = n.x + n.y;

    return perlinNoise(pos + r * rfactor + hash2D(seeds.ww), scale, seed);
}

// other

float sdfLens(vec2 p, float width, float height)
{
    float d = 1.0 / width - width / 4.0;
    float r = width / 2.0 + d;
    
    p = abs(p);

    float b = sqrt(r * r - d * d);
    vec4 par = p.xyxy - vec4(0.0, b, -d, 0.0);
    return (par.y * d > p.x * b) ? length(par.xy) : length(par.zw) - r;
}
vec3 tileWeave(vec2 pos, vec2 scale, float count, float width, float smoothness)
{
    vec2 i = floor(pos * scale);    
    float c = mod(i.x + i.y, 2.0);
    
    vec2 p = fract(pos.st * scale);
    p = mix(p.st, p.ts, c);
    p = fract(p * vec2(count, 1.0));
    
    // Vesica SDF based on Inigo Quilez
    width *= 2.0;
    p = p * 2.0 - 1.0;
    float d = sdfLens(p, width, 1.0);
    vec2 grad = vec2(dFdx(d), dFdy(d));

    float s = 1.0 - smoothstep(0.0, dot(abs(grad), vec2(1.0)) + smoothness, -d);
    return vec3(s , normalize(grad) * smoothstep(1.0, 0.99, s) * smoothstep(0.0, 0.01, s)); 
}

vec3 checkers45(const in vec2 pos, const in vec2 scale, const in vec2 smoothness)
{
    // based on filtering the checkerboard by Inigo Quilez 
    vec2 numTiles = floor(scale); 
    vec2 p = pos * numTiles * 2.0;
    
    const float angle = 3.14152 / 4.0;
    const float cosAngle = cos(angle);
    const float sinAngle = sin(angle);

    p *= 1.0 / sqrt(2.0);
    p = p * mat2(cosAngle, sinAngle, -sinAngle, cosAngle);
    p += vec2(0.5, 0.0);
    vec2 tile = mod(floor(p), numTiles);
    
    vec2 w = smoothness;
    // box filter using triangular signal
    vec2 s1 = abs(fract((p - 0.5 * w) / 2.0) - 0.5);
    vec2 s2 = abs(fract((p + 0.5 * w) / 2.0) - 0.5);
    vec2 i = 2.0 * (s1 - s2) / w;
    float d = 0.5 - 0.5 * i.x * i.y; // xor pattern
    return vec3(d, tile);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = fragCoord / iResolution.y;
    vec2 p = fract(uv * TILES);
    vec2 scale = vec2(SCALE);
	
    const float totalCount = 19.0;
    vec3 col;
    float current;
    if(iMouse.z > 0.0)
    	current = (iMouse.x / iResolution.x) * totalCount;
    else
    {
        current = mod(iTime * 0.5, totalCount);
#if ANIMATE == 1
    	p += iTime * 0.03;
#endif
    }

    current = clamp(floor(current), 0.0, totalCount - 1.0);

    if(current < 1.0)
    {
        col = vec3(perlinNoise(p, scale, /*rotation:*/ iTime * 0.25, 0.0)) * 0.5 + 0.5;
    }
    else if(current < 2.0)
    {
        uint mode = uint(mod(iTime, 5.0));
        if(mode == 4u)
        {
            col += cellularNoised(p, scale, /*jitter:*/ 1.0, /*phase*/ iTime * 0.5, 23.0).yzx * 0.5 + 0.5;
        }
        else
        {
        	vec2 cells = cellularNoise(p, scale, /*jitter:*/ 1.0, /*phase:*/ iTime * 0.5, /*metric*/ mode, 0.0);
        	col += 1.0 - cells.x;
        }
        
    }
    else if(current < 3.0)
    {
        col = vec3(perlinNoiseWarp(p, scale, /*strength:*/ 0.9, /*phase:*/ iTime * 0.5, mix(2.0, 6.0, abs(sin(iTime))), /*spread:*/ 0.001, 0.0));
    }
    else if(current < 4.0)
    {
        vec2 tilePos;
        vec3 c = cracks(p, scale, /*jitter*/ 0.5 + sin(iTime) * 0.2, /*width:*/ 0.1, /*smoothness:*/ 0.5, /*warp:*/ 0.1, /*warpScale:*/ 4.0, /*warpSmudge:*/ true, /*smudgePhase:*/ iTime * 0.75, 23.0);
        col += c.x;
        col *= hash3D(c.yz*0.0001);
    }
    else if(current < 5.0)
    {
        float jitter = 0.2 + sin(iTime*0.5 + 23.0) * 0.1;
        col+= randomLines(p + vec2(0.0, iTime * 0.35), scale, /*count*/ 14.0, /*width*/ 0.15, jitter, /*smoothness*/ vec2(0.1, 0.5), /*phase*/ iTime * 0.5, /*colorVariation*/ 0.5 + sin(iTime*0.5) * 0.5, 13.0).rgb;
    }
    else if(current < 6.0)
    {
       vec3 c = checkers45(p, scale, /*smoothness*/ vec2(0.5));
       col += c.x;
        
       vec4 hash = hash4D(c.yz + mod(floor(iTime * 1.5), 128.0));
       if(hash.w < (0.5 + sin(iTime * 0.5) * sin(iTime * 0.5) * 0.2))
       		col += smoothstep(0.5, 0.0, c.x) * hash.rgb;
    }
    else if(current < 7.0)
    {
        vec3 d = dotsNoise(p, scale, /*density*/ 0.5 + sin(iTime * 0.5) * 0.25, /*size*/ 0.45, /*sizeVariation*/ 0.7 + sin(iTime + 0.25) * 0.3, /*roundness*/ 0.7 + sin(iTime * 2.0 + 23.0) *  0.3, 0.0);
        col += hash3D(d.yy) * d.x * (d.z + 0.5);
    }
    else if(current < 8.0)
    {
        vec2 offset = vec2(iTime,cos(iTime*0.25)*2.)*0.015;
		col += fbm(uv + offset, scale, 9, /*shift:*/ 23.0,  /*timeShift: */ iTime * 0.25, /*gain:*/ 0.49, /*lacunarity*/ 2.0, /*octaveFactor:*/ -0.5, 0.0);
    }
    else if(current < 9.0)
    {
        
        col += fbmMetaballs(p, scale, 8, 0.0, /*timeShift:*/ iTime * 0.5, /*gain*/ 0.5, /*lacunarity*/ 2.0, /*octaveFactor*/ 0.0, /*jitter*/ 1.0, /*interpolate*/ 0.1, vec2(0.1, 0.03), 0.0); 
    }

    else if(current < 10.0)
    {
        vec4 v = fbmVoronoi(p, scale, 16, /*shift*/ 0.5, /*timeShift:*/ iTime * 0.000005, /*gain*/ 0.5, /*lacunarity*/ 2.0, /*octaveFactor:*/ -0.3, /*jitter*/ 1.0, /*interpolate*/ 0.0, 0.00);
        col = vec3(smoothstep(0.02, 0.10, v.x)) * v.yzw;
    }
    else if(current < 11.0)
    {
        vec3 tw = tileWeave(p, scale * 0.5, /*count*/ 3.0, /*width*/ 0.75, /*smoothness*/ 0.5 + sin(iTime) * 0.3);
        col = vec3(fragCoord.x / iResolution.x < 0.5 ? tw.xxx : normalize(vec3(tw.yz, 1.0)) * 0.5 + 0.5);
    }
    else if(current < 12.0)
    {
        float axialShift = sin(iTime * 4.25) * sin(iTime * 4.25 + 0.53) * 0.64;
        col = vec3(fbmPerlin(p, scale * 0.5, 16, /*shift*/ -iTime * 0.21, axialShift, /*gain*/ 0.3, /*lacunarity*/ 2.0, /*mode*/ 0u, /*factor*/ 0.75, /*offset*/ 0.0, 0.0)) * 2.0;
    }
    else if(current < 13.0)
    {
        vec2 phase = vec2(iTime *0.15, iTime *0.05 + 0.5);
        col = vec3(organicNoise(p, scale, /*density*/ 1.0, phase, /*contrast*/ 0.0, /*highlights*/ 0.25, /*shift*/ 0.5 + 0.4 * sin(iTime),0.));
    }
    else if(current < 14.0)
    {
        col = fbmd(p, scale, 16, /*shift:*/ vec2(0.0), /*timeShift*/ iTime * 0.5, /*gain:*/ 0.5, /*lacunarity*/ vec2(2.0), /*slopeness:*/ 0.2 + sin(iTime) * 0.2, /*octaveFactor:*/ 0.5, 0.0).yzx * 0.5 + 0.5;
        col = mix(col, col.zzz * 2.0 - 1.0, step(fragCoord.x / iResolution.x, 0.5));
    }
    else if(current < 15.0)
    {
        col = fbmdPerlin(p, scale, 8, /*shift:*/ vec2(iTime)*0.01, /*axialShift*/ iTime * 0.2, /*gain:*/ 0.45, vec2(2.0), /*slopeness:*/ 0.5, /* octaveFactor*/ sin(iTime) * 0.5, false, 0.0).yzx;
        col = mix(normalize(col + vec3(0.,0.,1.)) * 0.5 + 0.5, col.zzz, step(fragCoord.x / iResolution.x, 0.5));
    }
    else if(current < 17.0)
    {
        // domain warping example
        
        int octaves = 16;
        float gain = 0.5;
        float lacunarity = 2.0;
        float slopeness = 0.5;
        vec2 factors = vec2(1.0);
        vec4 shifts = vec4(123.0, 235.0, 53.0, 511.0);
        
        vec2 q;
        vec2 r;
    	float f = current < 16.0 ? fbmWarp(p, scale, factors, octaves, shifts, iTime * 0.1, gain, vec2(lacunarity), slopeness, 0.0, false, 0.0, q, r) :
        fbmWarp(p, scale, factors, octaves, shifts, iTime * 0.25, gain*0.93, vec2(lacunarity), slopeness, -0.5, true, 0.0, q, r);
        
        if (current < 16.0)
        {
            col = mix(vec3(0.1,0.5,0.8), vec3(0.6,0.6,0.3), clamp((f * f) * 8.0, 0.0, 1.0));
            col = mix(col, vec3(0.0, 0.1, 0.05), length(q));
            col = mix(col, vec3(0.8, 0.8, 0.8), r.x);
            col = mix(col, col * vec3(0.8, 0.4, 0.2), 0.5 * pow(length(r), 4.0));
        }
        else
        {
            col = mix(vec3(0.1,0.4,0.7), vec3(0.6,0.5,0.3), clamp((f * f) * 1.0, 6.0, 3.0));
            col = mix(col, vec3(0.0, 0.1, 0.05), length(q));
            col = mix(col, vec3(0.1), r.x);
            col = mix(col, col * vec3(1.8, 0.4, 0.2), 0.75 * pow(length(r), 10.0)) * 0.95;
        }
    }
    else if(current < 18.0)
    {
        // domain warping with curl noise example

        vec2 factors = vec2(1.25);
        float curl = abs(sin(iTime * 0.5));
        curl *= curl;
        vec4 seeds = vec4(123.0, 235.0, 53.0, 511.0);
        scale *= 0.25;
        
        vec2 q;
        vec2 r;
    	float f = curlWarp(p, scale, factors, seeds, curl, 2.0, q, r);
        col = vec3(1.0 - f);
        col = mix(col, vec3(0.0, 0.1, 0.05), length(q));
        col = mix(col, col * vec3(0.2, 0.4, 0.8), pow(length(r), 2.0));
    }
    else if(current < 19.0)
    {
        // multi value noise FBM
        
        int octaves = 16;
        float lacunarity = 2.0;
        
        vec4 n = fbmMulti(uv, floor(scale * vec2(0.5, 1.)), lacunarity, octaves, iTime, 0.0);
        n.xy = n.xz - n.yw;
        vec2 v = 1.0 - pow(abs(n.xy) * 4.0, vec2(0.1));
        col = vec3(pow(1.0 - v.x * v.y, 3.0));
    }
    
#if SHOW_TILING == 1
    vec2 pixel = vec2(TILES) / iResolution.xy;
    uv *= TILES;

    vec2 first = step(pixel, uv) * floor(mod(iTime * 0.75, 4.0)) * 0.25;        
    uv  = step(fract(uv), pixel);               
    col = mix(col, vec3(0.0, 1.0, 0.0), (uv.x + uv.y) * first.x * first.y);
#endif

    fragColor = vec4(col, 1.0);
}
'''


class StTileableTexturesCollection(GlslSource):
    name = 'st_tileable_textures_collection'
    label = 'Tileable textures collection'
    description = 'Collection of tileable procedural textures such as: cellular noise, fbm, voronoi, perlin and other. Github link:...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
