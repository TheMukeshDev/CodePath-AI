"""
Comprehensive Brand Asset Generator for CodePath AI
Generates production-grade SVG vectors, raster PNGs at multiple resolutions,
multi-size favicon.ico, light/dark/mono variants, and interactive showcase.
"""

from __future__ import annotations
import os
import subprocess
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # optional: only needed to rasterise the SVG assets
    Image = None  # type: ignore[assignment]

# Paths are derived from this file so the script is portable across machines.
WORKSPACE_DIR = Path(__file__).resolve().parent
BRAND_DIR = WORKSPACE_DIR / "public" / "brand"
PUBLIC_DIR = WORKSPACE_DIR / "public"
# Edge is only used to rasterise SVGs into PNGs; override with EDGE_EXE if needed.
EDGE_EXE = os.environ.get(
    "EDGE_EXE", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
)

BRAND_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================================== #
# SVG DEFINITIONS
# =========================================================================== #

# 1. Standalone Icon (Square 1:1, Transparent)
SVG_ICON = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%" fill="none">
  <defs>
    <!-- Sky Blue to Electric Blue gradient for code bracket -->
    <linearGradient id="bracket_grad" x1="80" y1="120" x2="220" y2="400" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#41B4FF"/>
    </linearGradient>

    <!-- The Developer Journey: Electric Blue -> Indigo -> Violet -->
    <linearGradient id="path_grad" x1="160" y1="390" x2="380" y2="130" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#41B4FF"/>
      <stop offset="45%" stop-color="#60A5FA"/>
      <stop offset="80%" stop-color="#8B5CF6"/>
      <stop offset="100%" stop-color="#A855F7"/>
    </linearGradient>

    <!-- AI Destination Beacon Glow -->
    <radialGradient id="beacon_glow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#8B5CF6" stop-opacity="0.45"/>
      <stop offset="60%" stop-color="#6366F1" stop-opacity="0.2"/>
      <stop offset="100%" stop-color="#41B4FF" stop-opacity="0"/>
    </radialGradient>

    <!-- AI Core Disk Gradient -->
    <linearGradient id="beacon_disk" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#A855F7"/>
      <stop offset="50%" stop-color="#8B5CF6"/>
      <stop offset="100%" stop-color="#6366F1"/>
    </linearGradient>

    <filter id="ai_glow_filter" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="16" result="blur"/>
      <feComposite in="SourceGraphic" in2="blur" operator="over"/>
    </filter>
  </defs>

  <!-- Left Code Bracket < : The entry to code discovery (Khojo Code) -->
  <polyline points="190,122 84,256 190,390" 
            stroke="url(#bracket_grad)" 
            stroke-width="46" 
            stroke-linecap="round" 
            stroke-linejoin="round" />

  <!-- The Forward Path : Moving from unfamiliar code to deep understanding (Samjho System) -->
  <polyline points="182,390 270,270 365,142" 
            stroke="url(#path_grad)" 
            stroke-width="42" 
            stroke-linecap="round" 
            stroke-linejoin="round" />

  <!-- Right Code Bracket > : Open-source contribution target frame -->
  <polyline points="334,390 428,278 382,216" 
            stroke="#8B5CF6" 
            stroke-width="42" 
            stroke-linecap="round" 
            stroke-linejoin="round" 
            stroke-opacity="0.82" />

  <!-- Node 1: Code Discovery Anchor (Khojo Code) -->
  <circle cx="182" cy="390" r="16" fill="#0B1020" stroke="#41B4FF" stroke-width="10" />

  <!-- Node 2: System Understanding Waypoint (Samjho System) -->
  <circle cx="270" cy="270" r="19" fill="#41B4FF" stroke="#0B1020" stroke-width="6" />
  <circle cx="270" cy="270" r="6" fill="#FFFFFF" />

  <!-- Node 3: AI Contribution Beacon (Contribute with Confidence) -->
  <!-- Atmospheric Glow Aura -->
  <circle cx="365" cy="142" r="50" fill="url(#beacon_glow)" filter="url(#ai_glow_filter)" />
  <!-- Orbital Resonance Ring -->
  <circle cx="365" cy="142" r="42" stroke="#8B5CF6" stroke-width="3.5" stroke-dasharray="6 6" stroke-opacity="0.65" />
  <!-- Main Beacon Node -->
  <circle cx="365" cy="142" r="26" fill="url(#beacon_disk)" stroke="#FFFFFF" stroke-width="7" />
  <!-- Central Guidance Spark -->
  <circle cx="365" cy="142" r="10" fill="#FFFFFF" />
