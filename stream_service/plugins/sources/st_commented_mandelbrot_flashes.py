"""Commented Mandelbrot (Flashes): A Mandelbrot fractal, but I commented the code in a manner I wish I had seen a while back :) I made my own complex number type instead of using a vec2 for...

Source: https://www.shadertoy.com/view/tlBGW3
Author: glyre
License: Unlicense
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Dedicated to the public domain via the Unlicense
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// A commented Mandelbrot fractal shader
// Dedicated to the public domain via the Unlicense

//
// Complex Math
//

/** A complex number. */
struct complex {
    float real;
    float imag;
};

/** Add two complex numbers together. */
complex complex_add(complex a, complex b) {
    complex sum;
    sum.real = a.real + b.real;
    sum.imag = a.imag + b.imag;
    return sum;
}

/** Multiply two complex numbers together. */
complex complex_multiply(complex a, complex b) {
    complex product;
    product.real = a.real * b.real - a.imag * b.imag;
    product.imag = a.real * b.imag + a.imag * b.real;
    return product;
}

/** Square a complex number. */
complex complex_square(complex a) {
    return complex_multiply(a, a);
}

//
// Mandelbrot Fractal
//

/**
 * The maximum number of Mandelbrot iterations. If we hit this value,
 * then we know the pixel under test lies in the Mandelbrot set.
 */
const int MAX_ITERATIONS = 100;

// HSV to RGB converter by Sam Hocevar (http://sam.zoy.org/)
// https://github.com/hughsk/glsl-hsv2rgb/blob/master/index.glsl
vec3 hsv2rgb(vec3 c) {
  	vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  	vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  	return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
	// Get normalized pixel coordinates
    // Center is (0, 0), bottom-left is (-0.5, -0.5), and top-right is (0.5, 0.5)
    vec2 uv = fragCoord.xy / iResolution.xy - 0.5;

    // Correct the aspect ratio
    uv.x *= iResolution.x / iResolution.y;

    // Zoom the "camera" in and out of the scene
    uv.xy += uv.xy * 0.5 * (sin(0.25 * 3.14 * iTime) - 0.5);
 
    // Move the "camera" around the scene
    uv.x -= 1.0 + 0.5 * cos(0.125 * 3.14 * iTime);
    uv.y += 0.5 * sin(0.5 * 3.14 * iTime);

    // Start iterating at zero (which is both a real and a complex number)
    complex z;
    z.real = 0.0;
    z.imag = 0.0;

    // Run a bunch of iterations to test if this pixel is in the Mandelbrot set
    // The iterations variable is outside the loop so we can go back and retrieve it
    int iterations;
    for (iterations = 0; iterations < MAX_ITERATIONS; ++iterations) {
        // Wrap pixel coordinates into a complex term (treat x as real and y as imaginary)
        // This will let us check if the pixel is in the Mandelbrot set
        // If it is not, we will remember how many iterations it took to figure out it's not
        // This number of iterations plotted against the XY plane makes a fractal! Neat!
        complex c;
        c.real = uv.x;
        c.imag = uv.y;

        // Update the Mandelbrot algorithm (from Wikipedia)
        z = complex_add(complex_square(z), c);

        // If the series diverged
        // We use the number 2 as an "escape" value to test for divergence
        if (z.real * z.real + z.imag * z.imag > 2.0 * 2.0) {
            // Stop iterating
            break;
        }
    }

    // The color intensity of this pixel
    // As mentioned above, the fractal is not the set, per se...
    // To get the fractal, we plot the # of iterations we took at each pixel
    // The more iterations a pixel took, the brigher we make it (more intense)
    float intensity = 0.25 + float(iterations) / float(MAX_ITERATIONS);

    // Color this pixel according to its intensity
    fragColor = vec4(1.5 * intensity * hsv2rgb(vec3(intensity + 1.5 * iTime, 0.75, 0.75)), 1);
}
'''


class StCommentedMandelbrotFlashes(GlslSource):
    name = 'st_commented_mandelbrot_flashes'
    label = 'Commented Mandelbrot (Flashes)'
    description = 'A Mandelbrot fractal, but I commented the code in a manner I wish I had seen a while back :) I made my own complex number type instead of using a vec2 for...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
