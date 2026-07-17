---
name: Bronze Intelligence
colors:
  surface: '#fcf9f8'
  surface-dim: '#dcd9d9'
  surface-bright: '#fcf9f8'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f6f3f2'
  surface-container: '#f0eded'
  surface-container-high: '#eae7e7'
  surface-container-highest: '#e5e2e1'
  on-surface: '#1c1b1b'
  on-surface-variant: '#4f453c'
  inverse-surface: '#313030'
  inverse-on-surface: '#f3f0ef'
  outline: '#81756a'
  outline-variant: '#d2c4b8'
  surface-tint: '#775836'
  primary: '#775836'
  on-primary: '#ffffff'
  primary-container: '#af8b65'
  on-primary-container: '#3d2508'
  inverse-primary: '#e8bf96'
  secondary: '#5d5f5d'
  on-secondary: '#ffffff'
  secondary-container: '#e2e3e1'
  on-secondary-container: '#636563'
  tertiary: '#5d5f5f'
  on-tertiary: '#ffffff'
  tertiary-container: '#919292'
  on-tertiary-container: '#292b2c'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ffddbb'
  primary-fixed-dim: '#e8bf96'
  on-primary-fixed: '#2b1700'
  on-primary-fixed-variant: '#5d4121'
  secondary-fixed: '#e2e3e1'
  secondary-fixed-dim: '#c6c7c5'
  on-secondary-fixed: '#1a1c1b'
  on-secondary-fixed-variant: '#454746'
  tertiary-fixed: '#e2e2e2'
  tertiary-fixed-dim: '#c6c6c7'
  on-tertiary-fixed: '#1a1c1c'
  on-tertiary-fixed-variant: '#454747'
  background: '#fcf9f8'
  on-background: '#1c1b1b'
  surface-variant: '#e5e2e1'
typography:
  headline-lg:
    fontFamily: Hanken Grotesk
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Hanken Grotesk
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
  label-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 16px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  sidebar_width: 260px
  utility_width: 320px
  gutter: 24px
  container_padding: 32px
  element_gap: 12px
---

## Brand & Style
This design system is built for high-performance AI productivity. It utilizes a **Corporate Modern** aesthetic characterized by exceptional clarity, structured information density, and a sophisticated color palette. The personality is "Quietly Intelligent"—it prioritizes the user's focus through an expansive use of white space and soft tonal layering, punctuated by a warm bronze accent that signifies human-agent collaboration and high-value actions.

The visual narrative avoids the typical neon-futurism of AI, opting instead for an enterprise-ready, editorial feel that suggests reliability, precision, and executive-level utility.

## Colors
The palette is rooted in a "Warm Industrial" spectrum. 

- **Primary Bronze (#AF8B65):** Reserved for primary CTA buttons, active states, and critical highlights. It provides a human, grounded contrast to the digital nature of AI.
- **Surface Tiers:** Backgrounds use a tiered approach: `#FFFFFF` for the main workspace/chat cards, `#F7F7F5` for the base app background, and `#F2F2F0` for sidebar or secondary utility panels.
- **Typography:** The primary text color is a deep charcoal (`#1A1A1A`), ensuring high legibility. Secondary metadata and labels use a muted gray (`#70706B`).
- **Semantic Accents:** Subtle greens and blues are used exclusively for status indicators (e.g., "Complete" icons) within the chat flow.

## Typography
The system uses a paired sans-serif approach. **Hanken Grotesk** is used for headings to provide a sharp, contemporary edge that distinguishes the product from generic SaaS tools. **Inter** is utilized for all body, UI labels, and data-heavy components to maximize legibility.

Vertical rhythm is strictly maintained with a 4px baseline grid. Body text should always prioritize line height (1.5x font size) to ensure long-form AI responses are easily scannable.

## Layout & Spacing
The system employs a **Fixed-Fluid-Fixed** three-column structure:
1.  **Navigation (Fixed):** 260px left sidebar for global navigation and chat history.
2.  **Workspace (Fluid):** Central chat and document area that expands to fill available space, optimized for a max-width of 900px to maintain line-length readability.
3.  **Intelligence Sidebar (Fixed):** 320px right utility panel for tools, knowledge bases, and context.

Margins are generous (32px) to prevent the interface from feeling cramped. Elements within cards utilize a tight 12px or 16px internal padding to maintain a structured, technical appearance.

## Elevation & Depth
Depth is created through **Tonal Layering** rather than heavy shadows. 

- **Level 0 (Background):** `#F7F7F5` (Base application canvas).
- **Level 1 (Cards/Panels):** `#FFFFFF` with a 1px solid border (`#E5E5E1`).
- **Level 2 (Popovers/Dropdowns):** `#FFFFFF` with a soft, diffused shadow: `0px 4px 20px rgba(0, 0, 0, 0.05)`.

Interactive elements like buttons use a subtle "pressed" effect (0.5px inset shadow) rather than physical elevation, keeping the aesthetic flat and modern.

## Shapes
The design system follows a consistent **Rounded** geometry. 

- **Standard UI (Buttons, Inputs):** 0.5rem (8px).
- **Containers (Chat bubbles, Cards):** 1rem (16px).
- **Secondary Elements (Chips, Tags):** 2rem (Pill-shaped) for high visual distinction.

The 16px radius for large cards is a signature element, providing a soft, approachable frame for complex AI data.

## Components
- **Primary Buttons:** Solid Bronze background with white text. Use high-contrast for primary actions like "Share" or "Run."
- **Secondary Buttons:** Ghost style with `#E5E5E1` borders and charcoal text.
- **Chat Bubbles:** Agent responses utilize a white card with a subtle border. User prompts are slightly inset or use a faint off-white background to distinguish the dialogue flow.
- **Tool Cards:** Compact cards in the right sidebar with 40px square icons. Icons should use thin 1.5pt strokes.
- **Input Field:** Large, multi-line text area for the prompt, featuring a floating action bar at the bottom for attachments and model selection.
- **Progress Steppers:** Vertical timeline-style indicators within the chat flow to show the AI's "chain of thought," using 16px circular icons and dashed connecting lines.