</svg>'''


# 2. Standalone Icon on Dark Navy Rounded Badge (App Icon / Social Avatar)
SVG_ICON_DARK_BADGE = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%" fill="none">
  <defs>
    <linearGradient id="bg_grad" x1="0" y1="0" x2="512" y2="512" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#0B1020"/>
      <stop offset="100%" stop-color="#151D2E"/>
    </linearGradient>
    <linearGradient id="badge_bracket" x1="80" y1="120" x2="220" y2="400" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#41B4FF"/>
    </linearGradient>
    <linearGradient id="badge_path" x1="160" y1="390" x2="380" y2="130" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#41B4FF"/>
      <stop offset="45%" stop-color="#60A5FA"/>
      <stop offset="80%" stop-color="#8B5CF6"/>
      <stop offset="100%" stop-color="#A855F7"/>
    </linearGradient>
    <radialGradient id="badge_glow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#8B5CF6" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="#41B4FF" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="badge_beacon_disk" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#A855F7"/>
      <stop offset="100%" stop-color="#6366F1"/>
    </linearGradient>
    <filter id="badge_glow_filter" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="16" result="blur"/>
      <feComposite in="SourceGraphic" in2="blur" operator="over"/>
    </filter>
  </defs>

  <!-- Container Base -->
  <rect width="512" height="512" rx="112" fill="url(#bg_grad)"/>
  <rect width="510" height="510" x="1" y="1" rx="111" stroke="#334155" stroke-width="2" stroke-opacity="0.35"/>

  <!-- Left Code Bracket < -->
  <polyline points="190,122 84,256 190,390" 
            stroke="url(#badge_bracket)" 
            stroke-width="46" 
            stroke-linecap="round" 
            stroke-linejoin="round" />

  <!-- The Forward Path -->
  <polyline points="182,390 270,270 365,142" 
            stroke="url(#badge_path)" 
            stroke-width="42" 
            stroke-linecap="round" 
            stroke-linejoin="round" />

  <!-- Right Code Bracket > -->
  <polyline points="334,390 428,278 382,216" 
            stroke="#8B5CF6" 
            stroke-width="42" 
            stroke-linecap="round" 
            stroke-linejoin="round" 
            stroke-opacity="0.85" />

  <!-- Node 1: Discovery Anchor -->
  <circle cx="182" cy="390" r="16" fill="#0B1020" stroke="#41B4FF" stroke-width="10" />

  <!-- Node 2: Intelligence Waypoint -->
  <circle cx="270" cy="270" r="19" fill="#41B4FF" stroke="#0B1020" stroke-width="6" />
  <circle cx="270" cy="270" r="6" fill="#FFFFFF" />

  <!-- Node 3: AI Destination Beacon -->
  <circle cx="365" cy="142" r="50" fill="url(#badge_glow)" filter="url(#badge_glow_filter)" />
  <circle cx="365" cy="142" r="42" stroke="#8B5CF6" stroke-width="3.5" stroke-dasharray="6 6" stroke-opacity="0.65" />
  <circle cx="365" cy="142" r="26" fill="url(#badge_beacon_disk)" stroke="#FFFFFF" stroke-width="7" />
  <circle cx="365" cy="142" r="10" fill="#FFFFFF" />
</svg>'''


