
import type { Config } from "tailwindcss";

// Dark mode (owner, 23 Oct: "make sure our various screens in this app all respond properly to dark mode"): every
// Tailwind colour is a CSS variable, one set per use, so a class like bg-stone-50 or text-teal-900 turns dark by
// itself (src/palette.css, written by scripts/gen_palette.mjs; the device's own setting, as a media query).
const FAMILIES = ["slate", "gray", "zinc", "neutral", "stone", "red", "orange", "amber", "yellow", "lime", "green", "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia", "pink", "rose"];
const SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];
const palette = (role: "bg" | "fg" | "bd") =>
	Object.fromEntries(FAMILIES.map((f) => [f, Object.fromEntries(SHADES.map((s) => [s, `rgb(var(--${role}-${f}-${s}) / <alpha-value>)`]))]));
const bg = { ...palette("bg"), white: "rgb(var(--bg-white) / <alpha-value>)" };
const fg = palette("fg");
const bd = palette("bd");

export default {
	// dark: variants follow the device too, never under html.light-only (the TVs' /display pages)
	darkMode: ["variant", "@media (prefers-color-scheme: dark) { html:not(.light-only) & }"],
	content: [
		"./pages/**/*.{ts,tsx}",
		"./components/**/*.{ts,tsx}",
		"./app/**/*.{ts,tsx}",
		"./src/**/*.{ts,tsx}",
	],
	prefix: "",
	theme: {
		container: {
			center: true,
			padding: '2rem',
			screens: {
				'2xl': '1400px'
			}
		},
		extend: {
			backgroundColor: bg,
			gradientColorStops: bg,
			ringOffsetColor: bg,
			textColor: fg,
			fill: fg,
			stroke: fg,
			caretColor: fg,
			accentColor: fg,
			placeholderColor: fg,
			textDecorationColor: fg,
			borderColor: bd,
			ringColor: bd,
			divideColor: bd,
			outlineColor: bd,
			boxShadowColor: bd,
			colors: {
				border: 'hsl(var(--border))',
				input: 'hsl(var(--input))',
				ring: 'hsl(var(--ring))',
				background: 'hsl(var(--background))',
				foreground: 'hsl(var(--foreground))',
				primary: {
					DEFAULT: 'hsl(var(--primary))',
					foreground: 'hsl(var(--primary-foreground))'
				},
				secondary: {
					DEFAULT: 'hsl(var(--secondary))',
					foreground: 'hsl(var(--secondary-foreground))'
				},
				destructive: {
					DEFAULT: 'hsl(var(--destructive))',
					foreground: 'hsl(var(--destructive-foreground))'
				},
				muted: {
					DEFAULT: 'hsl(var(--muted))',
					foreground: 'hsl(var(--muted-foreground))'
				},
				accent: {
					DEFAULT: 'hsl(var(--accent))',
					foreground: 'hsl(var(--accent-foreground))'
				},
				popover: {
					DEFAULT: 'hsl(var(--popover))',
					foreground: 'hsl(var(--popover-foreground))'
				},
				card: {
					DEFAULT: 'hsl(var(--card))',
					foreground: 'hsl(var(--card-foreground))'
				},
				sidebar: {
					DEFAULT: 'hsl(var(--sidebar-background))',
					foreground: 'hsl(var(--sidebar-foreground))',
					primary: 'hsl(var(--sidebar-primary))',
					'primary-foreground': 'hsl(var(--sidebar-primary-foreground))',
					accent: 'hsl(var(--sidebar-accent))',
					'accent-foreground': 'hsl(var(--sidebar-accent-foreground))',
					border: 'hsl(var(--sidebar-border))',
					ring: 'hsl(var(--sidebar-ring))'
				}
			},
			borderRadius: {
				lg: 'var(--radius)',
				md: 'calc(var(--radius) - 2px)',
				sm: 'calc(var(--radius) - 4px)'
			},
			keyframes: {
				'accordion-down': {
					from: {
						height: '0'
					},
					to: {
						height: 'var(--radix-accordion-content-height)'
					}
				},
				'accordion-up': {
					from: {
						height: 'var(--radix-accordion-content-height)'
					},
					to: {
						height: '0'
					}
				},
				'slide-in-up': {
					from: { transform: 'translateY(100%)' },
					to: { transform: 'translateY(0)' }
				},
				'slide-out-down': {
					from: { transform: 'translateY(0)' },
					to: { transform: 'translateY(100%)' }
				}
			},
			animation: {
				'accordion-down': 'accordion-down 0.2s ease-out',
				'accordion-up': 'accordion-up 0.2s ease-out',
				'slide-in-up': 'slide-in-up 0.3s ease-out forwards',
				'slide-out-down': 'slide-out-down 0.3s ease-out forwards'
			}
		}
	},
	plugins: [require("tailwindcss-animate")],
} satisfies Config;
