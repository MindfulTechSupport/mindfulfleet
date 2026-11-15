/* MindfulFleet color palettes. Each palette gives its own colors for light, and what changes in dark;
   status colors not listed come from `status`. Every text color is checked for contrast by
   tests/test_mindful_fleet.py (PaletteTests). */
window.MF_PALETTES = {
 "status": {"light": {"ok": "#1a7d52", "warn": "#9a6200", "bad": "#c22f3a", "off": "#63667f", "ok-body": "#8fdcb4", "warn-body": "#ffcf6e", "bad-body": "#ff9a9a", "off-body": "#c5c7d6"}, "dark": {"ok": "#7fe0ae", "warn": "#ffd072", "bad": "#ff9fa3", "off": "#bdbfd2"}},
 "palettes": {
  "lavender": {"name": "Lavender", "plus": false,
   "light": {"bg": "#e6e8f6", "ink": "#23264a", "soft": "#5a5e86", "card": "#ffffff", "track": "#e3e5f3", "neutral": "#6d74c4", "face": "#23264a"},
   "dark": {"bg": "#1d2040", "ink": "#f0f1fb", "soft": "#b0b3d6", "card": "#2a2e58", "track": "#3c4175", "neutral": "#a3a9f0"}},
  "ocean": {"name": "Ocean", "plus": false,
   "light": {"bg": "#e6ecf2", "card": "#fcfdfd", "track": "#dfe6ec", "ink": "#273744", "soft": "#54626e", "neutral": "#4b7395", "face": "#222f3a"},
   "dark": {"bg": "#1f2933", "card": "#2e3b47", "track": "#475866", "ink": "#eef3f6", "soft": "#bac5cf", "neutral": "#aac3da"}},
  "clear": {"name": "Clear (easy to read)", "plus": false,
   "light": {"bg": "#eceef2", "card": "#ffffff", "track": "#c4c9d2", "ink": "#000000", "soft": "#30353f", "neutral": "#1f4e9c", "face": "#000000", "ok": "#005a9c", "warn": "#8a4b00", "bad": "#a3004f", "off": "#474c56", "ok-body": "#9fd0f5", "warn-body": "#ffc56b", "bad-body": "#f6a8cc", "off-body": "#d0d4db"},
   "dark": {"bg": "#000000", "card": "#15171c", "track": "#4a4f59", "ink": "#ffffff", "soft": "#d6d9e0", "neutral": "#9cc3ff", "ok": "#8ccaff", "warn": "#ffbf5e", "bad": "#ff9ccc", "off": "#c8ccd4"}},
  "forest": {"name": "Forest", "plus": true,
   "light": {"bg": "#e7f1ea", "card": "#fcfdfc", "track": "#e0ebe4", "ink": "#2b4032", "soft": "#576a5e", "neutral": "#548c67", "face": "#25372b"},
   "dark": {"bg": "#213026", "card": "#314438", "track": "#4b6253", "ink": "#eff5f1", "soft": "#bcccc2", "neutral": "#b0d4bc"}},
  "sunset": {"name": "Sunset", "plus": true,
   "light": {"bg": "#f3e8e5", "card": "#fefcfb", "track": "#eee1dd", "ink": "#462d25", "soft": "#705952", "neutral": "#9b5945", "face": "#3c261f"},
   "dark": {"bg": "#34231d", "card": "#49332c", "track": "#694d44", "ink": "#f7f0ee", "soft": "#d1beb8", "neutral": "#deb3a6"}},
  "graphite": {"name": "Graphite", "plus": true,
   "light": {"bg": "#ebeced", "card": "#fcfcfd", "track": "#e4e5e7", "ink": "#333538", "soft": "#5f6163", "neutral": "#6a6f76", "face": "#2c2e30"},
   "dark": {"bg": "#27292a", "card": "#393a3d", "track": "#545659", "ink": "#f2f2f3", "soft": "#c3c4c6", "neutral": "#bec1c6"}},
  "rose": {"name": "Rose", "plus": true,
   "light": {"bg": "#f2e6eb", "card": "#fdfcfc", "track": "#ecdfe4", "ink": "#432833", "soft": "#6d555f", "neutral": "#924e6b", "face": "#39232c"},
   "dark": {"bg": "#322027", "card": "#462f39", "track": "#654854", "ink": "#f6eff2", "soft": "#cebbc3", "neutral": "#d8acbe"}},
  "sand": {"name": "Sand", "plus": true,
   "light": {"bg": "#f1eee7", "card": "#fdfdfc", "track": "#ebe7e0", "ink": "#423a2a", "soft": "#6c6456", "neutral": "#8f7a51", "face": "#383124"},
   "dark": {"bg": "#312c21", "card": "#453e30", "track": "#645b4a", "ink": "#f5f3ef", "soft": "#cdc7bc", "neutral": "#d6c8ae"}},
  "plum": {"name": "Plum", "plus": true,
   "light": {"bg": "#eee7f1", "card": "#fdfcfd", "track": "#e8e0eb", "ink": "#3a2b40", "soft": "#65576a", "neutral": "#7b548c", "face": "#322537"},
   "dark": {"bg": "#2c2130", "card": "#3e3144", "track": "#5b4b62", "ink": "#f3eff5", "soft": "#c8bccc", "neutral": "#c9b0d4"}},
  "mint": {"name": "Mint", "plus": true,
   "light": {"bg": "#e7f1ef", "card": "#fcfdfd", "track": "#e0ebe9", "ink": "#2a423d", "soft": "#566c67", "neutral": "#518f83", "face": "#243834"},
   "dark": {"bg": "#21312e", "card": "#304541", "track": "#4a645f", "ink": "#eff5f4", "soft": "#bccdca", "neutral": "#aed6ce"}},
  "citrus": {"name": "Citrus", "plus": true,
   "light": {"bg": "#eff2e6", "card": "#fdfdfc", "track": "#e9ecdf", "ink": "#3e4328", "soft": "#686d55", "neutral": "#85924e", "face": "#353923"},
   "dark": {"bg": "#2e3220", "card": "#41462f", "track": "#5f6548", "ink": "#f4f6ef", "soft": "#cacebb", "neutral": "#cfd8ac"}},
  "arctic": {"name": "Arctic", "plus": true,
   "light": {"bg": "#e9edef", "card": "#fcfdfd", "track": "#e2e7e9", "ink": "#2e393d", "soft": "#5b6467", "neutral": "#5e7983", "face": "#283134"},
   "dark": {"bg": "#242b2e", "card": "#343e41", "track": "#4f5b5f", "ink": "#f0f3f4", "soft": "#bfc7ca", "neutral": "#b6c8ce"}}
 }
};