# 3. Standalone Icon - Light Background Variant
SVG_ICON_LIGHT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%" fill="none">
  <defs>
    <linearGradient id="light_bracket" x1="80" y1="120" x2="220" y2="400" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#0284C7"/>
      <stop offset="100%" stop-color="#0369A1"/>
    </linearGradient>
    <linearGradient id="light_path" x1="160" y1="390" x2="380" y2="130" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#0284C7"/>
      <stop offset="50%" stop-color="#4F46E5"/>
      <stop offset="100%" stop-color="#7C3AED"/>
    </linearGradient>
    <linearGradient id="light_beacon_disk" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#8B5CF6"/>
      <stop offset="100%" stop-color="#6D28D9"/>
    </linearGradient>
  </defs>

  <!-- Left Code Bracket < -->
  <polyline points="190,122 84,256 190,390" 
            stroke="url(#light_bracket)" 
            stroke-width="46" 
            stroke-linecap="round" 
            stroke-linejoin="round" />

  <!-- The Forward Path -->
  <polyline points="182,390 270,270 365,142" 
            stroke="url(#light_path)" 
            stroke-width="42" 
            stroke-linecap="round" 
            stroke-linejoin="round" />

  <!-- Right Code Bracket > -->
  <polyline points="334,390 428,278 382,216" 
            stroke="#7C3AED" 
            stroke-width="42" 
            stroke-linecap="round" 
            stroke-linejoin="round" 
            stroke-opacity="0.85" />

  <!-- Node 1: Discovery Anchor -->
  <circle cx="182" cy="390" r="16" fill="#FFFFFF" stroke="#0284C7" stroke-width="10" />

  <!-- Node 2: Intelligence Waypoint -->
  <circle cx="270" cy="270" r="19" fill="#0284C7" stroke="#FFFFFF" stroke-width="6" />
  <circle cx="270" cy="270" r="6" fill="#FFFFFF" />

  <!-- Node 3: AI Destination Beacon -->
  <circle cx="365" cy="142" r="42" stroke="#7C3AED" stroke-width="3.5" stroke-dasharray="6 6" stroke-opacity="0.5" />
  <circle cx="365" cy="142" r="26" fill="url(#light_beacon_disk)" stroke="#FFFFFF" stroke-width="7" />
  <circle cx="365" cy="142" r="10" fill="#FFFFFF" />
</svg>'''


# 4. Standalone Icon - Monochrome Dark (Pure White for Dark Backgrounds)
SVG_ICON_MONO_DARK = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%" fill="none">
  <!-- Left Code Bracket < -->
  <polyline points="190,122 84,256 190,390" 
            stroke="#FFFFFF" 
            stroke-width="46" 
            stroke-linecap="round" 
            stroke-linejoin="round" />

  <!-- The Forward Path -->
  <polyline points="182,390 270,270 365,142" 
            stroke="#FFFFFF" 
            stroke-width="42" 
            stroke-linecap="round" 
            stroke-linejoin="round" />

  <!-- Right Code Bracket > -->
  <polyline points="334,390 428,278 382,216" 
            stroke="#FFFFFF" 
            stroke-width="42" 
            stroke-linecap="round" 
            stroke-linejoin="round" 
            stroke-opacity="0.8" />

  <!-- Node 1: Discovery Anchor -->
  <circle cx="182" cy="390" r="16" fill="#000000" stroke="#FFFFFF" stroke-width="10" />

  <!-- Node 2: Intelligence Waypoint -->
  <circle cx="270" cy="270" r="19" fill="#FFFFFF" stroke="#000000" stroke-width="6" />

  <!-- Node 3: AI Destination Beacon -->
  <circle cx="365" cy="142" r="42" stroke="#FFFFFF" stroke-width="3.5" stroke-dasharray="6 6" stroke-opacity="0.6" />
  <circle cx="365" cy="142" r="26" fill="#FFFFFF" stroke="#000000" stroke-width="6" />
  <circle cx="365" cy="142" r="10" fill="#000000" />
</svg>'''


# 5. Standalone Icon - Monochrome Light (Pure Black / Deep Navy for Print & Light BG)
SVG_ICON_MONO_LIGHT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%" fill="none">
  <!-- Left Code Bracket < -->
  <polyline points="190,122 84,256 190,390" 
            stroke="#0B1020" 
            stroke-width="46" 
            stroke-linecap="round" 
            stroke-linejoin="round" />

  <!-- The Forward Path -->
  <polyline points="182,390 270,270 365,142" 
            stroke="#0B1020" 
            stroke-width="42" 
            stroke-linecap="round" 
            stroke-linejoin="round" />

  <!-- Right Code Bracket > -->
  <polyline points="334,390 428,278 382,216" 
            stroke="#0B1020" 
            stroke-width="42" 
            stroke-linecap="round" 
            stroke-linejoin="round" 
            stroke-opacity="0.8" />

  <!-- Node 1: Discovery Anchor -->
  <circle cx="182" cy="390" r="16" fill="#FFFFFF" stroke="#0B1020" stroke-width="10" />

  <!-- Node 2: Intelligence Waypoint -->
  <circle cx="270" cy="270" r="19" fill="#0B1020" stroke="#FFFFFF" stroke-width="6" />

  <!-- Node 3: AI Destination Beacon -->
  <circle cx="365" cy="142" r="42" stroke="#0B1020" stroke-width="3.5" stroke-dasharray="6 6" stroke-opacity="0.6" />
  <circle cx="365" cy="142" r="26" fill="#0B1020" stroke="#FFFFFF" stroke-width="6" />
  <circle cx="365" cy="142" r="10" fill="#FFFFFF" />
