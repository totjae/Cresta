---
name: Cresta Professional Trading
colors:
  surface: '#0b1326'
  surface-dim: '#0b1326'
  surface-bright: '#31394d'
  surface-container-lowest: '#060e20'
  surface-container-low: '#131b2e'
  surface-container: '#171f33'
  surface-container-high: '#222a3d'
  surface-container-highest: '#2d3449'
  on-surface: '#dae2fd'
  on-surface-variant: '#c1c6d7'
  inverse-surface: '#dae2fd'
  inverse-on-surface: '#283044'
  outline: '#8b90a0'
  outline-variant: '#414755'
  surface-tint: '#adc6ff'
  primary: '#adc6ff'
  on-primary: '#002e69'
  primary-container: '#4b8eff'
  on-primary-container: '#00285c'
  inverse-primary: '#005bc1'
  secondary: '#53e16f'
  on-secondary: '#003911'
  secondary-container: '#05b046'
  on-secondary-container: '#003a11'
  tertiary: '#ffb4aa'
  on-tertiary: '#690003'
  tertiary-container: '#ff5545'
  on-tertiary-container: '#5c0002'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#d8e2ff'
  primary-fixed-dim: '#adc6ff'
  on-primary-fixed: '#001a41'
  on-primary-fixed-variant: '#004493'
  secondary-fixed: '#72fe88'
  secondary-fixed-dim: '#53e16f'
  on-secondary-fixed: '#002107'
  on-secondary-fixed-variant: '#00531c'
  tertiary-fixed: '#ffdad5'
  tertiary-fixed-dim: '#ffb4aa'
  on-tertiary-fixed: '#410001'
  on-tertiary-fixed-variant: '#930005'
  background: '#0b1326'
  on-background: '#dae2fd'
  surface-variant: '#2d3449'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  title-sm:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  data-mono:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
  label-caps:
    fontFamily: Inter
    fontSize: 10px
    fontWeight: '700'
    lineHeight: 12px
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  container-padding: 16px
  gutter: 12px
  component-gap: 8px
  compact-padding: 4px 8px
---

## Brand & Style
The design system is engineered for high-stakes intraday trading, where speed of comprehension and emotional stability are paramount. The brand personality is clinical, precise, and authoritative. It targets professional traders who require an environment that minimizes eye strain during long sessions while highlighting critical data changes instantly.

The visual style is **Corporate Modern with Glassmorphic accents**. It utilizes deep, layered surfaces to create a sense of focused immersion. The aesthetic leans into a "command center" feel—highly structured, dense with information, yet visually quiet enough to let real-time AI insights stand out. Key interface elements use subtle transparency and thin, high-contrast borders to define boundaries without adding visual bulk.

## Colors
The palette is optimized for a low-light "dark mode" environment. The primary background uses a deep navy-black to provide maximum contrast for financial data.

- **Primary Blue (#007AFF):** Reserved for core AI actions, focus states, and primary navigation.
- **Semantic Logic:** Success Green and Danger Red are used strictly for directional market data (gains/losses) and execution buttons (Buy/Sell). Warning Amber is used for volatility alerts and system notifications.
- **Neutral Scales:** Utilizes Slate and Navy tones rather than pure grays to maintain a premium, high-tech atmosphere.
- **Data Visualization:** Secondary charts should use desaturated versions of the semantic palette to avoid visual fatigue.

## Typography
This design system prioritizes legibility and numerical alignment. **Inter** is the primary typeface for UI elements and headings due to its excellent readability at small sizes.

For all financial figures, price tickers, and timestamps, **JetBrains Mono** or Inter with **Tabular Figures (tnum)** enabled must be used. This ensures that numbers align vertically in data tables, preventing "shimmering" during rapid price updates.

- **Headlines:** High contrast white (#FFFFFF) for immediate recognition.
- **Body:** Muted slate (#94A3B8) for descriptions and secondary metadata.
- **Hierarchy:** Use font weight rather than size increases to maintain information density in the dashboard.

## Layout & Spacing
The layout follows a **Fluid Grid** model optimized for a multi-monitor dashboard setup. It uses a base 4px unit to allow for extreme density without sacrificing alignment.

- **Desktop (1440px+):** 12-column grid with 12px gutters. Sidebars are fixed at 240px; main content areas are modular "widgets" that can span multiple columns.
- **Tablet (768px - 1024px):** 6-column grid. AI insights move to a bottom-sheet or collapsible drawer.
- **Mobile (375px):** Single column stack. Navigation shifts to a bottom bar.
- **Density:** The system defaults to a "Compact" spacing model. Vertical padding in data rows is kept to 4px–6px to maximize the number of visible assets.

## Elevation & Depth
Depth is signaled through **Tonal Layering** and **Glassmorphism**, rather than traditional shadows which can muddy a dark UI.

- **Level 0 (Base):** Deep Navy (#020617) – The canvas.
- **Level 1 (Widgets):** Surface Glass (rgba(30, 41, 59, 0.7)) – Semi-transparent layers with a 12px Backdrop Blur and a 1px border (rgba(255, 255, 255, 0.1)).
- **Level 2 (Popovers/Modals):** Solid Slate (#1E293B) – Elevated above the glass layer with a slight ambient glow using the Primary Blue at 10% opacity.
- **Interaction:** Hovering over a table row or card should trigger a "Border Highlight" where the stroke opacity increases from 0.1 to 0.4.

## Shapes
The shape language is "Soft" but leaning toward "Sharp" to maintain a professional, analytical feel.

- **Containers:** 4px radius for a precise, technical look.
- **Buttons/Inputs:** 4px radius to match containers.
- **Status Badges:** Fully rounded (pill-shaped) to distinguish them from actionable buttons.
- **Charts:** Line graphs should use a 1.5px stroke width with sharp vertices (no smoothing) to accurately represent price action.

## Components
Consistent component behavior ensures execution speed.

- **Trading Buttons:** "Buy" and "Sell" buttons use full-bleed semantic colors (#34C759 and #FF3B30). They feature a subtle top-down gradient to give a tactile, "pressable" feel.
- **Data Tables:** Headers are `label-caps` in muted gray. Rows feature a 1px bottom border. Real-time changes trigger a brief background "flash" (green for up, red for down) at 20% opacity.
- **AI Insights:** Highlighted with a `primary-blue` left-accent border and a very subtle glow effect to draw the eye.
- **Input Fields:** Darker than the widget surface with a 1px border. On focus, the border transitions to the Primary Blue.
- **Mini-Charts (Sparklines):** Simplified, no-axis line charts embedded in tables to show 24h trends. Positive trends are Success Green; negative are Danger Red.
- **Status Badges:** Small, pill-shaped indicators for "Market Open," "Live," or "Pending." Use a combination of a colored dot and text.
