// The code checker's rules for the dashboard's script (job "Dashboard - style and safety").
// No packages to install: plain ESLint rules only. Every finding is fixed or explained in the project's code check log.
const browser = Object.fromEntries([
  "window", "document", "localStorage", "fetch", "setTimeout", "clearTimeout", "setInterval", "clearInterval",
  "requestAnimationFrame", "console", "Event", "URL", "navigator", "getComputedStyle", "matchMedia",
].map((name) => [name, "readonly"]));

export default [
  {
    files: ["hub/dashboard/**/*.js"],
    languageOptions: { ecmaVersion: 2023, sourceType: "script", globals: browser },
    rules: {
      // Mistakes
      "no-undef": "error",
      "no-unused-vars": ["error", { args: "none" }],
      "no-dupe-keys": "error",
      "no-unreachable": "error",
      "no-constant-condition": ["error", { checkLoops: false }],
      "no-self-assign": "error",
      "use-isnan": "error",
      "valid-typeof": "error",
      eqeqeq: ["error", "smart"],
      // Safety: the dashboard shows text from the Pis, so it never builds HTML from strings or runs strings as code.
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-script-url": "error",
      "no-restricted-properties": ["error",
        { property: "innerHTML", message: "Use textContent or el(): readings come from the Pis." },
        { property: "outerHTML", message: "Use textContent or el(): readings come from the Pis." },
        { property: "insertAdjacentHTML", message: "Use el(): readings come from the Pis." },
        { object: "document", property: "write", message: "Build elements with el()." },
      ],
    },
  },
];