</svg>'''


# 6. Primary Horizontal Logo (Icon + Wordmark, Transparent Background)
SVG_LOGO_HORIZONTAL = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 160" width="100%" height="100%" fill="none">
  <defs>
    <linearGradient id="hl_bracket" x1="20" y1="30" x2="60" y2="130" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#41B4FF"/>
    </linearGradient>
    <linearGradient id="hl_path" x1="40" y1="125" x2="110" y2="35" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#41B4FF"/>
      <stop offset="50%" stop-color="#60A5FA"/>
      <stop offset="100%" stop-color="#8B5CF6"/>
    </linearGradient>
    <radialGradient id="hl_beacon_glow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#8B5CF6" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="#41B4FF" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="hl_beacon_disk" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#A855F7"/>
      <stop offset="100%" stop-color="#6366F1"/>
    </linearGradient>
    <filter id="hl_glow_filter" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="6" result="blur"/>
      <feComposite in="SourceGraphic" in2="blur" operator="over"/>
    </filter>
  </defs>

  <!-- Left Icon Lockup (Offset: X=16, Y=14, scaled to 132x132) -->
  <g transform="translate(16, 14) scale(0.258)">
    <!-- Left Bracket -->
    <polyline points="190,122 84,256 190,390" 
              stroke="url(#hl_bracket)" 
              stroke-width="46" 
              stroke-linecap="round" 
              stroke-linejoin="round" />

    <!-- Forward Path -->
    <polyline points="182,390 270,270 365,142" 
              stroke="url(#hl_path)" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" />

    <!-- Right Bracket -->
    <polyline points="334,390 428,278 382,216" 
              stroke="#8B5CF6" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" 
              stroke-opacity="0.85" />

    <!-- Discovery Anchor -->
    <circle cx="182" cy="390" r="16" fill="#0B1020" stroke="#41B4FF" stroke-width="10" />

    <!-- Intelligence Waypoint -->
    <circle cx="270" cy="270" r="19" fill="#41B4FF" stroke="#0B1020" stroke-width="6" />
    <circle cx="270" cy="270" r="6" fill="#FFFFFF" />

    <!-- AI Destination Beacon -->
    <circle cx="365" cy="142" r="50" fill="url(#hl_beacon_glow)" filter="url(#hl_glow_filter)" />
    <circle cx="365" cy="142" r="42" stroke="#8B5CF6" stroke-width="3.5" stroke-dasharray="6 6" stroke-opacity="0.65" />
    <circle cx="365" cy="142" r="26" fill="url(#hl_beacon_disk)" stroke="#FFFFFF" stroke-width="7" />
    <circle cx="365" cy="142" r="10" fill="#FFFFFF" />
  </g>

  <!-- Wordmark "CodePath AI" -->
  <g id="wordmark">
    <!-- "CodePath" in White -->
    <text x="164" y="103" 
          font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
          font-size="64" 
          font-weight="700" 
          letter-spacing="-0.03em" 
          fill="#FFFFFF">CodePath</text>

    <!-- "AI" highlighted in Electric Blue -->
    <text x="472" y="103" 
          font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
          font-size="64" 
          font-weight="800" 
          letter-spacing="-0.02em" 
          fill="#41B4FF">AI</text>
  </g>
</svg>'''


