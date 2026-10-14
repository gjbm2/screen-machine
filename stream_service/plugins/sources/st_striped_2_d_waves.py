"""Striped 2 D Waves: Saw a fun GIF and wanted to re-create it. First version went pretty quick until I realized there was a slight overlap on wave crests. Love any feedback,...

Source: https://www.shadertoy.com/view/Wl33RN
Author: Codax
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2019 Miguel "Codax" Nieves
// Twitter: @GameDevMig
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


#define PI 3.14159265359

//Inigo Quilez's Palette Function
//https://www.shadertoy.com/view/ll2GD3
vec3 pal( in float t, in vec3 a, in vec3 b, in vec3 c, in vec3 d )
{
    return a + b*cos( 6.28318*(c*t+d) );
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = fragCoord/iResolution.xy;
    
    //How many stripes to show (even works best)
    float stripes = 16.0;
    //Wave Scroll Speed
    float speed = 0.35;
    
    //Animated height of each wave from -1 to 1
    float amplitude = sin(iTime * 2.0);
    //float amplitude = (2.0 * abs( (2.0 * fract(iTime * 0.5))-1.0 ) - 1.0); //Triangle Wave
    amplitude *= 0.68;//0.78;
    
    uv.y = uv.y * stripes;
    uv.x = uv.x * 4.0;
    
    float waveID = round(uv.y);
    
    //Current Wave but cap the parts would overlap
    vec2 waveUV = uv;
    waveUV.x += iTime  * speed * (2.0 * step(1.0,mod(waveID,2.0)) - 1.);
    waveUV.y += max(-0.5, min(0.5, sin(waveUV.x * PI * 2.0) * amplitude));

	//Use next line only if amplitude is between -0.5 and 0.5
    //waveUV.y += sin(waveUV.x * PI * 2.0) * amplitude; 
    
    //Hold on to the current Stripe value
    float midWave = waveUV.y;
    
    //Calculate the Value from the Stripe Above
    float upperWave = uv.x + ( iTime * speed * (2.0 * step(1.0,mod(waveID + 1.0,2.0)) - 1.));
    upperWave = sin(upperWave * PI * 2.0) * amplitude;
    
    upperWave *= 1.0- step(0.5, fract(uv.y));
    /*if (fract(uv.y) >= 0.5) //Optimized Out
    {
        upperWave = 0.0;
    }*/
                      
	upperWave += fract(uv.y);
    upperWave = step(1.0, upperWave);
    
    //Calculate the Value from the Strip Below
    float lowerWave = uv.x + ( iTime * speed * (2.0 * step(1.0,mod(waveID - 1.0,2.0)) - 1.));
    lowerWave = sin(lowerWave * PI * 2.0) * amplitude;
    
    lowerWave *= step(0.5, fract(uv.y));
    /*if (fract(uv.y) < 0.5) //Optimized Out
    {
        lowerWave = 0.0;
    }
	*/
    lowerWave += fract(uv.y);
    lowerWave = step(0.00, lowerWave);


    //Mix and Overlap
	midWave *= upperWave;	//Use the Upperwave to first mask the mid wave
    upperWave *= step(0.000,midWave - waveID); //Then overlap the upperwave by the midwave
    
    waveUV.y *= (1.0 - upperWave); //Mask out the upper Wave
    waveUV.y += upperWave * (uv.y + 1.0); //Add in the upper 
    
	waveUV.y *= lowerWave;  //Mask out the lower wave from the 
    waveUV.y += (1.0 - lowerWave) * (uv.y - 1.0); //Put in the overlap from the lower wave

   
    //Create a color ID from 0.0 - 1.0
    float colorID = floor( waveUV.y ) / stripes;
    
    float ct = iTime * 0.1;
    //vec3 col = pal( colorID, vec3(0.5,0.5,0.5),vec3(0.5,0.5,0.5),vec3(2.0,1.0,1.0),vec3(0.4 * sin(ct*1.23),0.40 * cos(ct*2.14),0.80 * sin(-ct)) );
    vec3 col = pal( colorID, vec3(0.5,0.5,1.0),vec3(0.5,0.5,1.0),vec3(2.0, 1.0, 0.0),vec3(0.2+sin(ct*1.23), 0.2+cos(ct*2.14), 0.1 + sin(-ct)));
    
    //Darken the bottom few waves
    col *= smoothstep(1.0, 0.6, 1.0 - colorID);

    // Wave Debug
    //col = vec3(colorID);// * fract(uv.y));

    // Output to screen 
    fragColor = vec4(col,1.0);
}'''


class StStriped2DWaves(GlslSource):
    name = 'st_striped_2_d_waves'
    label = 'Striped 2 D Waves'
    description = 'Saw a fun GIF and wanted to re-create it. First version went pretty quick until I realized there was a slight overlap on wave crests. Love any feedback,...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