# 7. Dark Background Horizontal Logo (with Deep Navy background container)
SVG_LOGO_DARK = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 160" width="100%" height="100%" fill="none">
  <defs>
    <linearGradient id="bg_dark_h" x1="0" y1="0" x2="680" y2="160" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#0B1020"/>
      <stop offset="100%" stop-color="#151D2E"/>
    </linearGradient>
    <linearGradient id="hl_bracket_d" x1="20" y1="30" x2="60" y2="130" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#41B4FF"/>
    </linearGradient>
    <linearGradient id="hl_path_d" x1="40" y1="125" x2="110" y2="35" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#41B4FF"/>
      <stop offset="50%" stop-color="#60A5FA"/>
      <stop offset="100%" stop-color="#8B5CF6"/>
    </linearGradient>
    <radialGradient id="hl_beacon_glow_d" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#8B5CF6" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="#41B4FF" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="hl_beacon_disk_d" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#A855F7"/>
      <stop offset="100%" stop-color="#6366F1"/>
    </linearGradient>
    <filter id="hl_glow_filter_d" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="6" result="blur"/>
      <feComposite in="SourceGraphic" in2="blur" operator="over"/>
    </filter>
  </defs>

  <!-- Background Base -->
  <rect width="680" height="160" rx="24" fill="url(#bg_dark_h)"/>
  <rect width="678" height="158" x="1" y="1" rx="23" stroke="#1E293B" stroke-width="1.5"/>

  <!-- Left Icon Lockup -->
  <g transform="translate(24, 14) scale(0.258)">
    <polyline points="190,122 84,256 190,390" 
              stroke="url(#hl_bracket_d)" 
              stroke-width="46" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="182,390 270,270 365,142" 
              stroke="url(#hl_path_d)" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="334,390 428,278 382,216" 
              stroke="#8B5CF6" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" 
              stroke-opacity="0.85" />
    <circle cx="182" cy="390" r="16" fill="#0B1020" stroke="#41B4FF" stroke-width="10" />
    <circle cx="270" cy="270" r="19" fill="#41B4FF" stroke="#0B1020" stroke-width="6" />
    <circle cx="270" cy="270" r="6" fill="#FFFFFF" />
    <circle cx="365" cy="142" r="50" fill="url(#hl_beacon_glow_d)" filter="url(#hl_glow_filter_d)" />
    <circle cx="365" cy="142" r="42" stroke="#8B5CF6" stroke-width="3.5" stroke-dasharray="6 6" stroke-opacity="0.65" />
    <circle cx="365" cy="142" r="26" fill="url(#hl_beacon_disk_d)" stroke="#FFFFFF" stroke-width="7" />
    <circle cx="365" cy="142" r="10" fill="#FFFFFF" />
  </g>

  <!-- Wordmark -->
  <text x="172" y="103" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="64" 
        font-weight="700" 
        letter-spacing="-0.03em" 
        fill="#FFFFFF">CodePath</text>
  <text x="480" y="103" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="64" 
        font-weight="800" 
        letter-spacing="-0.02em" 
        fill="#41B4FF">AI</text>
</svg>'''


# 8. Light Background Horizontal Logo
SVG_LOGO_LIGHT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 160" width="100%" height="100%" fill="none">
  <defs>
    <linearGradient id="hl_bracket_l" x1="20" y1="30" x2="60" y2="130" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#0284C7"/>
      <stop offset="100%" stop-color="#0369A1"/>
    </linearGradient>
    <linearGradient id="hl_path_l" x1="40" y1="125" x2="110" y2="35" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#0284C7"/>
      <stop offset="50%" stop-color="#4F46E5"/>
      <stop offset="100%" stop-color="#7C3AED"/>
    </linearGradient>
    <linearGradient id="hl_beacon_disk_l" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#8B5CF6"/>
      <stop offset="100%" stop-color="#6D28D9"/>
    </linearGradient>
  </defs>

  <!-- Left Icon Lockup -->
  <g transform="translate(16, 14) scale(0.258)">
    <polyline points="190,122 84,256 190,390" 
              stroke="url(#hl_bracket_l)" 
              stroke-width="46" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="182,390 270,270 365,142" 
              stroke="url(#hl_path_l)" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="334,390 428,278 382,216" 
              stroke="#7C3AED" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" 
              stroke-opacity="0.85" />
    <circle cx="182" cy="390" r="16" fill="#FFFFFF" stroke="#0284C7" stroke-width="10" />
    <circle cx="270" cy="270" r="19" fill="#0284C7" stroke="#FFFFFF" stroke-width="6" />
    <circle cx="270" cy="270" r="6" fill="#FFFFFF" />
    <circle cx="365" cy="142" r="42" stroke="#7C3AED" stroke-width="3.5" stroke-dasharray="6 6" stroke-opacity="0.5" />
    <circle cx="365" cy="142" r="26" fill="url(#hl_beacon_disk_l)" stroke="#FFFFFF" stroke-width="7" />
    <circle cx="365" cy="142" r="10" fill="#FFFFFF" />
  </g>

  <!-- Wordmark -->
  <text x="164" y="103" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="64" 
        font-weight="700" 
        letter-spacing="-0.03em" 
        fill="#0B1020">CodePath</text>
  <text x="472" y="103" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="64" 
        font-weight="800" 
        letter-spacing="-0.02em" 
        fill="#0284C7">AI</text>
</svg>'''


# 9. Full Logo with Official Tagline (For Documentation & Presentations)
SVG_LOGO_FULL = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 740 210" width="100%" height="100%" fill="none">
  <defs>
    <linearGradient id="fl_bracket" x1="20" y1="30" x2="60" y2="130" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#41B4FF"/>
    </linearGradient>
    <linearGradient id="fl_path" x1="40" y1="125" x2="110" y2="35" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#41B4FF"/>
      <stop offset="50%" stop-color="#60A5FA"/>
      <stop offset="100%" stop-color="#8B5CF6"/>
    </linearGradient>
    <radialGradient id="fl_beacon_glow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#8B5CF6" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="#41B4FF" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="fl_beacon_disk" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#A855F7"/>
      <stop offset="100%" stop-color="#6366F1"/>
    </linearGradient>
    <filter id="fl_glow_filter" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="6" result="blur"/>
      <feComposite in="SourceGraphic" in2="blur" operator="over"/>
    </filter>
  </defs>

  <!-- Left Icon Lockup -->
  <g transform="translate(18, 18) scale(0.28)">
    <polyline points="190,122 84,256 190,390" 
              stroke="url(#fl_bracket)" 
              stroke-width="46" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="182,390 270,270 365,142" 
              stroke="url(#fl_path)" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="334,390 428,278 382,216" 
              stroke="#8B5CF6" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" 
              stroke-opacity="0.85" />
    <circle cx="182" cy="390" r="16" fill="#0B1020" stroke="#41B4FF" stroke-width="10" />
    <circle cx="270" cy="270" r="19" fill="#41B4FF" stroke="#0B1020" stroke-width="6" />
    <circle cx="270" cy="270" r="6" fill="#FFFFFF" />
    <circle cx="365" cy="142" r="50" fill="url(#fl_beacon_glow)" filter="url(#fl_glow_filter)" />
    <circle cx="365" cy="142" r="42" stroke="#8B5CF6" stroke-width="3.5" stroke-dasharray="6 6" stroke-opacity="0.65" />
    <circle cx="365" cy="142" r="26" fill="url(#fl_beacon_disk)" stroke="#FFFFFF" stroke-width="7" />
    <circle cx="365" cy="142" r="10" fill="#FFFFFF" />
  </g>

  <!-- Wordmark -->
  <text x="180" y="100" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="64" 
        font-weight="700" 
        letter-spacing="-0.03em" 
        fill="#FFFFFF">CodePath</text>
  <text x="488" y="100" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="64" 
        font-weight="800" 
        letter-spacing="-0.02em" 
        fill="#41B4FF">AI</text>

  <!-- Tagline -->
  <text x="182" y="148" 
        font-family="system-ui, -apple-system, 'SF Pro Text', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="20" 
        font-weight="500" 
        letter-spacing="0.01em" 
        fill="#A7B0C0">Khojo Code. Samjho System. Contribute with Confidence.</text>
</svg>'''


# 10. Compact Logo (For narrow Navbar / Sidebar)
SVG_LOGO_COMPACT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 340 90" width="100%" height="100%" fill="none">
  <defs>
    <linearGradient id="cl_bracket" x1="10" y1="15" x2="35" y2="70" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#41B4FF"/>
    </linearGradient>
    <linearGradient id="cl_path" x1="25" y1="70" x2="65" y2="15" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#41B4FF"/>
      <stop offset="100%" stop-color="#8B5CF6"/>
    </linearGradient>
  </defs>

  <!-- Compact Icon (70x70) -->
  <g transform="translate(10, 8) scale(0.145)">
    <polyline points="190,122 84,256 190,390" 
              stroke="url(#cl_bracket)" 
              stroke-width="50" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="182,390 270,270 365,142" 
              stroke="url(#cl_path)" 
              stroke-width="46" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="334,390 428,278 382,216" 
              stroke="#8B5CF6" 
              stroke-width="46" 
              stroke-linecap="round" 
              stroke-linejoin="round" 
              stroke-opacity="0.85" />
    <circle cx="182" cy="390" r="18" fill="#0B1020" stroke="#41B4FF" stroke-width="12" />
    <circle cx="270" cy="270" r="22" fill="#41B4FF" stroke="#0B1020" stroke-width="8" />
    <circle cx="365" cy="142" r="32" fill="#8B5CF6" stroke="#FFFFFF" stroke-width="9" />
    <circle cx="365" cy="142" r="12" fill="#FFFFFF" />
  </g>

  <!-- Wordmark -->
  <text x="96" y="58" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="36" 
        font-weight="700" 
        letter-spacing="-0.03em" 
        fill="#FFFFFF">CodePath</text>
  <text x="270" y="58" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="36" 
        font-weight="800" 
        letter-spacing="-0.02em" 
        fill="#41B4FF">AI</text>
</svg>'''


# 11. Monochrome Horizontal Logo - Dark
SVG_LOGO_MONO_DARK = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 160" width="100%" height="100%" fill="none">
  <!-- Left Icon Lockup in pure white -->
  <g transform="translate(16, 14) scale(0.258)">
    <polyline points="190,122 84,256 190,390" 
              stroke="#FFFFFF" 
              stroke-width="46" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="182,390 270,270 365,142" 
              stroke="#FFFFFF" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="334,390 428,278 382,216" 
              stroke="#FFFFFF" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" 
              stroke-opacity="0.8" />
    <circle cx="182" cy="390" r="16" fill="#000000" stroke="#FFFFFF" stroke-width="10" />
    <circle cx="270" cy="270" r="19" fill="#FFFFFF" stroke="#000000" stroke-width="6" />
    <circle cx="365" cy="142" r="42" stroke="#FFFFFF" stroke-width="3.5" stroke-dasharray="6 6" stroke-opacity="0.6" />
    <circle cx="365" cy="142" r="26" fill="#FFFFFF" stroke="#000000" stroke-width="6" />
    <circle cx="365" cy="142" r="10" fill="#000000" />
  </g>

  <!-- Wordmark in pure white -->
  <text x="164" y="103" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="64" 
        font-weight="700" 
        letter-spacing="-0.03em" 
        fill="#FFFFFF">CodePath</text>
  <text x="472" y="103" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="64" 
        font-weight="800" 
        letter-spacing="-0.02em" 
        fill="#FFFFFF">AI</text>
</svg>'''


# 12. Monochrome Horizontal Logo - Light
SVG_LOGO_MONO_LIGHT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 160" width="100%" height="100%" fill="none">
  <!-- Left Icon Lockup in pure black/navy -->
  <g transform="translate(16, 14) scale(0.258)">
    <polyline points="190,122 84,256 190,390" 
              stroke="#0B1020" 
              stroke-width="46" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="182,390 270,270 365,142" 
              stroke="#0B1020" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" />
    <polyline points="334,390 428,278 382,216" 
              stroke="#0B1020" 
              stroke-width="42" 
              stroke-linecap="round" 
              stroke-linejoin="round" 
              stroke-opacity="0.8" />
    <circle cx="182" cy="390" r="16" fill="#FFFFFF" stroke="#0B1020" stroke-width="10" />
    <circle cx="270" cy="270" r="19" fill="#0B1020" stroke="#FFFFFF" stroke-width="6" />
    <circle cx="365" cy="142" r="42" stroke="#0B1020" stroke-width="3.5" stroke-dasharray="6 6" stroke-opacity="0.6" />
    <circle cx="365" cy="142" r="26" fill="#0B1020" stroke="#FFFFFF" stroke-width="6" />
    <circle cx="365" cy="142" r="10" fill="#FFFFFF" />
  </g>

  <!-- Wordmark in pure black/navy -->
  <text x="164" y="103" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="64" 
        font-weight="700" 
        letter-spacing="-0.03em" 
        fill="#0B1020">CodePath</text>
  <text x="472" y="103" 
        font-family="system-ui, -apple-system, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif" 
        font-size="64" 
        font-weight="800" 
        letter-spacing="-0.02em" 
        fill="#0B1020">AI</text>
</svg>'''


def save_svgs() -> dict[str, Path]:
    svg_map = {
        "codepath-icon.svg": SVG_ICON,
        "codepath-icon-dark.svg": SVG_ICON_DARK_BADGE,
        "codepath-icon-light.svg": SVG_ICON_LIGHT,
        "codepath-icon-mono-dark.svg": SVG_ICON_MONO_DARK,
        "codepath-icon-mono-light.svg": SVG_ICON_MONO_LIGHT,
        "codepath-logo.svg": SVG_LOGO_HORIZONTAL,
        "codepath-logo-dark.svg": SVG_LOGO_DARK,
        "codepath-logo-light.svg": SVG_LOGO_LIGHT,
        "codepath-logo-full.svg": SVG_LOGO_FULL,
        "codepath-logo-compact.svg": SVG_LOGO_COMPACT,
        "codepath-logo-mono-dark.svg": SVG_LOGO_MONO_DARK,
        "codepath-logo-mono-light.svg": SVG_LOGO_MONO_LIGHT,
    }
    paths = {}
    for filename, content in svg_map.items():
        filepath = BRAND_DIR / filename
        filepath.write_text(content.strip(), encoding="utf-8")
        paths[filename] = filepath
        print(f"Saved SVG: {filepath.relative_to(WORKSPACE_DIR)}")
    return paths


def rasterize_svg_with_edge(svg_path: Path, output_png: Path, width: int, height: int):
    """
    Renders an SVG to a transparent PNG using headless Edge at exact dimensions.
    """
    temp_html = svg_path.parent / f"_temp_render_{width}x{height}.html"
    svg_content = svg_path.read_text(encoding="utf-8")

    html_wrapper = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{
    width: {width}px;
    height: {height}px;
    overflow: hidden;
    background: transparent !important;
  }}
  svg {{
    display: block;
    width: {width}px;
    height: {height}px;
  }}
</style>
</head>
<body>
{svg_content}
</body>
</html>
"""
    temp_html.write_text(html_wrapper, encoding="utf-8")

    args = [
        "--headless",
        "--disable-gpu",
        "--default-background-color=00000000",
        "--hide-scrollbars",
        f"--window-size={width},{height}",
        f"--screenshot={output_png.resolve()}",
        str(temp_html.resolve()),
    ]

    try:
        subprocess.run([EDGE_EXE] + args, check=True, capture_output=True)
    finally:
        if temp_html.exists():
            temp_html.unlink()

    # Crop to exact dimensions if needed
    if output_png.exists():
        im = Image.open(output_png)
        if im.size != (width, height):
            im = im.crop((0, 0, width, height))
            im.save(output_png, "PNG")


def generate_raster_assets():
    print("\n--- Generating High-Res Raster PNGs ---")

    # 1. Standalone Icon Sizes
    icon_svg = BRAND_DIR / "codepath-icon.svg"
    icon_1024_png = BRAND_DIR / "codepath-icon-1024.png"
    rasterize_svg_with_edge(icon_svg, icon_1024_png, 1024, 1024)
    print("Generated: codepath-icon-1024.png")

    # Load 1024x1024 in PIL to cleanly downsample with LANCZOS for icon sizes
    base_img = Image.open(icon_1024_png)

    sizes = [
        (512, "codepath-icon-512.png"),
        (512, "codepath-icon.png"),  # Standard 512px icon
        (256, "codepath-icon-256.png"),
        (128, "codepath-icon-128.png"),
        (64, "codepath-icon-64.png"),
        (32, "codepath-icon-32.png"),
        (16, "codepath-icon-16.png"),
    ]

    for size, filename in sizes:
        downsampled = base_img.resize((size, size), Image.Resampling.LANCZOS)
        out_path = BRAND_DIR / filename
        downsampled.save(out_path, "PNG")
        print(f"Generated: {filename} ({size}x{size})")

    # Generate public favicons
    base_img.resize((32, 32), Image.Resampling.LANCZOS).save(PUBLIC_DIR / "favicon-32x32.png", "PNG")
    base_img.resize((16, 16), Image.Resampling.LANCZOS).save(PUBLIC_DIR / "favicon-16x16.png", "PNG")

    # Generate multi-size favicon.ico (16, 32, 48, 64, 128, 256)
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    ico_img = Image.open(icon_1024_png)
    ico_path = PUBLIC_DIR / "favicon.ico"
    ico_img.save(ico_path, format="ICO", sizes=ico_sizes)
    print(f"Generated multi-size favicon: {ico_path.relative_to(WORKSPACE_DIR)}")

    # Also copy icon to public root for convenient static serving
    base_img.resize((512, 512), Image.Resampling.LANCZOS).save(PUBLIC_DIR / "codepath-icon.png", "PNG")

    # 2. Horizontal Logos
    logos = [
        ("codepath-logo.svg", "codepath-logo.png", 1360, 320),
        ("codepath-logo-dark.svg", "codepath-logo-dark.png", 1360, 320),
        ("codepath-logo-light.svg", "codepath-logo-light.png", 1360, 320),
        ("codepath-logo-full.svg", "codepath-logo-full.png", 1480, 420),
        ("codepath-logo-compact.svg", "codepath-logo-compact.png", 680, 180),
    ]

    for svg_name, png_name, w, h in logos:
        svg_file = BRAND_DIR / svg_name
        png_file = BRAND_DIR / png_name
        rasterize_svg_with_edge(svg_file, png_file, w, h)
        print(f"Generated: {png_name} ({w}x{h})")


if __name__ == "__main__":
    save_svgs()
    generate_raster_assets()
    print("\nAll brand assets successfully built!")
